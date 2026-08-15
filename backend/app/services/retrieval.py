"""Graph-RAG retrieval: semantic recall, then graph expansion around what it found.

The deck's two modes are decided here. Mode A answers from the graph. Mode B says
the graph does not know enough yet and the OSINT crawler should be run first.
Plain vector RAG would return the closest paragraphs and stop; the graph step is
what surfaces the *other* cases an entity appears in, which is the actual product.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.services import lexicon, vector_store
from app.services.query_focus import QueryFocus, blob_matches, extract_focus

logger = logging.getLogger(__name__)

# Entity -> Case is one hop for people, orgs and locations, but two for drugs
# (Case)-[:YIELDED]->(Seizure)-[:OF_DRUG]->(Drug), hence the bounded expansion.
EXPAND_ENTITIES = """
MATCH (c:Chunk)-[:MENTIONS]->(e)
WHERE c.id IN $chunk_ids
WITH e, count(DISTINCT c) AS mentions
ORDER BY mentions DESC
LIMIT $entity_limit
RETURN labels(e)[0] AS label,
       e.name AS name,
       coalesce(e.canonical_name, e.id) AS key,
       mentions
"""

EXPAND_CASES = """
MATCH (c:Chunk)-[:MENTIONS]->(e)
WHERE c.id IN $chunk_ids
WITH DISTINCT e
MATCH (e)-[:INVOLVED_IN|OCCURRED_AT|HANDLED_BY|INVOLVES_ORG|YIELDED|OF_DRUG*1..2]-(k:Case)
WITH k, collect(DISTINCT e.name) AS via
OPTIONAL MATCH (k)-[:OCCURRED_AT]->(l:Location)
OPTIONAL MATCH (k)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug)
OPTIONAL MATCH (p:Person)-[:INVOLVED_IN]->(k)
OPTIONAL MATCH (a:Article)-[:REPORTS]->(k)
RETURN k.id AS id, k.title AS title, k.date AS date, k.summary AS summary,
       via,
       collect(DISTINCT l.name)[..6] AS locations,
       collect(DISTINCT d.name)[..6] AS drugs,
       collect(DISTINCT p.name)[..8] AS persons,
       collect(DISTINCT a.url)[..3] AS sources
ORDER BY size(via) DESC, date DESC
LIMIT $case_limit
"""

LOOKUP_ENTITIES = """
CALL db.index.fulltext.queryNodes('entity_names', $q) YIELD node AS start, score
WITH start, score
WHERE score >= 0.8
RETURN labels(start)[0] AS label,
       start.name AS name,
       coalesce(start.canonical_name, start.id) AS key,
       score
ORDER BY score DESC
LIMIT $entity_limit
"""

EXPAND_CASES_FROM_KEYS = """
UNWIND $keys AS key
OPTIONAL MATCH (p:Person {canonical_name: key})
OPTIONAL MATCH (l:Location {canonical_name: key})
OPTIONAL MATCH (d:Drug {canonical_name: key})
OPTIONAL MATCH (o:Org {canonical_name: key})
WITH coalesce(p, l, d, o) AS e
WHERE e IS NOT NULL
MATCH (e)-[:INVOLVED_IN|OCCURRED_AT|HANDLED_BY|INVOLVES_ORG|YIELDED|OF_DRUG*1..2]-(k:Case)
WITH k, collect(DISTINCT e.name) AS via
OPTIONAL MATCH (k)-[:OCCURRED_AT]->(loc:Location)
OPTIONAL MATCH (k)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(drug:Drug)
OPTIONAL MATCH (person:Person)-[:INVOLVED_IN]->(k)
OPTIONAL MATCH (a:Article)-[:REPORTS]->(k)
RETURN k.id AS id, k.title AS title, k.date AS date, k.summary AS summary,
       via,
       collect(DISTINCT loc.name)[..6] AS locations,
       collect(DISTINCT drug.name)[..6] AS drugs,
       collect(DISTINCT person.name)[..8] AS persons,
       collect(DISTINCT a.url)[..3] AS sources
