"""Try candidate models against the real extraction prompt.

Listing models is not enough: retired or restricted models can appear in a listing
and still fail when called. This runs the actual extraction so the result reflects
exactly what ingest will do.

    python -m scripts.test_extraction                  # candidates for the provider
    python -m scripts.test_extraction llama-3.1-8b-instant   # specific models
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.llm import get_llm, reset_llm_cache  # noqa: E402

GROQ_CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct",
]

GEMINI_CANDIDATES = [
    "gemini-flash-latest",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

SAMPLE = (
    "The Narcotics Control Bureau seized 12 kg of heroin during a pre-dawn operation at "
    "a container yard near the Mumbai docks on Tuesday, 14 January 2026. Two men, "
    "identified as Ravi Deshmukh and Salim Qureshi, were arrested at the site. The "
    "agency estimated the street value at Rs 84 crore. A case has been registered under "
    "the NDPS Act."
)


async def try_model(name: str) -> tuple[bool, str]:
    if settings.llm_provider == "groq":
        settings.groq_model = name
    else:
        settings.gemini_model = name
    reset_llm_cache()

    started = time.perf_counter()
    try:
        result = await get_llm().extract_article(SAMPLE, title="NCB seizes 12 kg heroin")
    except Exception as exc:  # noqa: BLE001
        return False, f"FAILED: {str(exc).splitlines()[0][:120]}"

    elapsed = time.perf_counter() - started
    # Two named people, one drug, one location, one agency are all clearly in the text.
    ok = (
        result.is_narcotics_related
        and len(result.persons) == 2
        and len(result.drugs) >= 1
        and len(result.locations) >= 1
    )
    detail = (
        f"{elapsed:5.1f}s  persons={len(result.persons)} drugs={len(result.drugs)} "
        f"locations={len(result.locations)} orgs={len(result.orgs)} "
        f"date={result.case_date or '-'}"
    )
    return ok, detail


async def main() -> int:
    provider = settings.llm_provider.lower()
    llm = get_llm()
    if not llm.configured:
        print(f"No API key set for LLM_PROVIDER={provider}")
        return 1

    candidates = sys.argv[1:] or (
        GROQ_CANDIDATES if provider == "groq" else GEMINI_CANDIDATES
    )
    original = llm.model

    print(f"provider: {provider}, currently configured: {original}\n")
    print("Testing candidates against the real extraction prompt:\n")

    working: list[str] = []
    for candidate in candidates:
        ok, detail = await try_model(candidate)
        print(f"  {'ok  ' if ok else 'FAIL'} {candidate:<32} {detail}")
        if ok:
            working.append(candidate)
        await asyncio.sleep(1.0)

    if provider == "groq":
        settings.groq_model = original
    else:
        settings.gemini_model = original
    reset_llm_cache()

    print()
    if working:
        env_var = "GROQ_MODEL" if provider == "groq" else "GEMINI_MODEL"
        print(f"Set {env_var} to one of: {', '.join(working)}")
    else:
        print("No candidate produced a valid extraction.")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
