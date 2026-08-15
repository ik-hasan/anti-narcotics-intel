"""Mode B: search → filter → crawl → ingest → embed, then the caller re-runs Mode A.

Only one job runs at a time on a free instance. Each step updates the CrawlJob node
so the frontend can poll without holding an HTTP connection open for two minutes.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.services import jobs, vector_store
from app.services.crawler import crawl_urls
from app.services.ingest import ingest_text
from app.services.search import filter_hits, search_query_from_user, search_web

logger = logging.getLogger(__name__)


async def run_job(job_id: str, *, max_urls: int | None = None) -> dict:
    job = await jobs.get(job_id)
    if job is None:
        raise KeyError(job_id)

    keep = max_urls or settings.max_urls_per_query
    query = search_query_from_user(job["query"])

    try:
        await jobs.update(job_id, status="running", stage="search")
        raw, provider = await search_web(query)
        hits = filter_hits(raw, keep=keep)
        await jobs.update(
            job_id,
            provider=provider,
            urls_found=len(hits),
            stage="crawl",
            hits=[
                {
                    "url": h["url"],
                    "title": h["title"],
                    "rank": h["rank"],
                    "host": h["host"],
                }
                for h in hits
            ],
        )
        if not hits:
            return await jobs.update(
                job_id,
                status="done",
                stage="empty",
                error="Search returned no relevant URLs after filtering",
            )

        pages = await crawl_urls([h["url"] for h in hits])
        crawled = sum(1 for p in pages if p.text and not p.skipped and not p.error)
        await jobs.update(job_id, urls_crawled=crawled, stage="ingest")

        ingested = 0
        skipped = 0
        for page in pages:
            if page.skipped or page.error or len(page.text) < 200:
                skipped += 1
                continue
            try:
                result = await ingest_text(
                    text=page.text,
                    url=page.url,
                    title=page.title,
                    published_at=page.published_at,
                    source=f"osint:{provider}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ingest failed for %s: %s", page.url, exc)
                skipped += 1
                continue
            if result.status == "ingested":
                ingested += 1
            else:
                skipped += 1

        await jobs.update(
            job_id, urls_ingested=ingested, urls_skipped=skipped, stage="embed"
        )
        try:
            await vector_store.backfill(limit=max(ingested * 4, 32))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Post-crawl embed skipped: %s", exc)

        return await jobs.update(job_id, status="done", stage="done")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Crawl job %s failed", job_id)
        return await jobs.update(job_id, status="failed", error=str(exc)[:400])
