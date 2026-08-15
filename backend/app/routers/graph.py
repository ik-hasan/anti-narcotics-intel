"""Cytoscape-ready graph payloads plus the original neighbourhood walk."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.neo4j_client import neo4j_client
from app.db.schema import graph_stats
from app.deps import get_current_user

router = APIRouter(prefix="/api/graph", tags=["graph"], dependencies=[Depends(get_current_user)])

NETWORK_QUERY = """
MATCH (k:Case)
WITH k ORDER BY k.date DESC LIMIT $limit
OPTIONAL MATCH (p:Person)-[r1:INVOLVED_IN]->(k)
OPTIONAL MATCH (k)-[r2:OCCURRED_AT]->(l:Location)
OPTIONAL MATCH (k)-[:YIELDED]->(s:Seizure)-[:OF_DRUG]->(d:Drug)
OPTIONAL MATCH (k)-[r3:HANDLED_BY]->(o:Org)
OPTIONAL MATCH (a:Person)-[link:LINKED_TO]->(b:Person)
WHERE (a)-[:INVOLVED_IN]->(k) AND (b)-[:INVOLVED_IN]->(k)
RETURN
  collect(DISTINCT {
    id: k.id, label: 'Case', name: k.title, date: k.date, extra: k.summary
  }) AS cases,
  collect(DISTINCT CASE WHEN p IS NULL THEN NULL ELSE {
    id: 'person:' + p.canonical_name, label: 'Person', name: p.name, extra: r1.role
  } END) AS persons,
  collect(DISTINCT CASE WHEN l IS NULL THEN NULL ELSE {
    id: 'loc:' + l.canonical_name, label: 'Location', name: l.name, extra: l.city
  } END) AS locations,
  collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
    id: 'drug:' + d.canonical_name, label: 'Drug', name: d.name, extra: d.category
  } END) AS drugs,
  collect(DISTINCT CASE WHEN o IS NULL THEN NULL ELSE {
    id: 'org:' + o.canonical_name, label: 'Org', name: o.name, extra: o.type
  } END) AS orgs,
  collect(DISTINCT CASE WHEN p IS NULL THEN NULL ELSE {
    source: 'person:' + p.canonical_name, target: k.id, rel: 'INVOLVED_IN'
  } END) AS person_edges,
  collect(DISTINCT CASE WHEN l IS NULL THEN NULL ELSE {
    source: k.id, target: 'loc:' + l.canonical_name, rel: 'OCCURRED_AT'
  } END) AS loc_edges,
  collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
    source: k.id, target: 'drug:' + d.canonical_name, rel: 'YIELDED'
  } END) AS drug_edges,
  collect(DISTINCT CASE WHEN o IS NULL THEN NULL ELSE {
    source: k.id, target: 'org:' + o.canonical_name, rel: 'HANDLED_BY'
  } END) AS org_edges,
  collect(DISTINCT CASE WHEN link IS NULL THEN NULL ELSE {
    source: 'person:' + a.canonical_name,
    target: 'person:' + b.canonical_name,
    rel: 'LINKED_TO'
  } END) AS person_links
