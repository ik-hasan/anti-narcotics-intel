"""Turn one extracted article into graph writes.

Everything is MERGE-based and keyed on canonical names, so re-ingesting the same
URL is idempotent. That is what makes the crawler safe to re-run.
"""

import logging
import re
from datetime import date, datetime
from urllib.parse import urlparse

from app.db.neo4j_client import neo4j_client
from app.models.extraction import ArticleExtraction
from app.services import lexicon
from app.services.chunker import content_hash

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

UPSERT_ARTICLE = """
MERGE (a:Article {url: $url})
ON CREATE SET a.created_at = datetime()
SET a.title = $title,
    a.domain = $domain,
    a.source = $source,
    a.published_at = $published_at,
    a.content_hash = $content_hash,
    a.relevance = $relevance,
    a.summary = $summary,
    a.updated_at = datetime()
WITH a
UNWIND $chunks AS chunk
MERGE (c:Chunk {id: chunk.id})
SET c.text = chunk.text,
    c.position = chunk.position,
    c.article_url = $url,
    c.token_estimate = chunk.token_estimate,
    c.embedded = coalesce(c.embedded, false)
MERGE (a)-[:HAS_CHUNK]->(c)
RETURN count(c) AS chunks
"""

UPSERT_CASE = """
MATCH (a:Article {url: $url})
MERGE (k:Case {id: $case_id})
ON CREATE SET k.created_at = datetime()
SET k.title = $title,
    k.summary = $summary,
    k.date = $case_date,
    k.updated_at = datetime()
MERGE (a)-[:REPORTS]->(k)
RETURN k.id AS case_id
"""

# Alias union is a list comprehension rather than apoc.coll.union: APOC is not
# available on Aura Free.
UPSERT_PERSONS = """
MATCH (k:Case {id: $case_id})
UNWIND $persons AS p
MERGE (person:Person {canonical_name: p.canonical_name})
ON CREATE SET person.created_at = datetime(), person.aliases = []
SET person.name = p.name,
    person.aliases =
      [alias IN coalesce(person.aliases, []) WHERE NOT alias IN p.aliases] + p.aliases
MERGE (person)-[r:INVOLVED_IN]->(k)
SET r.role = p.role
RETURN count(person) AS persons
"""

UPSERT_DRUGS = """
MATCH (k:Case {id: $case_id})
UNWIND $drugs AS d
MERGE (drug:Drug {canonical_name: d.canonical_name})
ON CREATE SET drug.created_at = datetime()
SET drug.name = d.name, drug.category = d.category
MERGE (s:Seizure {id: d.seizure_id})
SET s.quantity = d.quantity,
    s.unit = d.unit,
    s.grams = d.grams,
    s.date = $case_date
MERGE (k)-[:YIELDED]->(s)
MERGE (s)-[:OF_DRUG]->(drug)
RETURN count(drug) AS drugs
"""

UPSERT_LOCATIONS = """
MATCH (k:Case {id: $case_id})
UNWIND $locations AS l
MERGE (loc:Location {canonical_name: l.canonical_name})
ON CREATE SET loc.created_at = datetime()
SET loc.name = l.name,
    loc.city = CASE WHEN l.city <> '' THEN l.city ELSE loc.city END,
    loc.state = CASE WHEN l.state <> '' THEN l.state ELSE loc.state END,
    loc.country = CASE WHEN l.country <> '' THEN l.country ELSE loc.country END
MERGE (k)-[r:OCCURRED_AT]->(loc)
SET r.role = l.role
RETURN count(loc) AS locations
"""

UPSERT_ORGS = """
MATCH (k:Case {id: $case_id})
UNWIND $orgs AS o
MERGE (org:Org {canonical_name: o.canonical_name})
ON CREATE SET org.created_at = datetime()
SET org.name = o.name, org.type = o.type
FOREACH (_ IN CASE WHEN o.type = 'agency' THEN [1] ELSE [] END |
    MERGE (k)-[:HANDLED_BY]->(org))
FOREACH (_ IN CASE WHEN o.type <> 'agency' THEN [1] ELSE [] END |
    MERGE (k)-[:INVOLVES_ORG]->(org))
RETURN count(org) AS orgs
"""

UPSERT_PERSON_LINKS = """
UNWIND $links AS link
MATCH (a:Person {canonical_name: link.source})
MATCH (b:Person {canonical_name: link.target})
WHERE a <> b
MERGE (a)-[r:LINKED_TO]->(b)
ON CREATE SET r.weight = 0
SET r.basis = link.basis, r.weight = coalesce(r.weight, 0) + 1
RETURN count(r) AS links
"""

MENTION_LABELS = ("Person", "Drug", "Location", "Org")


def _mentions_query(label: str) -> str:
    # The label is interpolated because Cypher cannot parameterise labels. It is
    # always one of MENTION_LABELS, never user input. Matching *with* a label lets
    # the canonical_name constraint index do the lookup instead of a full scan.
    return f"""
    UNWIND $mentions AS m
    MATCH (c:Chunk {{id: m.chunk_id}})
    MATCH (e:{label} {{canonical_name: m.canonical_name}})
    MERGE (c)-[:MENTIONS]->(e)
    RETURN count(*) AS mentions
    """


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def _normalize_date(value: str) -> str:
    """Return YYYY-MM-DD, or empty string when unparseable."""
    if not value:
        return ""
    match = _ISO_DATE.match(value.strip())
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        except ValueError:
            return ""
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _grams(quantity: float, unit: str) -> float | None:
    factor = lexicon.UNIT_TO_GRAMS.get(unit.lower().strip())
    return round(quantity * factor, 3) if factor and quantity else None


