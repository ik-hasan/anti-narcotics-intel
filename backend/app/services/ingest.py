"""The Phase 1 ingest pipeline, shared by the HTTP router and the seed script.

    text -> lexicon gate -> Gemini extraction -> chunk -> graph upsert

Chunks are written with embedded=false. Phase 2 backfills their vectors through
Voyage in batches, which keeps ingest latency independent of embedding rate limits.
"""

import logging
from datetime import datetime, timezone

from app.db.neo4j_client import neo4j_client
from app.models.extraction import IngestResponse
from app.services import lexicon, vector_store
from app.services.chunker import build_chunks, clean_text, content_hash
from app.services.embeddings import embedder
from app.services.graph_upsert import upsert_article
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

MIN_RELEVANCE = 0.15


def _synthetic_url(text: str, source: str) -> str:
    return f"internal://{source}/{content_hash(text)}"


async def ingest_text(
    *,
    text: str,
    url: str = "",
    title: str = "",
    published_at: str = "",
    source: str = "manual",
    force: bool = False,
) -> IngestResponse:
    cleaned = clean_text(text)
    resolved_url = url.strip() or _synthetic_url(cleaned, source)
    digest = content_hash(cleaned)

    existing = await neo4j_client.run(
        "MATCH (a:Article {url: $url}) RETURN a.content_hash AS h",
        url=resolved_url,
    )
    if existing and existing[0].get("h") == digest and not force:
        return IngestResponse(
            status="skipped",
            article_url=resolved_url,
            chunks=0,
            relevance=1.0,
            reason="URL already ingested with the same content hash.",
        )

    signals = lexicon.summarize_signals(cleaned)
    relevance = float(signals["relevance"])

    if relevance < MIN_RELEVANCE and not force:
        return IngestResponse(
            status="skipped",
            article_url=resolved_url,
            chunks=0,
            relevance=relevance,
            reason=(
                f"Lexicon relevance {relevance} is below {MIN_RELEVANCE}; "
                "no narcotics signal found. Pass force=true to ingest anyway."
            ),
        )

    llm = get_llm()
    extraction = await llm.extract_article(cleaned, title=title)

    if not extraction.is_narcotics_related and not force:
        return IngestResponse(
            status="skipped",
            article_url=resolved_url,
            chunks=0,
            relevance=relevance,
            extraction=extraction,
            reason=f"{llm.name} classified this text as not narcotics-related.",
        )

    chunks = build_chunks(resolved_url, cleaned)
    if not chunks:
        return IngestResponse(
            status="skipped",
            article_url=resolved_url,
            chunks=0,
            relevance=relevance,
            extraction=extraction,
            reason="Text produced no usable chunks after cleaning.",
        )

    counts = await upsert_article(
        url=resolved_url,
        title=title or extraction.title,
        text=cleaned,
        source=source,
        published_at=published_at or datetime.now(timezone.utc).date().isoformat(),
        chunks=chunks,
        extraction=extraction,
        signals=signals,
    )

    logger.info(
        "Ingested %s: %d chunks, %d persons, %d drugs, %d locations",
        resolved_url,
        counts.get("chunks", 0),
        counts.get("persons", 0),
        counts.get("drugs", 0),
        counts.get("locations", 0),
    )

    embedded = 0
    if embedder.configured:
        try:
            result = await vector_store.embed_article(resolved_url)
            embedded = int(result.get("embedded") or 0)
            counts["embedded"] = embedded
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding after ingest failed: %s", exc)

    return IngestResponse(
        status="ingested",
        article_url=resolved_url,
        chunks=len(chunks),
        relevance=relevance,
        extraction=extraction,
        graph=counts,
    )
