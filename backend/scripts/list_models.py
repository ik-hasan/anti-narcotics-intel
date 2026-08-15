"""List the models the configured provider's key can see.

Availability changes and retired models sometimes remain listed while failing at
call time, so treat this as a starting point and confirm with test_extraction.

    python -m scripts.list_models
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402


def list_groq() -> int:
    from groq import Groq

    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set")
        return 1

    models = Groq(api_key=settings.groq_api_key).models.list().data
    rows = sorted(
        (m.id, getattr(m, "owned_by", ""), getattr(m, "context_window", "") or "")
        for m in models
        if getattr(m, "active", True)
    )
    print(f"{len(rows)} active Groq models:\n")
    for model_id, owner, context in rows:
        marker = "  <-- configured" if model_id == settings.groq_model else ""
        print(f"  {model_id:<45} {str(owner):<14} ctx={context}{marker}")

    if not any(model_id == settings.groq_model for model_id, _, _ in rows):
        print(f"\nWARNING: GROQ_MODEL={settings.groq_model} is not in this list")
    return 0


def list_gemini() -> int:
    from google import genai

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set")
        return 1

    client = genai.Client(api_key=settings.gemini_api_key)
    rows = sorted(
        (m.name.replace("models/", ""), m.display_name or "")
        for m in client.models.list()
        if "generateContent" in (getattr(m, "supported_actions", None) or [])
    )
    print(f"{len(rows)} models support generateContent:\n")
    for name, display in rows:
        marker = "  <-- configured" if name == settings.gemini_model else ""
        print(f"  {name:<45} {display}{marker}")
    return 0


def main() -> int:
    provider = settings.llm_provider.lower()
    print(f"provider: {provider}\n")
    if provider == "groq":
        return list_groq()
    if provider == "gemini":
        return list_gemini()
    print(f"Unknown LLM_PROVIDER={provider}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
