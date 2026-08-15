"""Pattern-based risk flags over the knowledge graph.

These are prioritisation aids, not accusations. Each rule is a Cypher shape that
cannot fire from a single article: it needs the graph to have joined two reports.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.neo4j_client import neo4j_client

RULES = [
    {
        "id": "R1",
        "name": "Multi-city actor",
        "severity": "high",
        "description": "A named person appears in cases across two or more cities.",
    },
    {
        "id": "R2",
        "name": "Diversified location hub",
        "severity": "medium",
        "description": "One location is tied to three or more distinct drug types.",
    },
    {
        "id": "R3",
        "name": "Recurring co-accused",
        "severity": "high",
        "description": "The same two people appear together in two or more cases.",
    },
    {
        "id": "R4",
        "name": "Repeat actor",
        "severity": "medium",
        "description": "A named person is involved in three or more separate cases.",
    },
    {
        "id": "R5",
        "name": "High-volume seizure",
        "severity": "medium",
        "description": "A seizure of 1 kg or more of a controlled substance.",
    },
    {
        "id": "R6",
        "name": "Cross-substance actor",
        "severity": "medium",
        "description": "A person is linked to two or more drug categories (e.g. opioid and stimulant).",
    },
]

R1 = """
MATCH (p:Person)-[:INVOLVED_IN]->(c:Case)-[:OCCURRED_AT]->(l:Location)
WITH p,
     count(DISTINCT c) AS cases,
     collect(DISTINCT CASE WHEN l.city <> '' THEN l.city ELSE l.name END) AS cities
WHERE cases >= 2 AND size(cities) >= 2
RETURN p.name AS name, p.canonical_name AS key, cases, cities
ORDER BY size(cities) DESC, cases DESC
LIMIT 50
"""

R2 = """
MATCH (l:Location)<-[:OCCURRED_AT]-(c:Case)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug)
WITH l,
     count(DISTINCT c) AS cases,
     collect(DISTINCT d.name) AS drugs
WHERE size(drugs) >= 3
RETURN l.name AS name, l.canonical_name AS key,
       coalesce(l.city, '') AS city, cases, drugs
ORDER BY size(drugs) DESC, cases DESC
LIMIT 50
"""

R3 = """
MATCH (a:Person)-[:INVOLVED_IN]->(c:Case)<-[:INVOLVED_IN]-(b:Person)
WHERE a.canonical_name < b.canonical_name
WITH a, b,
     count(DISTINCT c) AS shared,
     collect(DISTINCT c.title)[..6] AS cases
WHERE shared >= 2
RETURN a.name AS person_a, a.canonical_name AS key_a,
       b.name AS person_b, b.canonical_name AS key_b,
       shared, cases
ORDER BY shared DESC
LIMIT 50
"""

R4 = """
MATCH (p:Person)-[:INVOLVED_IN]->(c:Case)
WITH p, count(DISTINCT c) AS cases, collect(DISTINCT c.title)[..8] AS titles
WHERE cases >= 3
RETURN p.name AS name, p.canonical_name AS key, cases, titles
ORDER BY cases DESC
LIMIT 50
"""

R5 = """
MATCH (k:Case)-[:YIELDED]->(s:Seizure)-[:OF_DRUG]->(d:Drug)
WHERE coalesce(s.grams, 0) >= 1000
OPTIONAL MATCH (p:Person)-[:INVOLVED_IN]->(k)
RETURN k.id AS case_id, k.title AS title, k.date AS date,
       d.name AS drug, s.grams AS grams, s.quantity AS quantity, s.unit AS unit,
       collect(DISTINCT p.name)[..6] AS persons
ORDER BY s.grams DESC
LIMIT 50
"""

R6 = """
MATCH (p:Person)-[:INVOLVED_IN]->(c:Case)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug)
WITH p,
     [cat IN collect(DISTINCT d.category) WHERE cat IS NOT NULL AND cat <> 'unknown'] AS categories,
     collect(DISTINCT d.name) AS drugs
