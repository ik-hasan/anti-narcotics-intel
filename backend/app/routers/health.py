from fastapi import APIRouter

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.db.schema import graph_stats
from app.services.llm import get_llm
from app.services import vector_store

router = APIRouter(tags=["health"])


def _llm_status() -> dict:
    try:
        llm = get_llm()
        return {"provider": llm.name, "model": llm.model, "configured": llm.configured}
    except ValueError as exc:
        return {"provider": settings.llm_provider, "configured": False, "error": str(exc)}


@router.get("/health")
async def health() -> dict:
    """Liveness probe for Render.

    Intentionally does no I/O: Render polls this and Aura Free should not be woken
    on every poll.
    """
    llm = _llm_status()
    return {
        "status": "ok",
        "service": "narcograph-api",
        "neo4j_connected": neo4j_client.is_connected,
        "llm_provider": llm["provider"],
        "llm_configured": llm["configured"],
        "voyage_configured": bool(settings.voyage_api_key),
    }


@router.get("/health/deep")
async def deep_health() -> dict:
    neo4j_ok = await neo4j_client.ping()
    stats = await graph_stats() if neo4j_ok else {}
    coverage = await vector_store.coverage() if neo4j_ok else {}
    return {
        "status": "ok" if neo4j_ok else "degraded",
        "neo4j": {"connected": neo4j_ok, "database": settings.neo4j_database},
        "llm": _llm_status(),
        "voyage": {
            "configured": bool(settings.voyage_api_key),
            "model": settings.voyage_model,
            "dimensions": settings.voyage_dim,
            "coverage": coverage,
        },
        "search": {
            "providers": settings.search_providers,
            "searxng": bool(settings.searxng_url),
            "searxng_url": settings.searxng_url or None,
            "google_cse": bool(settings.google_cse_key and settings.google_cse_id),
            "crawler": settings.crawler_backend,
            "max_urls_per_query": settings.max_urls_per_query,
        },
        "retrieval_policy": {
            "top_k": settings.top_k,
            "similarity_gate": settings.similarity_gate,
            "min_high_conf_chunks": settings.min_high_conf_chunks,
            "final_top_n": settings.final_top_n,
            "graph_max_hops": settings.graph_max_hops,
        },
        "graph": stats,
    }
