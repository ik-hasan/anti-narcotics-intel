"""Crawl-job state stored in Neo4j.

Render Free has no Redis and no persistent disk, so the graph is the job registry.
A job node is tiny and is keyed by id, which is all the frontend needs to poll.
"""

from __future__ import annotations

import json
import uuid

from app.db.neo4j_client import neo4j_client

UPSERT = """
MERGE (j:CrawlJob {id: $id})
ON CREATE SET j.created_at = datetime()
SET j.query = $job_query,
    j.status = $status,
    j.stage = $stage,
    j.error = $error,
    j.provider = $provider,
    j.urls_found = $urls_found,
    j.urls_crawled = $urls_crawled,
    j.urls_ingested = $urls_ingested,
    j.urls_skipped = $urls_skipped,
    j.updated_at = datetime(),
    j.hits_json = $hits_json
RETURN j.id AS id
"""

GET = """
MATCH (j:CrawlJob {id: $id})
RETURN j.id AS id, j.query AS query, j.status AS status, j.stage AS stage,
       j.error AS error, j.provider AS provider,
       j.urls_found AS urls_found, j.urls_crawled AS urls_crawled,
       j.urls_ingested AS urls_ingested, j.urls_skipped AS urls_skipped,
       toString(j.created_at) AS created_at,
       toString(j.updated_at) AS updated_at,
       j.hits_json AS hits_json
"""

LIST = """
MATCH (j:CrawlJob)
RETURN j.id AS id, j.query AS query, j.status AS status, j.stage AS stage,
       j.urls_found AS urls_found, j.urls_ingested AS urls_ingested,
       toString(j.created_at) AS created_at
ORDER BY j.created_at DESC
LIMIT $limit
"""

ACTIVE = """
MATCH (j:CrawlJob)
WHERE j.status IN ['queued', 'running']
RETURN count(j) AS n
"""


def _hits_json(hits: object) -> str:
    if isinstance(hits, str):
        return hits
    return json.dumps(hits or [])


def _parse_hits(raw: object) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _as_job(row: dict) -> dict:
    row["hits"] = _parse_hits(row.pop("hits_json", None))
    return row


def new_id() -> str:
    return uuid.uuid4().hex[:16]


async def create(query: str) -> dict:
    job_id = new_id()
    await neo4j_client.run_write(
        UPSERT,
        id=job_id,
        job_query=query,
        status="queued",
        stage="queued",
        error="",
        provider="",
        urls_found=0,
        urls_crawled=0,
        urls_ingested=0,
        urls_skipped=0,
        hits_json="[]",
    )
    return await get(job_id)


async def update(job_id: str, **fields: object) -> dict:
    current = await get(job_id)
    if current is None:
        raise KeyError(job_id)
    payload = {
        "id": job_id,
        "job_query": current["query"],
        "status": current["status"],
        "stage": current["stage"] or "",
        "error": current.get("error") or "",
        "provider": current.get("provider") or "",
        "urls_found": current.get("urls_found") or 0,
        "urls_crawled": current.get("urls_crawled") or 0,
        "urls_ingested": current.get("urls_ingested") or 0,
        "urls_skipped": current.get("urls_skipped") or 0,
        "hits_json": _hits_json(current.get("hits") or []),
    }
    payload.update(fields)
    if "query" in payload:
        payload["job_query"] = payload.pop("query")
    if "hits" in payload:
        payload["hits_json"] = _hits_json(payload.pop("hits"))
    await neo4j_client.run_write(UPSERT, **payload)
    return await get(job_id)


async def get(job_id: str) -> dict | None:
    rows = await neo4j_client.run(GET, id=job_id)
    return _as_job(rows[0]) if rows else None


async def list_jobs(limit: int = 20) -> list[dict]:
    return await neo4j_client.run(LIST, limit=limit)


async def active_count() -> int:
    rows = await neo4j_client.run(ACTIVE)
    return int(rows[0]["n"]) if rows else 0
