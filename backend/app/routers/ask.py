from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.deps import get_current_user, require_admin
from app.models.api import AskRequest, CrawlRequest
from app.services import jobs
from app.services.ask import answer_query
from app.services.discover import run_job
from app.services.llm import get_llm
from app.services.search import filter_hits, search_query_from_user, search_web

router = APIRouter(prefix="/api", tags=["ask"], dependencies=[Depends(get_current_user)])


def _require_graph() -> None:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")


@router.post("/ask")
async def ask(
    payload: AskRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> dict:
    """Mode A answer. Admins may kick off Mode B when evidence is thin; users stay on-graph."""
    _require_graph()
    try:
        result = await answer_query(payload.query, top_n=payload.top_n)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Ask failed: {exc}") from exc
    result["job_id"] = None
    result["mode"] = "retrieve"

    allow_discover = payload.discover and user.get("role") == "admin"
    if payload.discover and user.get("role") != "admin":
        result["discover_blocked"] = "Web discovery is limited to admin accounts"

    if allow_discover and result["discover_recommended"]:
        if await jobs.active_count() > 0:
            result["discover_blocked"] = "A crawl is already running"
            return result
        job = await jobs.create(payload.query)
        background_tasks.add_task(run_job, job["id"])
        result["job_id"] = job["id"]
        result["mode"] = "discover"
        result["job"] = job
    return result


@router.post("/osint/search", dependencies=[Depends(require_admin)])
async def preview_search(payload: CrawlRequest) -> dict:
    """Run search + relevance filter without fetching pages."""
    query = search_query_from_user(payload.query)
    raw, provider = await search_web(query)
    keep = payload.max_urls or settings.max_urls_per_query
    hits = filter_hits(raw, keep=keep)
    return {
        "query": query,
        "provider": provider,
        "raw": len(raw),
        "kept": len(hits),
        "hits": hits,
    }


@router.post("/osint/crawl", dependencies=[Depends(require_admin)])
async def start_crawl(payload: CrawlRequest, background_tasks: BackgroundTasks) -> dict:
    _require_graph()
    if not get_llm().configured:
        raise HTTPException(503, "LLM is not configured; crawl ingest needs it")
    if not payload.force and await jobs.active_count() > 0:
        raise HTTPException(409, "A crawl is already running")
    job = await jobs.create(payload.query)
    max_urls = payload.max_urls or None
    background_tasks.add_task(run_job, job["id"], max_urls=max_urls)
    return job


@router.get("/osint/jobs", dependencies=[Depends(require_admin)])
async def list_jobs(limit: int = Query(20, ge=1, le=50)) -> dict:
    _require_graph()
    rows = await jobs.list_jobs(limit)
    return {"count": len(rows), "jobs": rows}


@router.get("/osint/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def get_job(job_id: str) -> dict:
    _require_graph()
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job
