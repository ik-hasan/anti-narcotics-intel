from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    # --- Neo4j AuraDB Free ---
    neo4j_uri: str = ""
    # Aura's downloaded credentials file writes NEO4J_USERNAME; accept both spellings
    # so its contents can be pasted into .env unedited.
    neo4j_user: str = Field(
        default="neo4j",
        validation_alias=AliasChoices("NEO4J_USER", "NEO4J_USERNAME"),
    )
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    # Aura Free tolerates few connections; a small pool also keeps driver memory flat.
    neo4j_max_pool_size: int = 5

    # --- LLM provider ---
    # Both are hosted APIs, so neither puts model weights on the Render instance.
    llm_provider: str = "groq"

    groq_api_key: str = ""
    # 8b-instant returns the schema instead of filling it in, so it is unusable
    # here despite being the cheapest option.
    groq_model: str = "llama-3.3-70b-versatile"

    # Retained as a fallback provider only. Gemini retired gemini-2.5-flash for new
    # keys mid-project, and retired models still appear in models.list(), failing
    # only at call time -- hence the alias rather than a pinned version.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

    # --- Voyage embeddings (Phase 2) ---
    voyage_api_key: str = ""
    voyage_model: str = "voyage-4-lite"
    voyage_dim: int = 1024
    voyage_rpm: int = 2000
    voyage_tpm: int = 1_000_000

    # --- Search + crawl (Phase 4) ---
    searxng_url: str = ""
    google_cse_key: str = ""
    google_cse_id: str = ""
    search_provider_order: str = "searxng,duckduckgo,google_cse"
    # Scrapy in a subprocess is the intended crawler; httpx is the fallback.
    crawler_backend: str = "scrapy"
    max_urls_per_query: int = 15
    crawl_time_budget_seconds: int = 120

    # --- Retrieval policy (defaults from the pitch deck) ---
    top_k: int = 100
    similarity_gate: float = 0.80
    min_high_conf_chunks: int = 10
    final_top_n: int = 15
    graph_max_hops: int = 3

    # --- Chunking ---
    chunk_tokens: int = 600
    chunk_overlap_tokens: int = 80

    # --- App ---
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # --- Auth (JWT) ---
    jwt_secret: str = ""
    jwt_expire_hours: int = 24
    otp_expire_minutes: int = 10
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Narco-Graph Intel <noreply@localhost>"

    @property
    def smtp_configured(self) -> bool:
        return bool((self.smtp_host or "").strip() and (self.smtp_user or "").strip() and (self.smtp_password or "").strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def search_providers(self) -> list[str]:
        return [p.strip() for p in self.search_provider_order.split(",") if p.strip()]

    @property
    def jwt_configured(self) -> bool:
        return len((self.jwt_secret or "").strip()) >= 16

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_password)

    @property
    def active_llm_model(self) -> str:
        return self.groq_model if self.llm_provider == "groq" else self.gemini_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
