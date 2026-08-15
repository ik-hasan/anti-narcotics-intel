import asyncio
import logging

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.models.extraction import ArticleExtraction
from app.services.prompts import EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "deadline", "unavailable", "timeout")


class TransientGeminiError(RuntimeError):
    pass


class GeminiService:
    name = "gemini"

    def __init__(self) -> None:
        self._client: genai.Client | None = None

    @property
    def configured(self) -> bool:
        return bool(settings.gemini_api_key)

    @property
    def model(self) -> str:
        return settings.gemini_model

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.configured:
                raise RuntimeError("GEMINI_API_KEY is not set")
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    @retry(
        retry=retry_if_exception_type(TransientGeminiError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=12),
        reraise=True,
    )
    async def extract_article(self, text: str, title: str = "") -> ArticleExtraction:
        """Pull entities and relations out of one article as validated JSON."""
        prompt = f"TITLE: {title}\n\nARTICLE:\n{text[:20000]}"

        try:
            response = await self.client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=EXTRACTION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ArticleExtraction,
                    temperature=0.1,
                    # Extraction is mechanical; thinking only adds latency and tokens.
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises a wide range of errors
            if any(marker in str(exc).lower() for marker in _TRANSIENT_MARKERS):
                raise TransientGeminiError(str(exc)) from exc
            raise

        parsed = response.parsed
        if isinstance(parsed, ArticleExtraction):
            return parsed

        logger.warning("Gemini returned unparseable extraction; falling back to empty result")
        return ArticleExtraction(is_narcotics_related=False, title=title)

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 900, temperature: float = 0.2
    ) -> str:
        response = await self.client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (response.text or "").strip()

    async def health(self) -> bool:
        if not self.configured:
            return False
        try:
            await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents="ping",
                    config=types.GenerateContentConfig(
                        max_output_tokens=8,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                ),
                timeout=15,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini health check failed: %s", exc)
            return False
