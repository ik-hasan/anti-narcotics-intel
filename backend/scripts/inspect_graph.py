"""Read-only report on how well the graph has connected across articles.

The value of this project is cross-article linkage, not row counts, so this
prints the joins a single article could never produce on its own.
"""

from __future__ import annotations

import asyncio

from app.db.neo4j_client import neo4j_client

QUERIES: list[tuple[str, str]] = [
    (
        "People appearing in more than one case",
        """
        MATCH (p:Person)-[:INVOLVED_IN]->(c:Case)
        WITH p, collect(DISTINCT c.title) AS cases
        WHERE size(cases) > 1
        RETURN p.name AS name, size(cases) AS n, cases
        ORDER BY n DESC LIMIT 10
        """,
    ),
    (
        "Co-accused pairs (same case, different people)",
        """
        MATCH (a:Person)-[:INVOLVED_IN]->(c:Case)<-[:INVOLVED_IN]-(b:Person)
        WHERE a.canonical_name < b.canonical_name
        RETURN a.name AS person_a, b.name AS person_b, c.title AS via
        LIMIT 10
        """,
    ),
    (
        "Drugs spanning multiple cities",
        """
        MATCH (c:Case)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug)
        MATCH (c)-[:OCCURRED_AT]->(l:Location)
        WITH d, collect(DISTINCT coalesce(l.city, l.name)) AS cities
        WHERE size(cities) > 1
        RETURN d.name AS drug, size(cities) AS n, cities
        ORDER BY n DESC LIMIT 10
        """,
    ),
    (
        "Busiest agencies",
        """
        MATCH (o:Org)<-[:INVOLVES_ORG|HANDLED_BY]-(c:Case)
        RETURN o.name AS org, count(DISTINCT c) AS cases
        ORDER BY cases DESC LIMIT 8
        """,
    ),
    (
        "Cities by case volume",
        """
        MATCH (c:Case)-[:OCCURRED_AT]->(l:Location)
        RETURN coalesce(l.city, l.name) AS city, count(DISTINCT c) AS cases
        ORDER BY cases DESC LIMIT 8
        """,
    ),
]


async def main() -> None:
    await neo4j_client.connect()
    try:
        for title, cypher in QUERIES:
            rows = await neo4j_client.run(cypher)
            print(f"\n=== {title} ===")
            if not rows:
                print("  (none)")
                continue
            for row in rows:
                print("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
    finally:
        await neo4j_client.close()


if __name__ == "__main__":
    asyncio.run(main())
