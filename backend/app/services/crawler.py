"""Fetch a small set of pages and extract main text.

Default path is httpx + trafilatura so the API process never loads Scrapy/Twisted.
When CRAWLER_BACKEND=scrapy, a subprocess runs the spider and the parent reads
the JSON it writes — memory is released when the child exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from trafilatura.settings import use_config

from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENT = "NarcoGraphIntel/0.2 (+research; respects robots.txt)"
TIMEOUT = httpx.Timeout(18.0, connect=8.0)
MAX_BYTES = 1_500_000
SPIDER_PATH = Path(__file__).resolve().parents[2] / "crawler" / "news_spider.py"

_traf_config = use_config()
_traf_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "8")


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""
    published_at: str = ""
    status: int = 0
    error: str = ""
    skipped: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "published_at": self.published_at,
            "status": self.status,
            "error": self.error,
            "skipped": self.skipped,
            "chars": len(self.text),
        }


_robots_cache: dict[str, RobotFileParser | None] = {}


async def _robots(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _robots_cache:
        return _robots_cache[origin]

    def _load() -> RobotFileParser | None:
        rp = RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
            return rp
        except Exception:  # noqa: BLE001
            return None

    parser = await asyncio.to_thread(_load)
    _robots_cache[origin] = parser
    return parser


async def allowed_by_robots(url: str) -> bool:
    parser = await _robots(url)
    if parser is None:
        return True
    try:
        return parser.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def extract_article(html: str, url: str) -> tuple[str, str, str]:
    """Return (title, text, published_at) from raw HTML."""
    downloaded = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_recall=False,
        config=_traf_config,
    )
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta else "") or ""
    published = ""
    if meta and meta.date:
        published = str(meta.date)[:10]
    text = (downloaded or "").strip()
    return title, text, published


async def fetch_one(client: httpx.AsyncClient, url: str) -> Page:
    page = Page(url=url)
    if not await allowed_by_robots(url):
        page.skipped = "robots.txt"
        return page
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        page.error = str(exc)[:200]
        return page

    page.status = response.status_code
    if response.status_code >= 400:
        page.error = f"HTTP {response.status_code}"
        return page
    content_type = (response.headers.get("content-type") or "").lower()
    if "html" not in content_type and "text/" not in content_type:
        page.skipped = f"content-type {content_type[:40]}"
        return page
    html = response.text
    if len(html) > MAX_BYTES:
        html = html[:MAX_BYTES]
    title, text, published = await asyncio.to_thread(extract_article, html, url)
    page.title = title
    page.text = text
    page.published_at = published
    if len(text) < 200:
        page.skipped = "too little main text"
    return page


async def fetch_httpx(urls: list[str], *, pause: float = 1.2) -> list[Page]:
    pages: list[Page] = []
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        for index, url in enumerate(urls):
            pages.append(await fetch_one(client, url))
            if index < len(urls) - 1:
                await asyncio.sleep(pause)
    return pages


async def fetch_scrapy(urls: list[str], budget: int) -> list[Page]:
    if not SPIDER_PATH.exists():
        raise RuntimeError(f"Scrapy spider missing at {SPIDER_PATH}")
    with tempfile.TemporaryDirectory(prefix="narcograph-crawl-") as tmp:
        url_file = Path(tmp) / "urls.txt"
        out_file = Path(tmp) / "pages.json"
        url_file.write_text("\n".join(urls), encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scrapy",
            "runspider",
            str(SPIDER_PATH),
            "-a",
            f"url_file={url_file}",
            "-O",
            str(out_file),
            "-s",
            "LOG_LEVEL=WARNING",
            cwd=str(SPIDER_PATH.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=budget)
        except TimeoutError:
            proc.kill()
            raise RuntimeError("Scrapy subprocess exceeded the time budget") from None
        if proc.returncode not in (0, None) and not out_file.exists():
            raise RuntimeError(f"Scrapy failed: {(stderr or b'').decode('utf-8', 'ignore')[:400]}")
        if not out_file.exists():
            return []
        raw = json.loads(out_file.read_text(encoding="utf-8") or "[]")
        pages = []
        for item in raw:
            page = Page(
                url=item.get("url") or "",
                title=item.get("title") or "",
                text=item.get("text") or "",
                published_at=item.get("published_at") or "",
                status=int(item.get("status") or 200),
            )
            if len(page.text) < 200:
                page.skipped = "too little main text"
            pages.append(page)
        return pages


async def crawl_urls(urls: list[str]) -> list[Page]:
    backend = (settings.crawler_backend or "httpx").strip().lower()
    budget = settings.crawl_time_budget_seconds
    if backend == "scrapy":
        try:
            return await fetch_scrapy(urls, budget)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scrapy crawl failed (%s); falling back to httpx", exc)
    return await fetch_httpx(urls)
