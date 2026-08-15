"""Schemas for Gemini structured extraction.

These double as the response_schema handed to Gemini, so keep them flat and avoid
constructs the JSON-schema converter cannot express (unions, recursive models).
"""

from pydantic import BaseModel, Field


class ExtractedPerson(BaseModel):
    name: str
    role: str = Field(default="unknown", description="accused | arrested | suspect | official | witness | unknown")
    aliases: list[str] = Field(default_factory=list)


class ExtractedDrug(BaseModel):
    name: str
    quantity: float = Field(default=0.0, description="0 when not stated")
    unit: str = Field(default="", description="kg, g, tablets, litres, ...")


class ExtractedLocation(BaseModel):
    name: str
    city: str = ""
    state: str = ""
    country: str = "India"
    role: str = Field(default="incident", description="incident | origin | destination | transit")


class ExtractedOrg(BaseModel):
    name: str
    type: str = Field(default="unknown", description="agency | network | gang | company | unknown")


class ExtractedLink(BaseModel):
    source_person: str
    target_person: str
    basis: str = Field(default="", description="why these two are connected, in a few words")


class ArticleExtraction(BaseModel):
    is_narcotics_related: bool
    title: str = ""
    summary: str = Field(default="", description="two or three factual sentences")
    case_date: str = Field(default="", description="ISO YYYY-MM-DD, or empty when unknown")
    persons: list[ExtractedPerson] = Field(default_factory=list)
    drugs: list[ExtractedDrug] = Field(default_factory=list)
    locations: list[ExtractedLocation] = Field(default_factory=list)
    orgs: list[ExtractedOrg] = Field(default_factory=list)
    person_links: list[ExtractedLink] = Field(default_factory=list)


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=50)
    url: str = ""
    title: str = ""
    published_at: str = ""
    source: str = "manual"
    force: bool = Field(
        default=False,
        description="Ingest even when the lexicon pre-pass scores the text as irrelevant",
    )


class IngestResponse(BaseModel):
    status: str
    article_url: str
    chunks: int
    relevance: float
    extraction: ArticleExtraction | None = None
    graph: dict = Field(default_factory=dict)
    reason: str = ""
