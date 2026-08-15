"""Backfill Voyage embeddings for chunks that were ingested without vectors.

    python -m scripts.backfill_embeddings
    python -m scripts.backfill_embeddings --limit 64
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.neo4j_client import neo4j_client  # noqa: E402
from app.services import vector_store  # noqa: E402
from app.services.embeddings import embedder  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=256)
    args = parser.parse_args()

    await neo4j_client.connect()
    if not neo4j_client.is_connected:
        print("ERROR: Neo4j is not configured")
        return 1
    if not embedder.configured:
        print("ERROR: VOYAGE_API_KEY is not set")
        return 1

    before = await vector_store.coverage()
    print(f"before: {before}")
    result = await vector_store.backfill(limit=args.limit)
    after = await vector_store.coverage()
    print(f"backfill: {result}")
    print(f"after: {after}")
    await embedder.close()
    await neo4j_client.close()
    return 0 if not result.get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
