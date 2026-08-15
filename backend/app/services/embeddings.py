"""Voyage AI embeddings over plain HTTP.

The official SDK is not used on purpose: it pulls in extra dependencies for what
is a single POST, and every megabyte of resident memory matters on a 512 MB
instance. httpx is already a dependency for the crawler.

Query and document embeddings are asymmetric in the voyage-4 series, so the
input_type must differ between ingest and search or recall degrades noticeably.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage accepts up to 1000 texts per call, but a smaller batch keeps the JSON
# payload (and the response, which is batch x 1024 floats) from spiking memory.
BATCH_SIZE = 64
TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class EmbeddingError(RuntimeError):
    pass


class VoyageEmbedder:
    name = "voyage"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(settings.voyage_api_key)

    @property
    def model(self) -> str:
        return settings.voyage_model

    @property
    def dimensions(self) -> int:
        return settings.voyage_dim

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=TIMEOUT,
                headers={
                    "Authorization": f"Bearer {settings.voyage_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _post(self, texts: list[str], input_type: str) -> list[list[float]]:
        client = await self._get_client()
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
        }

        for attempt in range(4):
            try:
                response = await client.post(VOYAGE_URL, json=payload)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise EmbeddingError(f"Voyage request failed: {exc}") from exc
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 3:
                    raise EmbeddingError(
                        f"Voyage returned {response.status_code}: {response.text[:200]}"
                    )
                # Voyage sets Retry-After on throttle; fall back to linear backoff.
                delay = float(response.headers.get("retry-after", 0) or 0) or 3 * (attempt + 1)
                logger.warning("Voyage %s, retrying in %.1fs", response.status_code, delay)
                await asyncio.sleep(delay)
                continue

            if response.status_code != 200:
                raise EmbeddingError(
                    f"Voyage returned {response.status_code}: {response.text[:300]}"
                )

            data = response.json().get("data", [])
            # The API preserves input order, but sort by index rather than trust it:
            # a silent misalignment would attach every vector to the wrong chunk.
            ordered = sorted(data, key=lambda d: d.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
            if len(vectors) != len(texts):
                raise EmbeddingError(
                    f"Voyage returned {len(vectors)} vectors for {len(texts)} inputs"
                )
            return vectors

        raise EmbeddingError("Voyage retries exhausted")

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk text for storage, batching to bound peak memory."""
        if not self.configured:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            vectors.extend(await self._post(batch, "document"))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        if not self.configured:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        vectors = await self._post([text], "query")
        return vectors[0]

    async def health(self) -> bool:
        if not self.configured:
            return False
        try:
            await self.embed_query("health check")
            return True
        except Exception:  # noqa: BLE001
            return False


embedder = VoyageEmbedder()
