"""Search-layer fallbacks: SearXNG → DuckDuckGo HTML → Google CSE.

Each provider returns the same {title, url, snippet} shape. Unconfigured providers
are skipped, so a laptop with no keys still has DuckDuckGo.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.config import settings
from app.services import lexicon

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=3.0)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

SOCIAL_HOSTS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "reddit.com",
    "pinterest.com",
    "linkedin.com",
    "t.me",
    "whatsapp.com",
}

# Higher score = prefer to crawl. Gov/press first, then national news.
TRUSTED_SUFFIXES: dict[str, float] = {
    "ncb.gov.in": 1.0,
    "pib.gov.in": 1.0,
    "mha.gov.in": 0.95,
    "cbic.gov.in": 0.95,
    "indiancgov.in": 0.9,
    "nic.in": 0.75,
    "gov.in": 0.8,
    "thehindu.com": 0.85,
    "indianexpress.com": 0.85,
    "hindustantimes.com": 0.8,
    "ndtv.com": 0.8,
    "indiatoday.in": 0.78,
    "timesofindia.indiatimes.com": 0.75,
    "deccanherald.com": 0.75,
    "tribuneindia.com": 0.75,
    "theprint.in": 0.75,
    "scroll.in": 0.7,
    "thewire.in": 0.7,
    "reuters.com": 0.85,
    "bbc.com": 0.8,
    "bbc.co.uk": 0.8,
    "aljazeera.com": 0.75,
    "theguardian.com": 0.75,
    "apnews.com": 0.8,
}

_HREF = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_LITE_HREF = re.compile(
    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>'
    r'|<td[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    return unescape(_TAGS.sub(" ", value or "")).replace("\xa0", " ").strip()


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_trust(url: str) -> float:
    host = _host(url)
    if not host or host in SOCIAL_HOSTS:
        return 0.0
    for suffix, score in TRUSTED_SUFFIXES.items():
        if host == suffix or host.endswith("." + suffix):
            return score
    # Unknown news-shaped domains get a weak prior so they can still pass on keywords.
    if host.endswith(".in") or host.endswith(".com"):
        return 0.25
    return 0.1


def _unwrap_ddg(href: str) -> str:
    href = unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def _is_http_page(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in (".pdf", ".jpg", ".png", ".zip", ".mp4", ".mp3")):
        return False
    return True


async def _searxng(query: str, limit: int) -> list[dict]:
    base = (settings.searxng_url or "").rstrip("/")
    if not base:
        return []
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(
            f"{base}/search",
            params={"q": query, "format": "json", "language": "en", "safesearch": 0},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    out = []
    for item in results[:limit]:
        url = item.get("url") or ""
        if url:
            out.append(
                {
                    "title": item.get("title") or "",
                    "url": url,
                    "snippet": item.get("content") or "",
                    "provider": "searxng",
                }
            )
    return out


def _parse_ddg(html: str, limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for pattern in (_HREF, _LITE_HREF):
        for match in pattern.finditer(html):
            url = _unwrap_ddg(match.group(1))
            title = _strip_html(match.group(2))
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"title": title, "url": url, "snippet": "", "provider": "duckduckgo"})
            if len(out) >= limit:
                return out
    snippets = [_strip_html(a or b or "") for a, b in _SNIPPET.findall(html)]
    for item, snippet in zip(out, snippets):
        item["snippet"] = snippet
    return out


async def _duckduckgo(query: str, limit: int) -> list[dict]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    attempts = (
        ("GET", "https://html.duckduckgo.com/html/", {"params": {"q": query}}),
        ("POST", "https://html.duckduckgo.com/html/", {"data": {"q": query}}),
        ("GET", "https://lite.duckduckgo.com/lite/", {"params": {"q": query}}),
    )
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers, follow_redirects=True) as client:
        for method, url, kwargs in attempts:
            response = await client.request(method, url, **kwargs)
            # 202 is DDG's bot-challenge page; raise_for_status treats it as success.
            if response.status_code != 200:
                logger.warning("DuckDuckGo %s %s -> %s", method, url, response.status_code)
                continue
            hits = _parse_ddg(response.text, limit)
            if hits:
                return hits
    return []


async def _google_cse(query: str, limit: int) -> list[dict]:
    if not (settings.google_cse_key and settings.google_cse_id):
        return []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": settings.google_cse_key,
                "cx": settings.google_cse_id,
                "q": query,
                "num": min(limit, 10),
            },
        )
        response.raise_for_status()
        items = response.json().get("items") or []
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": item.get("snippet") or "",
            "provider": "google_cse",
        }
        for item in items
        if item.get("link")
    ]


PROVIDERS = {
    "searxng": _searxng,
    "duckduckgo": _duckduckgo,
    "google_cse": _google_cse,
}


def rank_hit(hit: dict) -> float:
    blob = f"{hit.get('title', '')} {hit.get('snippet', '')}"
    relevance = lexicon.relevance_score(blob)
    trust = domain_trust(hit.get("url") or "")
    # Trust can carry a clearly official page even when the snippet is thin.
    return round(0.55 * relevance + 0.45 * trust, 4)


def filter_hits(hits: list[dict], *, keep: int) -> list[dict]:
    scored: list[dict] = []
    seen_hosts: dict[str, int] = {}
    for hit in hits:
        url = hit.get("url") or ""
        if not _is_http_page(url):
            continue
        host = _host(url)
        if host in SOCIAL_HOSTS:
            continue
        # Cap pages per host so one outlet cannot fill the crawl budget.
        if seen_hosts.get(host, 0) >= 2:
            continue
        score = rank_hit(hit)
        if score < 0.18:
            continue
        seen_hosts[host] = seen_hosts.get(host, 0) + 1
        scored.append({**hit, "host": host, "rank": score, "trust": domain_trust(url)})
    scored.sort(key=lambda h: h["rank"], reverse=True)
    return scored[:keep]


def search_query_from_user(query: str) -> str:
    """Bias a natural-language question toward public enforcement reporting."""
    text = query.strip()
    if not lexicon.find_drugs(text) and "ndps" not in text.lower():
        text = f"{text} narcotics OR NDPS OR seizure"
    return text[:240]


async def search_web(query: str, *, limit: int | None = None) -> tuple[list[dict], str]:
    """Try providers in configured order; return (raw hits, provider used)."""
    limit = limit or max(settings.max_urls_per_query * 2, 20)
    last_error = ""
    for name in settings.search_providers:
        fn = PROVIDERS.get(name)
        if fn is None:
            continue
        if name == "searxng" and not settings.searxng_url:
            continue
        if name == "google_cse" and not (settings.google_cse_key and settings.google_cse_id):
            continue
        try:
            hits = await fn(query, limit)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{name}: {exc}"
            logger.warning("Search provider %s failed: %s", name, exc)
            continue
        if hits:
            return hits, name
    if last_error:
        raise RuntimeError(f"All search providers failed ({last_error})")
    return [], "none"
