import logging

from app.config import settings
from app.db.neo4j_client import neo4j_client

logger = logging.getLogger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT article_url IF NOT EXISTS FOR (a:Article) REQUIRE a.url IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT case_id IF NOT EXISTS FOR (c:Case) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT person_key IF NOT EXISTS FOR (p:Person) REQUIRE p.canonical_name IS UNIQUE",
    "CREATE CONSTRAINT org_key IF NOT EXISTS FOR (o:Org) REQUIRE o.canonical_name IS UNIQUE",
    "CREATE CONSTRAINT drug_key IF NOT EXISTS FOR (d:Drug) REQUIRE d.canonical_name IS UNIQUE",
    "CREATE CONSTRAINT location_key IF NOT EXISTS FOR (l:Location) REQUIRE l.canonical_name IS UNIQUE",
    "CREATE CONSTRAINT seizure_id IF NOT EXISTS FOR (s:Seizure) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT crawljob_id IF NOT EXISTS FOR (j:CrawlJob) REQUIRE j.id IS UNIQUE",
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT user_email IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX article_published IF NOT EXISTS FOR (a:Article) ON (a.published_at)",
    "CREATE INDEX article_hash IF NOT EXISTS FOR (a:Article) ON (a.content_hash)",
    "CREATE INDEX case_date IF NOT EXISTS FOR (c:Case) ON (c.date)",
    "CREATE INDEX location_city IF NOT EXISTS FOR (l:Location) ON (l.city)",
    "CREATE INDEX chunk_embedded IF NOT EXISTS FOR (c:Chunk) ON (c.embedded)",
]

FULLTEXT_INDEXES = [
    """CREATE FULLTEXT INDEX entity_names IF NOT EXISTS
       FOR (n:Person|Org|Location|Drug) ON EACH [n.name]""",
    # Retrieval falls back to this whenever embeddings are missing, so the system
    # can answer questions before a Voyage key exists.
    """CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS
       FOR (c:Chunk) ON EACH [c.text]""",
    """CREATE FULLTEXT INDEX case_text IF NOT EXISTS
       FOR (c:Case) ON EACH [c.title, c.summary]""",
]


def vector_index_statement() -> str:
    # Dimension must match VOYAGE_DIM. Changing it later requires dropping the index.
    return f"""
    CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
    FOR (c:Chunk) ON (c.embedding)
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {settings.voyage_dim},
        `vector.similarity_function`: 'cosine'
    }}}}
    """


async def ensure_schema() -> None:
    if not neo4j_client.is_connected:
        logger.warning("Skipping schema setup; Neo4j not connected")
        return

    statements = [*CONSTRAINTS, *INDEXES, *FULLTEXT_INDEXES, vector_index_statement()]
    for statement in statements:
        try:
            await neo4j_client.run(statement)
        except Exception as exc:  # noqa: BLE001
            # A failure here should degrade the app, not prevent it from booting.
            logger.error("Schema statement failed (%s): %s", statement.split("\n")[0].strip(), exc)

    logger.info("Neo4j schema ensured (%d statements)", len(statements))


async def graph_stats() -> dict[str, int]:
    # Deliberately a plain scan rather than CALL {} subqueries: the subquery syntax
    # shifted across Neo4j 5.x releases and this stays valid on every Aura version.
    rows = await neo4j_client.run(
        """
        OPTIONAL MATCH (n)
        WITH sum(CASE WHEN n:Article THEN 1 ELSE 0 END) AS articles,
             sum(CASE WHEN n:Chunk THEN 1 ELSE 0 END) AS chunks,
             sum(CASE WHEN n:Chunk AND n.embedded THEN 1 ELSE 0 END) AS embedded_chunks,
             sum(CASE WHEN n:Case THEN 1 ELSE 0 END) AS cases,
             sum(CASE WHEN n:Person THEN 1 ELSE 0 END) AS persons,
             sum(CASE WHEN n:Drug THEN 1 ELSE 0 END) AS drugs,
             sum(CASE WHEN n:Location THEN 1 ELSE 0 END) AS locations,
             sum(CASE WHEN n:Org THEN 1 ELSE 0 END) AS orgs,
             sum(CASE WHEN n:Seizure THEN 1 ELSE 0 END) AS seizures
        OPTIONAL MATCH ()-[r]->()
        RETURN articles, chunks, embedded_chunks, cases, persons, drugs,
               locations, orgs, seizures, count(r) AS relationships
        """
    )
    return rows[0] if rows else {}