"""


def _require_graph() -> None:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")


def _elements_from_network(row: dict) -> dict:
    nodes = []
    seen: set[str] = set()
    for group in ("cases", "persons", "locations", "drugs", "orgs"):
        for item in row.get(group) or []:
            if not item or not item.get("id"):
                continue
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            nodes.append(
                {
                    "data": {
                        "id": item["id"],
                        "label": item.get("name") or item["id"],
                        "kind": item.get("label"),
                        "extra": item.get("extra") or "",
                        "date": item.get("date") or "",
                    }
                }
            )
    edges = []
    edge_seen: set[tuple[str, str, str]] = set()
    for group in ("person_edges", "loc_edges", "drug_edges", "org_edges", "person_links"):
        for item in row.get(group) or []:
            if not item or not item.get("source") or not item.get("target"):
                continue
            key = (item["source"], item["target"], item.get("rel") or "")
            if key in edge_seen:
                continue
            edge_seen.add(key)
            edges.append(
                {
                    "data": {
                        "id": f"{key[0]}|{key[2]}|{key[1]}",
                        "source": item["source"],
                        "target": item["target"],
                        "label": item.get("rel") or "",
                    }
                }
            )
    return {"nodes": nodes, "edges": edges}


@router.get("/stats")
async def stats() -> dict:
    _require_graph()
    return await graph_stats()


@router.get("/cases")
async def list_cases(
    limit: int = Query(20, ge=1, le=100),
    city: str = Query("", max_length=80),
    drug: str = Query("", max_length=80),
) -> dict:
    _require_graph()
    rows = await neo4j_client.run(
        """
        MATCH (k:Case)
        OPTIONAL MATCH (k)-[:OCCURRED_AT]->(l:Location)
        OPTIONAL MATCH (k)-[:YIELDED]->(:Seizure)-[:OF_DRUG]->(d:Drug)
        OPTIONAL MATCH (p:Person)-[:INVOLVED_IN]->(k)
        OPTIONAL MATCH (a:Article)-[:REPORTS]->(k)
        WITH k,
             collect(DISTINCT l) AS locs,
             collect(DISTINCT d) AS drugs,
             collect(DISTINCT p.name) AS persons,
             collect(DISTINCT a.url) AS sources
        WHERE ($city = '' OR any(l IN locs WHERE
                toLower(coalesce(l.city, '')) CONTAINS toLower($city)
                OR toLower(coalesce(l.name, '')) CONTAINS toLower($city)))
          AND ($drug = '' OR any(d IN drugs WHERE
                toLower(coalesce(d.name, '')) CONTAINS toLower($drug)))
        RETURN k.id AS id, k.title AS title, k.date AS date, k.summary AS summary,
               [l IN locs WHERE l IS NOT NULL | coalesce(l.city, l.name)] AS locations,
               [d IN drugs WHERE d IS NOT NULL | d.name] AS drugs,
               persons, sources
        ORDER BY date DESC
        LIMIT $limit
        """,
        limit=limit,
        city=city,
        drug=drug,
    )
    return {"count": len(rows), "cases": rows}


@router.get("/network")
async def network(limit: int = Query(40, ge=5, le=80)) -> dict:
    """Whole-graph view sized for the demo corpus / a modest live crawl."""
    _require_graph()
    rows = await neo4j_client.run(NETWORK_QUERY, limit=limit)
    payload = _elements_from_network(rows[0] if rows else {})
    payload["stats"] = await graph_stats()
    return payload


@router.get("/entity")
async def entity_neighborhood(
    name: str = Query(min_length=2),
    hops: int = Query(2, ge=1, le=3),
) -> dict:
    """Constrained neighbourhood walk around one named entity.

    Relationship types are whitelisted rather than unbounded: an open expansion on a
    hub node like 'Mumbai' would pull back most of the graph.
    """
    _require_graph()
    rows = await neo4j_client.run(
        f"""
        CALL db.index.fulltext.queryNodes('entity_names', $name) YIELD node AS start, score
        WITH start ORDER BY score DESC LIMIT 1
        MATCH path = (start)-[:INVOLVED_IN|OCCURRED_AT|YIELDED|OF_DRUG|
                              MEMBER_OF|LINKED_TO|HANDLED_BY|INVOLVES_ORG*1..{hops}]-(other)
        WITH start, path, other LIMIT 300
        RETURN
          {{name: start.name, labels: labels(start),
            id: coalesce(start.canonical_name, start.id)}} AS start,
          collect(DISTINCT {{
            name: other.name,
            title: other.title,
            labels: labels(other),
            id: coalesce(other.canonical_name, other.id)
          }})[..100] AS neighbours,
          count(DISTINCT path) AS paths
        """,
        name=name,
    )
    if not rows or not rows[0]["start"] or rows[0]["start"].get("name") is None:
        raise HTTPException(404, f"No entity matching '{name}'")

    start = rows[0]["start"]
    elements = {"nodes": [], "edges": []}
    start_id = f"{start['labels'][0]}:{start['id']}" if start.get("labels") else start["id"]
    elements["nodes"].append(
        {
            "data": {
                "id": start_id,
                "label": start.get("name") or start_id,
                "kind": (start.get("labels") or ["Entity"])[0],
            }
        }
    )
    for neighbour in rows[0]["neighbours"] or []:
        if not neighbour or not neighbour.get("id"):
            continue
        kind = (neighbour.get("labels") or ["Entity"])[0]
        nid = f"{kind}:{neighbour['id']}"
        elements["nodes"].append(
            {
                "data": {
                    "id": nid,
                    "label": neighbour.get("name") or neighbour.get("title") or nid,
                    "kind": kind,
                }
            }
        )
        elements["edges"].append(
            {"data": {"id": f"{start_id}|{nid}", "source": start_id, "target": nid, "label": ""}}
        )
    return {**rows[0], "elements": elements}
