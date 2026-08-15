"""Neo4j-side vector operations: backfill, similarity search, keyword fallback.

Embedding happens out of band rather than during ingest. Ingest writes chunks with
embedded=false and returns immediately, so a slow or throttled Voyage call can
never block an HTTP request or a crawl.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.services.embeddings import EmbeddingError, embedder

logger = logging.getLogger(__name__)

PENDING_CHUNKS = """
MATCH (c:Chunk)
WHERE c.embedded = false OR c.embedded IS NULL
RETURN c.id AS id, c.text AS text
ORDER BY c.id
LIMIT $limit
"""

# setNodeVectorProperty stores a compact float32 array; a plain SET keeps the list
# boxed and costs noticeably more heap on a free-tier instance.
STORE_VECTORS = """
UNWIND $rows AS row
MATCH (c:Chunk {id: row.id})
CALL db.create.setNodeVectorProperty(c, 'embedding', row.vector)
SET c.embedded = true, c.embedded_at = datetime()
RETURN count(c) AS stored
"""

STORE_VECTORS_FALLBACK = """
UNWIND $rows AS row
MATCH (c:Chunk {id: row.id})
SET c.embedding = row.vector, c.embedded = true, c.embedded_at = datetime()
RETURN count(c) AS stored
"""

VECTOR_SEARCH = """
CALL db.index.vector.queryNodes('chunk_embedding', $k, $vector)
YIELD node AS c, score
MATCH (a:Article)-[:HAS_CHUNK]->(c)
OPTIONAL MATCH (a)-[:REPORTS]->(k:Case)
RETURN c.id AS chunk_id, c.text AS text, score AS score,
       a.url AS url, a.title AS title, a.published_at AS published_at,
       a.domain AS domain, k.id AS case_id
ORDER BY score DESC
"""

# Used when no embeddings exist yet, so the product still answers questions before
# a Voyage key is configured.
FULLTEXT_SEARCH = """
CALL db.index.fulltext.queryNodes('chunk_text', $search, {limit: $k})
YIELD node AS c, score
MATCH (a:Article)-[:HAS_CHUNK]->(c)
OPTIONAL MATCH (a)-[:REPORTS]->(k:Case)
RETURN c.id AS chunk_id, c.text AS text, score AS score,
       a.url AS url, a.title AS title, a.published_at AS published_at,
       a.domain AS domain, k.id AS case_id
ORDER BY score DESC
"""

EMBEDDING_COVERAGE = """
MATCH (c:Chunk)
RETURN count(c) AS total,
       sum(CASE WHEN c.embedded THEN 1 ELSE 0 END) AS embedded
"""

_use_vector_procedure = True


async def coverage() -> dict[str, int]:
    rows = await neo4j_client.run(EMBEDDING_COVERAGE)
    if not rows:
        return {"total": 0, "embedded": 0, "pending": 0}
    total, embedded = rows[0]["total"] or 0, rows[0]["embedded"] or 0
    return {"total": int(total), "embedded": int(embedded), "pending": int(total) - int(embedded)}


async def _store(rows: list[dict]) -> int:
    global _use_vector_procedure

    if _use_vector_procedure:
        try:
            result = await neo4j_client.run_write(STORE_VECTORS, rows=rows)
            return result[0]["stored"] if result else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "db.create.setNodeVectorProperty unavailable (%s); using plain SET", exc
            )
            _use_vector_procedure = False

    result = await neo4j_client.run_write(STORE_VECTORS_FALLBACK, rows=rows)
    return result[0]["stored"] if result else 0


async def backfill(limit: int = 256, batch: int = 64) -> dict[str, int]:
    """Embed chunks that do not have vectors yet.

    Returns counts rather than raising on partial failure: a crawl that embeds 40
    of 50 chunks is still useful, and the remainder is picked up on the next run.
    """
    if not embedder.configured:
        return {"pending": 0, "embedded": 0, "failed": 0, "reason": "voyage_not_configured"}

    rows = await neo4j_client.run(PENDING_CHUNKS, limit=limit)
    if not rows:
        return {"pending": 0, "embedded": 0, "failed": 0}

    embedded = 0
    failed = 0

    for start in range(0, len(rows), batch):
        window = rows[start : start + batch]
        texts = [r["text"] or "" for r in window]
        try:
            vectors = await embedder.embed_documents(texts)
        except EmbeddingError as exc:
            logger.error("Embedding batch failed: %s", exc)
            failed += len(window)
            continue

        stored = await _store(
            [{"id": r["id"], "vector": v} for r, v in zip(window, vectors)]
        )
        embedded += stored

    return {"pending": len(rows), "embedded": embedded, "failed": failed}


async def embed_article(url: str) -> dict[str, int]:
    """Embed chunks belonging to one article. Used immediately after ingest."""
    if not embedder.configured:
        return {"embedded": 0, "reason": "voyage_not_configured"}
    rows = await neo4j_client.run(
        """
        MATCH (a:Article {url: $url})-[:HAS_CHUNK]->(c:Chunk)
        WHERE c.embedded = false OR c.embedded IS NULL
        RETURN c.id AS id, c.text AS text
        """,
        url=url,
    )
    if not rows:
        return {"embedded": 0}
    texts = [r["text"] or "" for r in rows]
    try:
        vectors = await embedder.embed_documents(texts)
    except EmbeddingError as exc:
        logger.error("Article embed failed for %s: %s", url, exc)
        return {"embedded": 0, "failed": len(rows)}
    stored = await _store([{"id": r["id"], "vector": v} for r, v in zip(rows, vectors)])
    return {"embedded": stored}


async def search_chunks(query: str, k: int | None = None) -> tuple[list[dict], str]:
    """Return scored chunks and the retrieval mode actually used.

    Vector search needs both a Voyage key and at least one embedded chunk; when
    either is missing this degrades to the fulltext index rather than returning
    nothing, so the demo works before embeddings are backfilled.
    """
    k = k or settings.top_k
    stats = await coverage()

    if embedder.configured and stats["embedded"] > 0:
        try:
            vector = await embedder.embed_query(query)
            rows = await neo4j_client.run(VECTOR_SEARCH, k=k, vector=vector)
            return rows, "vector"
        except EmbeddingError as exc:
            logger.warning("Vector search unavailable (%s); falling back to fulltext", exc)

    rows = await search_chunks_keyword(_lucene_escape(query), k=k)
    return rows, "fulltext"


async def search_chunks_keyword(search: str, k: int | None = None) -> list[dict]:
    """Lucene recall over chunk text. Independent of embeddings."""
    k = k or settings.top_k
    if not (search or "").strip():
        return []
    rows = await neo4j_client.run(FULLTEXT_SEARCH, search=search, k=k)
    if rows:
        top = max(r["score"] for r in rows) or 1.0
        for row in rows:
            row["score"] = round(row["score"] / top, 4)
    return rows


_LUCENE_SPECIAL = r'+-&|!(){}[]^"~*?:\/'


def _lucene_escape(query: str) -> str:
    """Escape Lucene operators so a natural-language question is a valid query."""
    escaped = "".join(f"\\{ch}" if ch in _LUCENE_SPECIAL else ch for ch in query)
    terms = [t for t in escaped.split() if t]
    # OR semantics: questions carry filler words that would zero out an AND match.
    return " OR ".join(terms) if terms else escaped
