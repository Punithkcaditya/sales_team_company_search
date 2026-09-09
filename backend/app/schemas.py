"""Wire and storage shapes for reports.

These models are the contract between the agent, SQLite, and the frontend --
one definition, used everywhere.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Section = Literal["overview", "key_people", "news", "financials", "risks"]

# Ordered the way a rep prepares: what the company is, who runs it, what's
# current, the numbers, then what could blindside them.
SECTION_ORDER: tuple[Section, ...] = (
    "overview",
    "key_people",
    "news",
    "financials",
    "risks",
)

SECTION_LABELS: dict[Section, str] = {
    "overview": "Company Overview",
    "key_people": "Key People",
    "news": "Recent News",
    "financials": "Financial Highlights",
    "risks": "Risk Factors",
}


class Person(BaseModel):
    name: str
    title: str


class NewsItem(BaseModel):
    headline: str
    source_url: str | None = None
    published: str | None = None


class Financials(BaseModel):
    """Every field is nullable on purpose: a private company has no market cap,
    and a fabricated number is worse than an honest blank."""

    revenue: str | None = None
    employee_count: str | None = None
    market_cap: str | None = None
    yoy_growth: str | None = None


class Source(BaseModel):
    title: str
    url: str


class ReportSections(BaseModel):
    overview: str = ""
    key_people: list[Person] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    financials: Financials = Field(default_factory=Financials)
    risks: list[str] = Field(default_factory=list)


class ReportSummary(BaseModel):
    id: int
    company: str
    created_at: str


class Report(ReportSummary):
    sections: ReportSections
    sources: list[Source] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    company: str = Field(min_length=1, max_length=100)

    @field_validator("company")
    @classmethod
    def must_look_like_a_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Enter a company name.")
        if not any(ch.isalnum() for ch in cleaned):
            raise ValueError("Enter a company name -- letters or numbers, please.")
        return cleaned


class RiskItem(BaseModel):
    risk: str
