"""Minimal Scrapy spider used only as a subprocess.

Never import this module from the FastAPI process: Twisted and Scrapy together
would sit in RSS for the life of the worker. The parent writes a URL list and
reads the JSON this spider emits.
"""

from __future__ import annotations

from pathlib import Path

import scrapy
import trafilatura
from trafilatura.settings import use_config

_traf_config = use_config()
_traf_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "8")


class NewsSpider(scrapy.Spider):
    name = "narcograph_news"
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS": 2,
        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_TIMEOUT": 15,
        "MEMUSAGE_ENABLED": True,
        "MEMUSAGE_LIMIT_MB": 180,
        "MEMUSAGE_WARNING_MB": 140,
        "USER_AGENT": "NarcoGraphIntel/0.2 (+research; respects robots.txt)",
        "LOG_LEVEL": "WARNING",
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": False,
        "RETRY_TIMES": 1,
    }

    def __init__(self, url_file: str = "", **kwargs):
        super().__init__(**kwargs)
        self.start_urls = [
            line.strip()
            for line in Path(url_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def parse(self, response: scrapy.http.Response):
        html = response.text
        if len(html) > 1_500_000:
            html = html[:1_500_000]
        text = trafilatura.extract(
            html,
            url=response.url,
            include_comments=False,
            include_tables=False,
            config=_traf_config,
        ) or ""
        meta = trafilatura.extract_metadata(html, default_url=response.url)
        yield {
            "url": response.url,
            "title": (meta.title if meta else "") or "",
            "text": text.strip(),
            "published_at": (str(meta.date)[:10] if meta and meta.date else ""),
            "status": response.status,
        }
