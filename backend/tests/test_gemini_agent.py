"""The Gemini agent's research phase and section streaming, with the SDK faked.

The fake speaks the SDK's real event objects rather than stand-ins, so a change
in the provider's event shapes fails here instead of in production.
"""

import json
from types import SimpleNamespace

import pytest
from google.genai._gaos.types.interactions.googlesearchcallarguments import (
    GoogleSearchCallArguments,
)
from google.genai._gaos.types.interactions.googlesearchcalldelta import GoogleSearchCallDelta
from google.genai._gaos.types.interactions.stepdelta import StepDelta
from google.genai._gaos.types.interactions.textdelta import TextDelta as SDKTextDelta

from app.agent.base import AgentError, ItemChunk, ResearchContext, TextDelta, ValueChunk
from app.agent.gemini_agent import GeminiResearchAgent
from app.schemas import Financials


def text_event(text: str) -> StepDelta:
    return StepDelta(delta=SDKTextDelta(text=text), index=0)


def search_event(*queries: str) -> StepDelta:
    return StepDelta(
        delta=GoogleSearchCallDelta(arguments=GoogleSearchCallArguments(queries=list(queries))),
        index=0,
    )


def citation_event(*citations: tuple[str, str]) -> SimpleNamespace:
    """An `interaction.completed` event carrying grounding citations."""
    annotations = [
        SimpleNamespace(type="url_citation", url=url, title=title) for title, url in citations
    ]
    return SimpleNamespace(
        event_type="interaction.completed",
        interaction=SimpleNamespace(
            steps=[SimpleNamespace(content=[SimpleNamespace(annotations=annotations)])]
        ),
    )


class FakeInteractions:
    """Replays one scripted event stream per call."""

    def __init__(self, streams: list[list]) -> None:
        self._streams = list(streams)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        events = self._streams.pop(0) if self._streams else []

        async def stream():
            for event in events:
                yield event

        return stream()


def build_agent(streams: list[list]) -> tuple[GeminiResearchAgent, FakeInteractions]:
    interactions = FakeInteractions(streams)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    return GeminiResearchAgent(client=client, model="gemini-2.5-flash"), interactions


def observer(sink: list[str]):
    async def on_search(query: str) -> None:
        sink.append(query)

    return on_search


DIGEST = {
    "company_name": "Acme Corporation",
    "researchable": True,
    "note": "Covered products, leadership and financials.",
    "findings": "Acme makes widgets. CEO is Ada Lovelace. Revenue $4.2B.",
}