WHERE size(categories) >= 2
RETURN p.name AS name, p.canonical_name AS key, categories, drugs
ORDER BY size(categories) DESC
LIMIT 50
"""


def _band(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _person_scores(r1: list[dict], r3: list[dict], r4: list[dict], r6: list[dict]) -> list[dict]:
    """Roll person-centric rules into one score per canonical name."""
    by_key: dict[str, dict] = {}

    def slot(key: str, name: str) -> dict:
        item = by_key.get(key)
        if item is None:
            item = {"key": key, "name": name, "score": 0, "band": "low", "rules": []}
            by_key[key] = item
        return item

    for row in r1:
        item = slot(row["key"], row["name"])
        item["score"] += 20 + 8 * min(len(row["cities"]), 4)
        item["rules"].append(
            {"id": "R1", "detail": f"{row['cases']} cases across {', '.join(row['cities'])}"}
        )

    for row in r3:
        for key, name, other in (
            (row["key_a"], row["person_a"], row["person_b"]),
            (row["key_b"], row["person_b"], row["person_a"]),
        ):
            item = slot(key, name)
            item["score"] += 15 + 10 * min(int(row["shared"]), 4)
            item["rules"].append(
                {"id": "R3", "detail": f"Co-appears with {other} in {row['shared']} cases"}
            )

    for row in r4:
        item = slot(row["key"], row["name"])
        item["score"] += 10 + 8 * min(int(row["cases"]), 5)
        item["rules"].append({"id": "R4", "detail": f"Named in {row['cases']} cases"})

    for row in r6:
        item = slot(row["key"], row["name"])
        item["score"] += 12 + 6 * min(len(row["categories"]), 4)
        item["rules"].append(
            {"id": "R6", "detail": f"Categories: {', '.join(row['categories'])}"}
        )

    ranked = sorted(by_key.values(), key=lambda x: x["score"], reverse=True)
    for item in ranked:
        item["score"] = min(item["score"], 100)
        item["band"] = _band(item["score"])
    return ranked


async def evaluate() -> dict:
    r1 = await neo4j_client.run(R1)
    r2 = await neo4j_client.run(R2)
    r3 = await neo4j_client.run(R3)
    r4 = await neo4j_client.run(R4)
    r5 = await neo4j_client.run(R5)
    r6 = await neo4j_client.run(R6)

    persons = _person_scores(r1, r3, r4, r6)
    locations = [
        {
            "key": row["key"],
            "name": row["name"],
            "city": row["city"],
            "score": min(40 + 12 * min(len(row["drugs"]), 5), 100),
            "band": _band(min(40 + 12 * min(len(row["drugs"]), 5), 100)),
            "rules": [
                {
                    "id": "R2",
                    "detail": f"{len(row['drugs'])} drug types across {row['cases']} cases: "
                    + ", ".join(row["drugs"]),
                }
            ],
        }
        for row in r2
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Flags are graph-pattern matches on public reporting. They are "
            "prioritisation aids for a human analyst, not findings of guilt."
        ),
        "rules": RULES,
        "counts": {
            "r1_multi_city": len(r1),
            "r2_hubs": len(r2),
            "r3_pairs": len(r3),
            "r4_repeat": len(r4),
            "r5_volume": len(r5),
            "r6_cross_substance": len(r6),
            "scored_persons": len(persons),
        },
        "persons": persons,
        "locations": locations,
        "pairs": r3,
        "seizures": r5,
        "raw": {"r1": r1, "r2": r2, "r4": r4, "r6": r6},
    }


async def flags_for_names(names: list[str]) -> list[dict]:
    """Subset of person flags whose display or canonical name is in `names`."""
    if not names:
        return []
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    report = await evaluate()
    return [
        p
        for p in report["persons"]
        if p["name"].lower() in wanted or p["key"] in wanted
    ]
