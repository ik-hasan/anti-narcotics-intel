"""Print the full, untruncated error from one minimal LLM call.

Isolates auth and permission problems from model or schema problems: no response
schema, no system prompt, minimal output. Also fingerprints the key so you can
confirm a replacement actually took effect.

    python -m scripts.probe_llm
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def describe_key(key: str) -> None:
    if not key:
        print("key: NOT SET")
        return
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:8]
    print(f"key: len={len(key)} prefix={key[:10]}... fingerprint={fingerprint}")


def probe_groq(model: str) -> int:
    from groq import Groq

    key = settings.groq_api_key
    describe_key(key)
    if not key:
        return 1
    print(f"model: {model}\n")

    client = Groq(api_key=key)

    print("--- models.list() reachable? ---")
    try:
        print(f"ok: {len(client.models.list().data)} models listed\n")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}\n")
        return 1

    print("--- minimal chat completion ---")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_completion_tokens=16,
        )
        print(f"SUCCESS: {response.choices[0].message.content!r}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"type: {type(exc).__name__}")
        print(f"full message:\n{exc}")
        return 1


def probe_gemini(model: str) -> int:
    from google import genai
    from google.genai import types

    key = settings.gemini_api_key
    describe_key(key)
    if not key:
        return 1
    key_type = (
        "auth key (current format)"
        if key.startswith("AQ.")
        else "standard key"
        if key.startswith("AIza")
        else "UNRECOGNISED"
    )
    print(f"key type: {key_type}")
    print(f"model: {model}\n")

    client = genai.Client(api_key=key)

    print("--- models.list() reachable? ---")
    try:
        print(f"ok: {len(list(client.models.list()))} models listed\n")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}\n")
        return 1

    print("--- minimal generate_content ---")
    try:
        response = client.models.generate_content(
            model=model,
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(max_output_tokens=16),
        )
        print(f"SUCCESS: {response.text!r}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"type: {type(exc).__name__}\nfull message:\n{exc}")

    # Bypass the SDK: the raw response sometimes carries detail the SDK collapses.
    print("\n--- raw REST call (SDK bypassed) ---")
    try:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Reply with: ok"}]}]},
            timeout=30,
        )
        print(f"HTTP {response.status_code}")
        print(json.dumps(response.json(), indent=2)[:1500])
    except Exception as exc:  # noqa: BLE001
        print(f"request failed: {type(exc).__name__}: {exc}")
    return 1


def main() -> int:
    provider = settings.llm_provider.lower()
    model = sys.argv[1] if len(sys.argv) > 1 else settings.active_llm_model
    print(f"provider: {provider}")
    if provider == "groq":
        return probe_groq(model)
    if provider == "gemini":
        return probe_gemini(model)
    print(f"Unknown LLM_PROVIDER={provider}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