class TestGather:
    async def test_it_forwards_the_queries_the_model_chose_as_they_happen(self):
        agent, _ = build_agent(
            [
                [
                    search_event("Acme Corporation overview"),
                    search_event("Acme Corporation CEO"),
                    text_event(json.dumps(DIGEST)),
                ]
            ]
        )
        seen: list[str] = []
        context = await agent.gather("acme", observer(seen))

        assert seen == ["Acme Corporation overview", "Acme Corporation CEO"]
        assert context.queries == seen
        # The model's canonical name wins over the rep's typing.
        assert context.company == "Acme Corporation"
        assert context.researchable is True
        assert "widgets" in context.findings

    async def test_a_repeated_query_is_only_reported_once(self):
        agent, _ = build_agent(
            [[search_event("Acme news"), search_event("Acme news"), text_event(json.dumps(DIGEST))]]
        )
        seen: list[str] = []
        await agent.gather("Acme", observer(seen))

        assert seen == ["Acme news"]

    async def test_grounding_citations_become_the_report_sources(self):
        agent, _ = build_agent(
            [
                [
                    text_event(json.dumps(DIGEST)),
                    citation_event(("Acme profile", "https://x.test/a"), ("Acme news", "https://x.test/b")),
                ]
            ]
        )
        context = await agent.gather("Acme", observer([]))

        assert [s.url for s in context.sources()] == ["https://x.test/a", "https://x.test/b"]

    async def test_gibberish_is_marked_unresearchable(self):
        digest = {**DIGEST, "researchable": False, "note": "Not a company.", "findings": ""}
        agent, _ = build_agent([[text_event(json.dumps(digest))]])

        context = await agent.gather("qwertyuiop", observer([]))

        assert context.researchable is False
        assert context.note == "Not a company."

    async def test_an_empty_findings_set_is_treated_as_not_found(self):
        """A confident-sounding digest with no evidence must not become a briefing."""
        agent, _ = build_agent([[text_event(json.dumps({**DIGEST, "findings": "   "}))]])

        context = await agent.gather("Acme", observer([]))

        assert context.researchable is False

    async def test_unparseable_output_fails_honestly_rather_than_guessing(self):
        agent, _ = build_agent([[text_event("I could not complete that request.")]])

        context = await agent.gather("Acme", observer([]))

        assert context.researchable is False
        assert "unreadable" in context.note

    async def test_a_digest_wrapped_in_prose_is_still_recovered(self):
        agent, _ = build_agent([[text_event("```json\n" + json.dumps(DIGEST) + "\n```")]])

        context = await agent.gather("Acme", observer([]))

        assert context.researchable is True
        assert context.company == "Acme Corporation"

    async def test_a_stream_error_event_surfaces_as_a_readable_failure(self):
        agent, _ = build_agent([[SimpleNamespace(event_type="error")]])

        with pytest.raises(AgentError):
            await agent.gather("Acme", observer([]))

    async def test_only_the_research_call_is_grounded(self):
        """Grounded prompts are the metered resource; sections must not use one."""
        agent, fake = build_agent([[text_event(json.dumps(DIGEST))], [text_event("Acme.")]])
        context = await agent.gather("Acme", observer([]))
        [c async for c in agent.stream_section("overview", context)]

        assert fake.calls[0]["tools"] == [{"type": "google_search"}]
        assert "tools" not in fake.calls[1]


class TestSectionStreaming:
    context = ResearchContext(company="Acme", findings="Acme makes widgets. CEO is Ada Lovelace.")

    async def test_prose_sections_stream_as_text_fragments(self):
        agent, _ = build_agent([[text_event("Acme "), text_event("makes widgets.")]])

        chunks = [c async for c in agent.stream_section("overview", self.context)]

        assert chunks == [TextDelta("Acme "), TextDelta("makes widgets.")]

    async def test_list_sections_emit_each_entry_as_its_line_completes(self):
        agent, _ = build_agent(
            [[text_event('{"name": "Ada", "title": "CEO"}\n{"name": "Grace",'), text_event(' "title": "CTO"}')]]
        )

        chunks = [c async for c in agent.stream_section("key_people", self.context)]

        assert chunks == [
            ItemChunk({"name": "Ada", "title": "CEO"}),
            ItemChunk({"name": "Grace", "title": "CTO"}),
        ]

    async def test_financials_come_back_whole_and_schema_validated(self):
        payload = {"revenue": "$4.2B", "employee_count": "8,000", "market_cap": None, "yoy_growth": "18%"}
        agent, fake = build_agent([[text_event(json.dumps(payload))]])

        chunks = [c async for c in agent.stream_section("financials", self.context)]

        assert chunks == [ValueChunk(payload)]
        assert fake.calls[0]["response_format"]["mime_type"] == "application/json"

    async def test_unusable_financials_come_back_blank_rather_than_wrong(self):
        agent, _ = build_agent([[text_event("Revenue is roughly four billion dollars.")]])

        chunks = [c async for c in agent.stream_section("financials", self.context)]

        assert chunks == [ValueChunk(Financials().model_dump())]

    @pytest.mark.parametrize("section", ["overview", "key_people", "news", "risks", "financials"])
    async def test_every_section_prompt_carries_the_gathered_evidence(self, section):
        agent, fake = build_agent([[]])

        [c async for c in agent.stream_section(section, self.context)]

        prompt = fake.calls[0]["input"]
        assert "Acme makes widgets" in prompt
        # Section writing is a shallow task; the thinking budget reflects that.
        assert fake.calls[0]["generation_config"]["thinking_level"] == "low"
