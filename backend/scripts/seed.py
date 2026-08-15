"""Load the synthetic demo corpus into Neo4j.

Calls the ingest pipeline directly rather than going over HTTP, so it works against
a paused/cold Render service and can be run from any machine with the .env file.

    python -m scripts.seed              # ingest all articles
    python -m scripts.seed --dry-run    # lexicon pre-pass only, no Gemini, no writes
    python -m scripts.seed --wipe       # delete existing graph data first
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.neo4j_client import neo4j_client  # noqa: E402
from app.db.schema import ensure_schema, graph_stats  # noqa: E402
from app.services import lexicon  # noqa: E402
from app.services.ingest import ingest_text  # noqa: E402
from app.services.llm import get_llm  # noqa: E402
from app.services import vector_store  # noqa: E402

SEED_FILE = Path(__file__).parent / "seed_articles.json"


async def wipe_graph() -> None:
    # Batched delete: Aura Free will reject a single unbounded DETACH DELETE on a
    # graph of any size.
    while True:
        rows = await neo4j_client.run(
            "MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(n) AS deleted"
        )
        deleted = rows[0]["deleted"] if rows else 0
        print(f"  deleted {deleted} nodes")
        if deleted == 0:
            break


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Narco-Graph demo corpus")
    parser.add_argument("--dry-run", action="store_true", help="lexicon scoring only")
    parser.add_argument("--wipe", action="store_true", help="clear the graph first")
    args = parser.parse_args()

    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    articles = payload["articles"]
    print(f"Loaded {len(articles)} synthetic articles\n")

    if args.dry_run:
        for article in articles:
            signals = lexicon.summarize_signals(article["text"])
            print(f"  {signals['relevance']:.2f}  {article['title'][:60]}")
            print(f"        drugs={signals['drug_terms']} agencies={signals['agencies'][:3]}")
        return 0

    await neo4j_client.connect()
    if not neo4j_client.is_connected:
        print("ERROR: Neo4j is not configured. Set NEO4J_URI and NEO4J_PASSWORD in backend/.env")
        return 1
    llm = get_llm()
    if not llm.configured:
        print(f"ERROR: no API key set for LLM_PROVIDER={llm.name} in backend/.env")
        return 1
    print(f"Using {llm.name} / {llm.model}\n")

    await ensure_schema()

    if args.wipe:
        print("Wiping existing graph...")
        await wipe_graph()
        await ensure_schema()
        print()

    ingested = skipped = failed = 0
    for index, article in enumerate(articles, start=1):
        label = article["title"][:58]
        try:
            result = await ingest_text(
                text=article["text"],
                url=article["url"],
                title=article["title"],
                published_at=article["published_at"],
                source=article.get("source", "synthetic-demo"),
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[{index:2}/{len(articles)}] FAILED   {label} -> {exc}")
            continue

        if result.status == "ingested":
            ingested += 1
            graph = result.graph
            print(
                f"[{index:2}/{len(articles)}] ok       {label}\n"
                f"           {result.chunks} chunks, {graph.get('persons', 0)} persons, "
                f"{graph.get('drugs', 0)} drugs, {graph.get('locations', 0)} locations, "
                f"{graph.get('mentions', 0)} mentions"
            )
        else:
            skipped += 1
            print(f"[{index:2}/{len(articles)}] skipped  {label} -> {result.reason}")

        # Gentle pacing so a free-tier key does not trip per-minute quotas.
        await asyncio.sleep(1.0)

    print(f"\nIngested {ingested}, skipped {skipped}, failed {failed}")

    try:
        coverage = await vector_store.backfill(limit=64)
        print(f"\nEmbedding backfill: {coverage}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nEmbedding backfill skipped: {exc}")

    print("\nGraph totals:")
    for key, value in (await graph_stats()).items():
        print(f"  {key:18} {value}")

    await neo4j_client.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
