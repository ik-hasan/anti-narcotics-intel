"""Validate every Cypher statement against a live Neo4j without spending Gemini quota.

Builds a hand-written extraction (no LLM call), pushes it through the real upsert
path, then exercises the read queries. Run this after any schema change, and against
Aura once before a demo.

    python -m scripts.verify_graph
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.neo4j_client import neo4j_client  # noqa: E402
from app.db.schema import ensure_schema, graph_stats  # noqa: E402
from app.models.extraction import (  # noqa: E402
    ArticleExtraction,
    ExtractedDrug,
    ExtractedLink,
    ExtractedLocation,
    ExtractedOrg,
    ExtractedPerson,
)
from app.services import lexicon  # noqa: E402
from app.services.chunker import build_chunks  # noqa: E402
from app.services.graph_upsert import upsert_article  # noqa: E402

SAMPLE_TEXT = (
    "The Narcotics Control Bureau seized 12 kg of heroin at a container yard near the "
    "Mumbai docks on Tuesday. Two men, Ravi Deshmukh and Salim Qureshi, were arrested. "
    "Officials said Deshmukh had arranged clearing paperwork for three earlier "
    "consignments. A case was registered under the NDPS Act. The estimated value of "
    "the seizure is Rs 84 crore. Investigators said the consignment entered through a "
    "coastal route using forged export documentation."
)

SAMPLE_EXTRACTION = ArticleExtraction(
    is_narcotics_related=True,
    title="NCB seizes 12 kg heroin in Mumbai dock operation",
    summary="NCB seized 12 kg heroin at a Mumbai container yard and arrested two men.",
    case_date="2026-01-14",
    persons=[
        ExtractedPerson(name="Ravi Deshmukh", role="arrested", aliases=["Ravi D"]),
        ExtractedPerson(name="Shri Salim Qureshi", role="arrested"),
    ],
    drugs=[ExtractedDrug(name="heroin", quantity=12.0, unit="kg")],
    locations=[
        ExtractedLocation(name="Mumbai docks", city="Mumbai", state="Maharashtra"),
    ],
    orgs=[ExtractedOrg(name="Narcotics Control Bureau", type="agency")],
    person_links=[
        ExtractedLink(
            source_person="Ravi Deshmukh",
            target_person="Salim Qureshi",
            basis="arrested together in the same seizure",
        )
    ],
)

URL = "https://example-demo.invalid/verify-graph-fixture"

# The fixture describes the same fictional event as a seed article, so leaving it
# behind makes one raid look like two cases in the demo graph.
PURGE = """
MATCH (a:Article {url: $url})
OPTIONAL MATCH (a)-[:HAS_CHUNK]->(ch:Chunk)
OPTIONAL MATCH (a)-[:REPORTS]->(k:Case)
OPTIONAL MATCH (k)-[:YIELDED]->(s:Seizure)
DETACH DELETE a, ch, k, s
"""

PURGE_ORPHANS = """
MATCH (n)
WHERE (n:Person OR n:Drug OR n:Location OR n:Org) AND NOT (n)--()
DELETE n
RETURN count(n) AS orphans
"""


async def purge_fixture() -> int:
    """Remove the fixture and any entities left with no remaining links."""
    await neo4j_client.run_write(PURGE, url=URL)
    rows = await neo4j_client.run_write(PURGE_ORPHANS)
    return rows[0]["orphans"] if rows else 0

CHECKS: list[tuple[str, str]] = [
    (
        "vector index present",
        "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN count(*) AS n",
    ),
    (
        "fulltext index lookup",
        "CALL db.index.fulltext.queryNodes('entity_names', 'Deshmukh') "
        "YIELD node RETURN count(node) AS n",
    ),
    (
        "constrained 2-hop walk",
        "MATCH p = (:Person)-[:INVOLVED_IN|OCCURRED_AT|YIELDED|OF_DRUG|"
        "MEMBER_OF|LINKED_TO|HANDLED_BY|INVOLVES_ORG*1..2]-() RETURN count(p) AS n",
    ),
    (
        "risk rule R1 shape (multi-city actor)",
        "MATCH (p:Person)-[:INVOLVED_IN]->(c:Case)-[:OCCURRED_AT]->(l:Location) "
        "WITH p, count(DISTINCT c) AS cases, count(DISTINCT l.city) AS cities "
        "RETURN count(*) AS n",
    ),
    (
        "risk rule R2 shape (diversified hub)",
        "MATCH (l:Location)<-[:OCCURRED_AT]-(:Case)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug) "
        "WITH l, count(DISTINCT d.canonical_name) AS types RETURN count(*) AS n",
    ),
    (
        "case listing join",
        "MATCH (k:Case) OPTIONAL MATCH (k)-[:OCCURRED_AT]->(l:Location) "
        "OPTIONAL MATCH (k)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug) "
        "RETURN k.id AS id, collect(DISTINCT l.name) AS locs, "
        "collect(DISTINCT d.name) AS drugs",
    ),
    (
        "vector query on empty embeddings (Phase 2 readiness)",
        "SHOW INDEXES YIELD name, state WHERE name = 'chunk_embedding' "
        "RETURN name, state",
    ),
]


async def main() -> int:
    await neo4j_client.connect()
    if not neo4j_client.is_connected:
        print("ERROR: set NEO4J_URI / NEO4J_PASSWORD first")
        return 1

    failures = 0

    print("1. ensure_schema()")
    await ensure_schema()
    indexes = await neo4j_client.run("SHOW INDEXES YIELD name, type RETURN name, type")
    print(f"   {len(indexes)} indexes present")

    print("\n2. upsert_article() with a fixed extraction")
    chunks = build_chunks(URL, SAMPLE_TEXT)
    counts = await upsert_article(
        url=URL,
        title=SAMPLE_EXTRACTION.title,
        text=SAMPLE_TEXT,
        source="verify",
        published_at="2026-01-15",
        chunks=chunks,
        extraction=SAMPLE_EXTRACTION,
        signals=lexicon.summarize_signals(SAMPLE_TEXT),
    )
    print(f"   {counts}")

    print("\n3. idempotency: same upsert again")
    counts_again = await upsert_article(
        url=URL,
        title=SAMPLE_EXTRACTION.title,
        text=SAMPLE_TEXT,
        source="verify",
        published_at="2026-01-15",
        chunks=chunks,
        extraction=SAMPLE_EXTRACTION,
        signals=lexicon.summarize_signals(SAMPLE_TEXT),
    )
    # Scope the assertion to this fixture's case: a global count would also see
    # whatever the seed corpus has already put in the graph.
    rows = await neo4j_client.run(
        "MATCH (p:Person)-[:INVOLVED_IN]->(:Case {id: $case_id}) RETURN count(p) AS persons",
        case_id=counts_again["case_id"],
    )
    stats = {"persons": rows[0]["persons"] if rows else 0}
    if stats["persons"] != 2:
        failures += 1
        print(f"   FAIL: expected 2 persons after re-upsert, got {stats['persons']}")
    else:
        print(f"   ok: still {stats['persons']} persons, {counts_again['mentions']} mentions")

    print("\n4. honorific stripping")
    rows = await neo4j_client.run(
        "MATCH (p:Person) RETURN p.canonical_name AS key, p.name AS name ORDER BY key"
    )
    for row in rows:
        print(f"   {row['key']!r} <- {row['name']!r}")
    if any(row["key"].startswith("shri") for row in rows):
        failures += 1
        print("   FAIL: honorific was not stripped from the canonical key")

    print("\n5. read queries")
    for label, query in CHECKS:
        try:
            result = await neo4j_client.run(query)
            print(f"   ok    {label}: {result[0] if result else '(no rows)'}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"   FAIL  {label}: {exc}")

    print("\n6. cleanup")
    orphans = await purge_fixture()
    print(f"   fixture removed, {orphans} orphaned entities cleared")

    print("\n7. graph stats")
    for key, value in (await graph_stats()).items():
        print(f"   {key:18} {value}")

    await neo4j_client.close()
    print(f"\n{'PASSED' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
