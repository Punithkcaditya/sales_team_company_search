"""A stand-in agent for running without API keys.

It implements the same `ResearchAgent` protocol and emits the same chunk types
at roughly the same pace as the real one, so every state the UI has to handle --
searching, per-item streaming, an unresearchable company -- is reachable without
credentials. The real agent in `anthropic_agent.py` is what should be reviewed;
this exists so the app runs end to end for someone who has no keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..schemas import SECTION_ORDER, Section
from .base import ItemChunk, ResearchContext, SearchObserver, SectionChunk, TextDelta, ValueChunk
from .search import SearchResult

# Anything shorter, or with no vowels, is almost certainly a typo or keyboard
# mash rather than a company -- enough to exercise the not-found path.
_MIN_PLAUSIBLE_LENGTH = 3
_VOWELS = set("aeiouAEIOU")

_QUERIES = [
    "{company} company overview products customers",
    "{company} CEO CTO CFO executive leadership team",
    "{company} news acquisition funding earnings 2026",
    "{company} revenue employees market cap growth",
    "{company} lawsuit regulatory investigation security breach",
]


class DemoResearchAgent:
    """Deterministic for a given company name, so demos and tests repeat."""

    def __init__(self, pace: float = 0.02) -> None:
        self._pace = pace

    async def gather(self, company: str, on_search: SearchObserver) -> ResearchContext:
        if not _is_plausible(company):
            return ResearchContext(
                company=company,
                researchable=False,
                note=f"We could not find a company called “{company}”.",
            )

        context = ResearchContext(company=company)
        for template in _QUERIES:
            query = template.format(company=company)
            context.queries.append(query)
            await on_search(query)
            await asyncio.sleep(self._pace * 12)
            context.results.append(
                SearchResult(
                    title=f"{company} — {query.split(company)[-1].strip() or 'profile'}",
                    url=f"https://example.com/{company.lower().replace(' ', '-')}",
                    snippet="Demo mode: no live search was performed.",
                )
            )
        return context

    async def write(self, context: ResearchContext) -> AsyncIterator[tuple[Section, SectionChunk]]:
        for section in SECTION_ORDER:
            async for chunk in self._section(section, context):
                yield section, chunk

    async def _section(
        self, section: Section, context: ResearchContext
    ) -> AsyncIterator[SectionChunk]:
        company = context.company

        if section == "overview":
            for word in _OVERVIEW.format(company=company).split(" "):
                await asyncio.sleep(self._pace)
                yield TextDelta(word + " ")
            return

        if section == "financials":
            await asyncio.sleep(self._pace * 20)
            yield ValueChunk(
                {
                    "revenue": "$1.4B (FY2025)",
                    "employee_count": "~6,200",
                    "market_cap": None,
                    "yoy_growth": "22% YoY",
                }
            )
            return

        for item in _LIST_ITEMS[section]:
            await asyncio.sleep(self._pace * 15)
            yield ItemChunk({k: v.format(company=company) if isinstance(v, str) else v for k, v in item.items()})


def _is_plausible(company: str) -> bool:
    cleaned = company.strip()
    return len(cleaned) >= _MIN_PLAUSIBLE_LENGTH and bool(_VOWELS & set(cleaned))


_OVERVIEW = (
    "{company} builds workflow software for mid-market operations teams, sold as a "
    "per-seat subscription with an enterprise tier. Its core products cover intake, "
    "approvals, and reporting, and it competes mainly with incumbent ITSM suites on "
    "time-to-value rather than breadth. Roughly two-thirds of revenue comes from North "
    "America, with a growing EMEA presence. Recent positioning leans hard on AI-assisted "
    "triage, which is where most competitive displacement conversations start. "
    "(Demo data — no live search was performed.)"
)

_LIST_ITEMS: dict[str, list[dict]] = {
    "key_people": [
        {"name": "Dana Whitfield", "title": "Chief Executive Officer"},
        {"name": "Marcus Oyelaran", "title": "Chief Technology Officer"},
        {"name": "Priya Raghunathan", "title": "Chief Financial Officer"},
        {"name": "Tom Beckett", "title": "Chief Information Security Officer"},
    ],
    "news": [
        {
            "headline": "{company} acquired Latchkey Analytics to add forecasting to its reporting suite",
            "published": "August 2026",
            "source_url": "https://example.com/news/acquisition",
        },
        {
            "headline": "Q2 results beat guidance on 22% YoY growth, driven by enterprise renewals",
            "published": "July 2026",
            "source_url": "https://example.com/news/earnings",
        },
        {
            "headline": "Launched an AI triage assistant, its first agentic product",
            "published": "May 2026",
            "source_url": "https://example.com/news/launch",
        },
        {
            "headline": "Named a former AWS executive as its first Chief Revenue Officer",
            "published": "March 2026",
            "source_url": "https://example.com/news/leadership",
        },
    ],
    "risks": [
        {"risk": "Two enterprise customers publicly cited migration delays during the Q2 call, which competitors are using in displacement pitches."},
        {"risk": "An unresolved class action over 2024 subscription auto-renewal practices is scheduled for hearing this quarter."},
        {"risk": "Heavy reliance on a single cloud provider concentrates both cost and outage exposure."},
    ],
}
