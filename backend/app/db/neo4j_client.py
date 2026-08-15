import logging
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import settings

logger = logging.getLogger(__name__)

# The driver emits a paragraph-long INFO notification for every idempotent schema
# statement. Suppressed here rather than in main.py so the scripts benefit too.
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


class Neo4jClient:
    """Thin async wrapper around a single shared driver.

    One driver for the whole process: Aura Free has a low connection ceiling and
    Render Free gives us 512 MB, so we never want a second pool.
    """

    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None

    @property
    def is_connected(self) -> bool:
        return self._driver is not None

    async def connect(self) -> None:
        if self._driver is not None:
            return
        if not settings.neo4j_configured:
            logger.warning("NEO4J_URI/NEO4J_PASSWORD not set; running without a graph backend")
            return

        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_max_pool_size,
            connection_acquisition_timeout=30,
        )
        await self._driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s", settings.neo4j_uri)

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j is not connected. Check NEO4J_URI and NEO4J_PASSWORD.")
        result = await self._driver.execute_query(
            query, params, database_=settings.neo4j_database
        )
        return [record.data() for record in result.records]

    async def run_write(self, query: str, **params: Any) -> list[dict[str, Any]]:
        return await self.run(query, **params)

    async def ping(self) -> bool:
        if self._driver is None:
            return False
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception as exc:  # noqa: BLE001 - health probe must never raise
            logger.warning("Neo4j ping failed: %s", exc)
            return False


neo4j_client = Neo4jClient()
