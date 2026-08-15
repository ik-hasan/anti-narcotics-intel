from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.neo4j_client import neo4j_client
from app.deps import get_current_user
from app.services.risk import evaluate

router = APIRouter(prefix="/api/risk", tags=["risk"], dependencies=[Depends(get_current_user)])


@router.get("/flags")
async def flags() -> dict:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")
    return await evaluate()


@router.get("/person")
async def person_flags(name: str = Query(min_length=2)) -> dict:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")
    report = await evaluate()
    needle = name.strip().lower()
    matches = [
        p
        for p in report["persons"]
        if needle in p["name"].lower() or needle in p["key"]
    ]
    return {"query": name, "count": len(matches), "persons": matches, "disclaimer": report["disclaimer"]}