def _prepare_entities(extraction: ArticleExtraction, case_id: str) -> dict[str, list[dict]]:
    persons = []
    for person in extraction.persons:
        canonical = lexicon.canonical_person(person.name)
        if not canonical or len(canonical) < 3:
            continue
        display = lexicon.display_person(person.name)
        aliases = [a.strip() for a in person.aliases if a.strip()]
        # Keep the honorific form searchable even though it is not shown.
        if person.name.strip() != display:
            aliases.append(person.name.strip())
        persons.append(
            {
                "canonical_name": canonical,
                "name": display,
                "role": (person.role or "unknown").strip().lower(),
                "aliases": aliases,
            }
        )

    drugs = []
    for drug in extraction.drugs:
        canonical = lexicon.canonical_key(drug.name)
        if not canonical:
            continue
        drugs.append(
            {
                "canonical_name": canonical,
                "name": drug.name.strip(),
                "category": lexicon.DRUG_TERMS.get(canonical, "unknown"),
                "quantity": float(drug.quantity or 0.0),
                "unit": (drug.unit or "").strip().lower(),
                "grams": _grams(float(drug.quantity or 0.0), drug.unit or ""),
                "seizure_id": f"{case_id}:{canonical}",
            }
        )

    locations = []
    for loc in extraction.locations:
        canonical = lexicon.canonical_key(loc.name)
        if not canonical:
            continue
        locations.append(
            {
                "canonical_name": canonical,
                "name": loc.name.strip(),
                "city": (loc.city or "").strip(),
                "state": (loc.state or "").strip(),
                "country": (loc.country or "India").strip(),
                "role": (loc.role or "incident").strip().lower(),
            }
        )

    orgs = []
    for org in extraction.orgs:
        canonical = lexicon.canonical_key(org.name)
        if not canonical:
            continue
        org_type = (org.type or "unknown").strip().lower()
        if canonical in lexicon.AGENCY_TERMS:
            org_type = "agency"
        orgs.append({"canonical_name": canonical, "name": org.name.strip(), "type": org_type})

    known_persons = {p["canonical_name"] for p in persons}
    links = []
    for link in extraction.person_links:
        source = lexicon.canonical_person(link.source_person)
        target = lexicon.canonical_person(link.target_person)
        if source in known_persons and target in known_persons and source != target:
            links.append({"source": source, "target": target, "basis": link.basis.strip()})

    return {"persons": persons, "drugs": drugs, "locations": locations, "orgs": orgs, "links": links}


def _build_mentions(
    chunks: list[dict], entities: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """Attach each chunk to the entities whose surface form appears inside it.

    Done in Python rather than Cypher: string matching over a handful of chunks is
    trivial here, and doing it in the database would mean a full text scan per entity.
    """
    candidates: list[tuple[str, str, list[str]]] = []

    for person in entities["persons"]:
        candidates.append(
            ("Person", person["canonical_name"], [person["name"], *person["aliases"]])
        )
    for group, label in (("drugs", "Drug"), ("locations", "Location"), ("orgs", "Org")):
        for item in entities[group]:
            candidates.append((label, item["canonical_name"], [item["name"]]))

    by_label: dict[str, list[dict]] = {label: [] for label in MENTION_LABELS}
    for chunk in chunks:
        chunk_norm = lexicon.normalize(str(chunk["text"]))
        for label, canonical, surfaces in candidates:
            normalized = [lexicon.normalize(s) for s in surfaces]
            if any(surface and surface in chunk_norm for surface in normalized):
                by_label[label].append(
                    {"chunk_id": chunk["id"], "canonical_name": canonical}
                )

    return by_label


async def upsert_article(
    *,
    url: str,
    title: str,
    text: str,
    source: str,
    published_at: str,
    chunks: list[dict],
    extraction: ArticleExtraction,
    signals: dict,
) -> dict[str, int]:
    case_id = content_hash(url)
    case_date = _normalize_date(extraction.case_date) or _normalize_date(published_at)
    entities = _prepare_entities(extraction, case_id)

    counts: dict[str, int] = {}

    result = await neo4j_client.run(
        UPSERT_ARTICLE,
        url=url,
        title=title or extraction.title,
        domain=_domain(url),
        source=source,
        published_at=_normalize_date(published_at),
        content_hash=content_hash(text),
        relevance=float(signals.get("relevance", 0.0)),
        summary=extraction.summary,
        chunks=chunks,
    )
    counts["chunks"] = result[0]["chunks"] if result else 0

    await neo4j_client.run(
        UPSERT_CASE,
        url=url,
        case_id=case_id,
        title=title or extraction.title,
        summary=extraction.summary,
        case_date=case_date,
    )

    for query, key, payload in (
        (UPSERT_PERSONS, "persons", {"persons": entities["persons"]}),
        (UPSERT_DRUGS, "drugs", {"drugs": entities["drugs"], "case_date": case_date}),
        (UPSERT_LOCATIONS, "locations", {"locations": entities["locations"]}),
        (UPSERT_ORGS, "orgs", {"orgs": entities["orgs"]}),
    ):
        if not next(iter(payload.values())):
            counts[key] = 0
            continue
        rows = await neo4j_client.run(query, case_id=case_id, **payload)
        counts[key] = rows[0][key] if rows else 0

    if entities["links"]:
        rows = await neo4j_client.run(UPSERT_PERSON_LINKS, links=entities["links"])
        counts["links"] = rows[0]["links"] if rows else 0
    else:
        counts["links"] = 0

    mentions_by_label = _build_mentions(chunks, entities)
    total_mentions = 0
    for label, mentions in mentions_by_label.items():
        if not mentions:
            continue
        rows = await neo4j_client.run(_mentions_query(label), mentions=mentions)
        total_mentions += rows[0]["mentions"] if rows else 0
    counts["mentions"] = total_mentions

    counts["case_id"] = case_id
    return counts
