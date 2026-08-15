"""Groq (Llama) implementation of the extraction interface.

Groq serves Llama over an API, so nothing is downloaded or held in RAM on Render --
the same deployment profile as any other hosted LLM.
"""

import asyncio
import logging
import re
from typing import Any

from groq import AsyncGroq

from app.config import settings
from app.models.extraction import ArticleExtraction
from app.services.prompts import COMPACT_SCHEMA_HINT, EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "timeout", "overloaded", "rate limit")

# Groq reports the exact wait in the 429 body, e.g. "Please try again in 15.49s".
_RETRY_AFTER = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)

# Reserved output tokens count against the per-minute budget even when unused, so
# this is sized to the largest realistic extraction rather than left generous.
MAX_OUTPUT_TOKENS = 1400


class TransientLLMError(RuntimeError):
    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a pydantic JSON schema acceptable to strict structured-output validators.

    Strict mode requires every object to forbid extra keys and to list all of its
    properties as required. Pydantic omits both, since it treats defaults as optional.
    """
    if not isinstance(schema, dict):
        return schema

    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())

    for key in ("properties", "$defs", "definitions"):
        for value in schema.get(key, {}).values():
            _strictify(value)

    for key in ("items", "additionalItems"):
        if key in schema:
            _strictify(schema[key])

    for key in ("anyOf", "oneOf", "allOf"):
        for value in schema.get(key, []):
            _strictify(value)

    return schema


def extraction_schema() -> dict[str, Any]:
    return _strictify(ArticleExtraction.model_json_schema())


class GroqService:
    name = "groq"

    def __init__(self) -> None:
        self._client: AsyncGroq | None = None
        # Set once a json_schema request is rejected, so we stop retrying it per call.
        self._schema_mode_supported = True

    @property
    def configured(self) -> bool:
        return bool(settings.groq_api_key)

    @property
    def model(self) -> str:
        return settings.groq_model

    @property
    def client(self) -> AsyncGroq:
        if self._client is None:
            if not self.configured:
                raise RuntimeError("GROQ_API_KEY is not set")
            self._client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0)
        return self._client

    def _response_format(self) -> dict[str, Any]:
        if self._schema_mode_supported:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "article_extraction",
                    "strict": True,
                    "schema": extraction_schema(),
                },
            }
        return {"type": "json_object"}

    def _user_prompt(self, text: str, title: str) -> str:
        prompt = f"TITLE: {title}\n\nARTICLE:\n{text[:20000]}"
        if not self._schema_mode_supported:
            prompt += "\n\n" + COMPACT_SCHEMA_HINT
        return prompt

    async def extract_article(self, text: str, title: str = "") -> ArticleExtraction:
        """Extract entities, retrying on rate limits using the wait the API asks for."""
        last_error: TransientLLMError | None = None

        for attempt in range(4):
            try:
                return await self._extract_once(text, title)
            except TransientLLMError as exc:
                last_error = exc
                if attempt == 3:
                    break
                # Groq's own hint beats a guessed backoff; it knows when the window
                # reopens. The margin covers clock skew between client and server.
                delay = exc.retry_after + 1.0 if exc.retry_after else 2.0 * (attempt + 1)
                logger.warning("Rate limited by %s; waiting %.1fs", self.model, delay)
                await asyncio.sleep(delay)

        raise last_error if last_error else RuntimeError("extraction failed")

    async def _extract_once(self, text: str, title: str) -> ArticleExtraction:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": self._user_prompt(text, title)},
                ],
                response_format=self._response_format(),
                temperature=0.1,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if self._schema_mode_supported and (
                "json_schema" in message or "response_format" in message
            ):
                logger.warning(
                    "%s rejected json_schema mode; falling back to json_object", self.model
                )
                self._schema_mode_supported = False
                return await self._extract_once(text, title)
            if any(marker in message for marker in _TRANSIENT_MARKERS):
                match = _RETRY_AFTER.search(str(exc))
                raise TransientLLMError(
                    str(exc), retry_after=float(match.group(1)) if match else 0.0
                ) from exc
            raise

        content = response.choices[0].message.content or ""
        try:
            return ArticleExtraction.model_validate_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not parse extraction from %s: %s", self.model, exc)
            logger.debug("Raw content: %s", content[:500])
            return ArticleExtraction(is_narcotics_related=False, title=title)

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 900, temperature: float = 0.2
    ) -> str:
        """Free-form completion, used for answer composition rather than extraction."""
        last_error: TransientLLMError | None = None

        for attempt in range(4):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_completion_tokens=max_tokens,
                )
                return (response.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if not any(marker in message for marker in _TRANSIENT_MARKERS):
                    raise
                match = _RETRY_AFTER.search(str(exc))
                last_error = TransientLLMError(
                    str(exc), retry_after=float(match.group(1)) if match else 0.0
                )
                if attempt == 3:
                    break
                await asyncio.sleep(last_error.retry_after + 1.0 or 2.0 * (attempt + 1))

        raise last_error if last_error else RuntimeError("completion failed")

    async def health(self) -> bool:
        if not self.configured:
            return False
        try:
            await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with: ok"}],
                max_completion_tokens=16,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Groq health check failed: %s", exc)
            return False