ORDER BY size(via) DESC, date DESC
LIMIT $case_limit
"""


@dataclass
class Evidence:
    chunks: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)
    mode: str = "vector"
    high_confidence: int = 0
    required_confidence: int = 0
    sufficient: bool = False
    corpus_chunks: int = 0
    focus: dict = field(default_factory=dict)
    on_topic: bool = True

    def to_dict(self) -> dict:
        return {
            "retrieval_mode": self.mode,
            "high_confidence_chunks": self.high_confidence,
            "required_high_confidence": self.required_confidence,
            "sufficient": self.sufficient,
            "corpus_chunks": self.corpus_chunks,
            "chunks": self.chunks,
            "entities": self.entities,
            "related_cases": self.cases,
            "focus": self.focus,
            "on_topic": self.on_topic,
        }


def _required_high_confidence(_total_chunks: int) -> int:
    """Mode A is allowed only when this many on-topic chunks score ≥ similarity_gate.

    The product rule is fixed (default 10 hits at ≥ 0.80). Scaling it down for a
    small corpus would skip OSINT even when the graph cannot support the question.
    """
    return settings.min_high_conf_chunks


async def _expand_from_chunks(chunk_ids: list[str], case_limit: int = 12) -> tuple[list[dict], list[dict]]:
    if not chunk_ids:
        return [], []
    entities = await neo4j_client.run(
        EXPAND_ENTITIES, chunk_ids=chunk_ids, entity_limit=25
    )
    cases = await neo4j_client.run(
        EXPAND_CASES, chunk_ids=chunk_ids, case_limit=case_limit
    )
    return entities, cases


def _case_blob(case: dict) -> str:
    return " ".join(
        [
            case.get("title") or "",
            case.get("summary") or "",
            " ".join(case.get("persons") or []),
            " ".join(case.get("locations") or []),
            " ".join(case.get("drugs") or []),
            " ".join(case.get("via") or []),
        ]
    )


def _filter_to_focus(
    entities: list[dict], cases: list[dict], focus: QueryFocus
) -> tuple[list[dict], list[dict]]:
    """Drop graph neighbours that only share a city/drug with the hit, not the question.

    A chunk about person X that also mentions Mumbai would otherwise expand into
    every Mumbai case — which looks like the UI is stuck on the previous search.
    """
    if not focus.constrained:
        return entities, cases
    kept_cases = [c for c in cases if blob_matches("", _case_blob(c), focus)]
    allowed = {
        lexicon.normalize(name)
        for case in kept_cases
        for name in (
            *(case.get("persons") or []),
            *(case.get("locations") or []),
            *(case.get("drugs") or []),
            *(case.get("via") or []),
        )
        if name
    }
    kept_entities = [
        entity
        for entity in entities
        if blob_matches(entity.get("name") or "", "", focus)
        or lexicon.normalize(entity.get("name") or "") in allowed
    ]
    return kept_entities, kept_cases


def _topical_chunks(rows: list[dict], focus: QueryFocus) -> list[dict]:
    return [
        row
        for row in rows
        if blob_matches(row.get("title") or "", row.get("text") or "", focus)
    ]


async def _merge_keyword_hits(
    query: str, focus: QueryFocus, existing: list[dict]
) -> list[dict]:
    """Add Lucene hits so an ingested article is found even when vectors point elsewhere."""
    search = focus.lucene_entity_query() or query
    try:
        extra = await vector_store.search_chunks_keyword(search, k=settings.top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Keyword recall failed: %s", exc)
        return existing
    seen = {row.get("chunk_id") for row in existing}
    merged = list(existing)
    for row in _topical_chunks(extra, focus):
        cid = row.get("chunk_id")
        if not cid or cid in seen:
            continue
        merged.append(row)
        seen.add(cid)
    return merged


async def _expand_from_query_entities(focus: QueryFocus) -> tuple[list[dict], list[dict]]:
    lucene = focus.lucene_entity_query()
    if not lucene:
        return [], []
    try:
        entities = await neo4j_client.run(
            LOOKUP_ENTITIES, q=lucene, entity_limit=8
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entity fulltext lookup failed: %s", exc)
        return [], []
    keys = [e["key"] for e in entities if e.get("key") and blob_matches(e.get("name") or "", "", focus)]
    entities = [e for e in entities if blob_matches(e.get("name") or "", "", focus)]
    if not keys:
        return entities, []
    cases = await neo4j_client.run(
        EXPAND_CASES_FROM_KEYS, keys=keys, case_limit=12
    )
    return entities, cases


async def retrieve(query: str, *, top_n: int | None = None) -> Evidence:
    focus = extract_focus(query)
    rows, mode = await vector_store.search_chunks(query, k=settings.top_k)
    stats = await vector_store.coverage()
    top_n = top_n or settings.final_top_n

    vector_topical = _topical_chunks(rows, focus)
    on_topic = await _merge_keyword_hits(query, focus, vector_topical)
    if on_topic and vector_topical and len(on_topic) > len(vector_topical):
        mode = "hybrid"
    elif on_topic and not vector_topical:
        mode = "fulltext"

    gate = settings.similarity_gate
    high = [r for r in on_topic if (r.get("score") or 0) >= gate]
    required = _required_high_confidence(stats["total"])

    evidence = Evidence(
        mode=mode,
        high_confidence=len(high),
        required_confidence=required,
        sufficient=bool(on_topic) and len(high) >= required,
        corpus_chunks=stats["total"],
        focus=focus.to_dict(),
        on_topic=bool(on_topic) or not focus.constrained,
    )

    if on_topic:
        evidence.chunks = (high or on_topic)[:top_n]
        entities, cases = await _expand_from_chunks(
            [r["chunk_id"] for r in evidence.chunks]
        )
        evidence.entities, evidence.cases = _filter_to_focus(entities, cases, focus)
        return evidence

    # Named / token-constrained question missed the chunk index. Try the entity
    # index before declaring a gap — a known person should still walk their cases
    # even if the wording of the question matches no paragraph.
    evidence.chunks = []
    evidence.sufficient = False
    entities, cases = await _expand_from_query_entities(focus)
    evidence.entities, evidence.cases = _filter_to_focus(entities, cases, focus)
    if evidence.entities or evidence.cases:
        evidence.on_topic = True
        # Entity walk can still answer, but it does not satisfy the 10×0.80 gate,
        # so Mode B (search → crawl → ingest) still runs when the caller asked for it.
    return evidence
