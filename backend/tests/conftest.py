from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.agent.base import AgentError, ItemChunk, ResearchContext, TextDelta, ValueChunk
from app.agent.search import SearchResult
from app.config import Settings, get_settings
from app.schemas import SECTION_ORDER
from app.db import Database
from app.deps import ActiveResearch
from app.main import create_app
from app.repository import ReportRepository


class FakeAgent:
    """A scripted stand-in for the real agent.

    Tests drive behaviour through the constructor rather than by patching the
    provider SDK, so they assert on the pipeline's contract rather than on how
    the agent happens to call its provider.
    """

    def __init__(
        self,
        *,
        researchable: bool = True,
        fail_at: str | None = None,
        people: list[dict] | None = None,
        news: list[dict] | None = None,
        risks: list[dict] | None = None,
    ) -> None:
        self.researchable = researchable
        # Where the single writing stream blows up, if anywhere.
        self.fail_at = fail_at
        self.people = people if people is not None else [{"name": "Ada Lovelace", "title": "CEO"}]
        self.news = (
            news
            if news is not None
            else [{"headline": "Raised a Series C", "source_url": "https://x.test/a"}]
        )
        self.risks = risks if risks is not None else [{"risk": "Pending antitrust review"}]
        self.gathered: list[str] = []

    async def gather(self, company: str, on_search) -> ResearchContext:
        self.gathered.append(company)
        await on_search(f"{company} overview")
        await on_search(f"{company} leadership")
        if not self.researchable:
            return ResearchContext(company=company, researchable=False, note="No such company.")
        return ResearchContext(
            company=company,
            results=[
                SearchResult(title="Profile", url="https://x.test/a", snippet="Does things."),
                SearchResult(title="News", url="https://x.test/b", snippet="Did a thing."),
            ],
        )

    async def write(self, context: ResearchContext) -> AsyncIterator:
        for section in SECTION_ORDER:
            if section == self.fail_at:
                raise AgentError("provider exploded")
            for chunk in self._chunks(section):
                yield section, chunk

    def _chunks(self, section: str) -> list:
        if section == "overview":
            return [TextDelta(part) for part in ("Acme ", "makes ", "widgets.")]
        if section == "key_people":
            return [ItemChunk(person) for person in self.people]
        if section == "news":
            return [ItemChunk(item) for item in self.news]
        if section == "risks":
            return [ItemChunk(risk) for risk in self.risks]
        return [
            ValueChunk(
                {"revenue": "$10M", "employee_count": "50", "market_cap": None, "yoy_growth": "30%"}
            )
        ]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gemini_api_key=None,
        serper_api_key=None,
        llm_provider="demo",
        database_path=":memory:",
        _env_file=None,
    )


@pytest.fixture
def repository() -> ReportRepository:
    return ReportRepository(Database(":memory:"))


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
def app(settings: Settings, repository: ReportRepository, agent: FakeAgent):
    """The real app, with the pieces `lifespan` would build swapped for fakes."""
    application = create_app(settings)
    application.state.repository = repository
    application.state.agent = agent
    application.state.active_research = ActiveResearch()
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def collect_events(client: httpx.AsyncClient, company: str) -> list[tuple[str, dict]]:
    """Run a research request and decode the SSE stream into (event, data)."""
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/api/research", json={"company": company}) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))
    return events


def events_named(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [data for event, data in events if event == name]
