import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.db.schema import ensure_schema
from app.routers import ask, auth, graph, health, ingest, risk
from app.services.embeddings import embedder

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup must never hard-fail: on Render Free a crash loop is far worse than a
    # degraded service that reports why it is degraded via /health.
    try:
        await neo4j_client.connect()
        await ensure_schema()
    except Exception as exc:  # noqa: BLE001
        logger.error("Startup could not initialise Neo4j: %s", exc)
    if settings.smtp_configured:
        logger.info("OTP email: SMTP %s as %s", settings.smtp_host, settings.smtp_user)
    else:
        logger.warning("OTP email: SMTP is not configured; codes will be logged only")
    yield
    await embedder.close()
    await neo4j_client.close()


app = FastAPI(
    title="Narco-Graph Intel API",
    description="Graph-RAG anti-narcotics intelligence platform with adaptive OSINT crawling",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(graph.router)
app.include_router(ask.router)
app.include_router(risk.router)


@app.get("/")
async def root() -> dict:
    return {
        "service": "Narco-Graph Intel API",
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/health",
    }
