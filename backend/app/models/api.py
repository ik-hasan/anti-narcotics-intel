"""HTTP request/response models that are not part of LLM extraction."""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=3, max_length=800)
    discover: bool = Field(
        default=True,
        description="If fewer than min_high_conf_chunks score ≥ similarity_gate, run OSINT",
    )
    top_n: int = Field(default=15, ge=3, le=20)


class CrawlRequest(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    max_urls: int = Field(default=0, ge=0, le=15, description="0 uses the server default")
    force: bool = Field(
        default=False,
        description="Start even if another crawl is already running",
    )


class EmbedRequest(BaseModel):
    limit: int = Field(default=256, ge=1, le=2000)
