import logging

from fastapi import APIRouter, Depends, HTTPException

from app.db.neo4j_client import neo4j_client
from app.deps import require_admin
from app.models.api import EmbedRequest
from app.models.extraction import IngestResponse, IngestTextRequest
from app.services import lexicon, vector_store
from app.services.ingest import ingest_text
from app.services.llm import get_llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"], dependencies=[Depends(require_admin)])


@router.post("/text", response_model=IngestResponse)
async def ingest_text_endpoint(payload: IngestTextRequest) -> IngestResponse:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected; check NEO4J_URI and NEO4J_PASSWORD")
    llm = get_llm()
    if not llm.configured:
        raise HTTPException(503, f"No API key configured for LLM_PROVIDER={llm.name}")

    try:
        return await ingest_text(
            text=payload.text,
            url=payload.url,
            title=payload.title,
            published_at=payload.published_at,
            source=payload.source,
            force=payload.force,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ingest failed")
        raise HTTPException(500, f"Ingest failed: {exc}") from exc


@router.post("/analyze")
async def analyze_only(payload: IngestTextRequest) -> dict:
    """Run the lexicon pre-pass without touching Gemini or the graph.

    Useful for tuning the relevance threshold and for verifying a deploy before
    any API keys are wired up.
    """
    return {
        "signals": lexicon.summarize_signals(payload.text),
        "length": len(payload.text),
    }


@router.post("/embed")
async def embed_pending(payload: EmbedRequest = EmbedRequest()) -> dict:
    """Backfill Voyage vectors for chunks that were ingested without embeddings."""
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")
    limit = payload.limit
    coverage = await vector_store.coverage()
    result = await vector_store.backfill(limit=limit)
    return {"coverage_before": coverage, "backfill": result, "coverage_after": await vector_store.coverage()}


@router.get("/embed/coverage")
async def embed_coverage() -> dict:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")
    return await vector_store.coverage()
