"""The Gemini agent's tool loop and section streaming, with the SDK faked.

External APIs are mocked; what is under test is the loop's behaviour -- which
searches run, how results are handed back, and when it stops.
"""

import json
from types import SimpleNamespace

import pytest
from google.genai._gaos.types.interactions.stepdelta import StepDelta
from google.genai._gaos.types.interactions.textdelta import TextDelta as SDKTextDelta

from app.agent.base import (
    AgentError,
    ItemChunk,
    QuotaExceededError,
    ResearchContext,
    TextDelta,
    ValueChunk,
)
from app.agent import backoff, gemini_agent
from app.agent.gemini_agent import GeminiResearchAgent, as_gemini_tool
from app.agent.prompts import WEB_SEARCH_TOOL
from app.agent.search import SearchError, SearchResult, StaticSearchClient
from app.schemas import Financials


def text_event(text: str) -> StepDelta:
    return StepDelta(delta=SDKTextDelta(text=text), index=0)


def call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", id=call_id, name=name, arguments=arguments)


def interaction(*steps: SimpleNamespace, id: str = "int_1") -> SimpleNamespace:
    return SimpleNamespace(id=id, steps=list(steps))


class FakeInteractions:
    """Returns scripted interactions for the loop, or a scripted event stream."""

    def __init__(self, interactions: list, stream_events: list | None = None) -> None:
        self._interactions = list(interactions)
        self._stream_events = stream_events or []
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            return self._interactions.pop(0) if self._interactions else interaction()

        events = self._stream_events

        async def stream():
            for event in events:
                yield event

        return stream()


def build_agent(interactions=None, stream_events=None, search=None):
    fake = FakeInteractions(interactions or [], stream_events)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=fake))
    agent = GeminiResearchAgent(
        client=client,
        search=search
        or StaticSearchClient([SearchResult(title="T", url="https://x.test", snippet="S")], delay=0),
        model="gemini-flash-latest",
        max_turns=4,
    )
    return agent, fake


def observer(sink: list[str]):
    async def on_search(query: str) -> None:
        sink.append(query)

    return on_search


FINISH = {"company_name": "Acme Corporation", "researchable": True, "note": "ok"}


@pytest.fixture(autouse=True)
def instant_sleep(monkeypatch):
    """Record backoff waits instead of serving them, so the suite stays fast."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(backoff.asyncio, "sleep", fake_sleep)
    return slept


class TestToolShape:
    def test_tools_are_reshaped_from_the_shared_definitions(self):
        """The wording lives in prompts.py; only the envelope is provider-specific."""
        tool = as_gemini_tool(WEB_SEARCH_TOOL)

        assert tool["type"] == "function"
        assert tool["name"] == WEB_SEARCH_TOOL["name"]
        assert tool["description"] == WEB_SEARCH_TOOL["description"]
        assert "query" in tool["parameters"]["properties"]
        # Gemini's schema dialect rejects this, so it must not survive conversion.
        assert "additionalProperties" not in tool["parameters"]


class TestGather:
    async def test_it_runs_the_searches_gemini_asks_for_and_stops_at_finish_research(self):
        agent, fake = build_agent(
            [
                interaction(
                    call("c1", "web_search", {"query": "Acme overview"}),
                    call("c2", "web_search", {"query": "Acme CEO"}),
                    id="int_1",
                ),
                interaction(call("c3", "finish_research", FINISH), id="int_2"),
            ]
        )
        seen: list[str] = []
        context = await agent.gather("acme", observer(seen))

        assert seen == ["Acme overview", "Acme CEO"]
        # The model's canonical name wins over the rep's typing.
        assert context.company == "Acme Corporation"
        assert context.researchable is True
        assert len(context.results) == 2
        assert len(fake.calls) == 2, "it should stop as soon as finish_research is called"

    async def test_results_go_back_as_function_results_matched_to_their_call_ids(self):
        agent, fake = build_agent(
            [
                interaction(
                    call("c1", "web_search", {"query": "a"}),
                    call("c2", "web_search", {"query": "b"}),
                ),
                interaction(call("c3", "finish_research", FINISH), id="int_2"),
            ]
        )
        await agent.gather("Acme", observer([]))

        payload = fake.calls[1]["input"]
        assert [item["call_id"] for item in payload] == ["c1", "c2"]
        assert all(item["type"] == "function_result" for item in payload)
        # The loop continues the same conversation rather than resending it.
        assert fake.calls[1]["previous_interaction_id"] == "int_1"

    async def test_a_failing_search_is_reported_back_rather_than_crashing_the_run(self):
        class BrokenSearch:
            async def search(self, query: str, limit: int = 6):
                raise SearchError("upstream is down")

        agent, fake = build_agent(
            [
                interaction(call("c1", "web_search", {"query": "Acme"})),
                interaction(call("c2", "finish_research", FINISH), id="int_2"),
            ],
            search=BrokenSearch(),
        )
        context = await agent.gather("Acme", observer([]))

        assert "Search failed" in fake.calls[1]["input"][0]["result"][0]["text"]
        # No evidence at all, so we say so rather than invent a briefing.
        assert context.researchable is False

    async def test_gibberish_is_marked_unresearchable(self):
        agent, _ = build_agent(
            [
                interaction(call("c1", "web_search", {"query": "qwertyuiop"})),
                interaction(
                    call(
                        "c2",
                        "finish_research",
                        {"company_name": "qwertyuiop", "researchable": False, "note": "Not a company."},
                    ),
                    id="int_2",
                ),
            ],
            search=StaticSearchClient([], delay=0),
        )
        context = await agent.gather("qwertyuiop", observer([]))

        assert context.researchable is False
        assert context.note == "Not a company."

    async def test_the_loop_is_bounded_so_a_confused_model_cannot_run_forever(self):
        agent, fake = build_agent(
            [
                interaction(call(f"c{i}", "web_search", {"query": f"q{i}"}), id=f"int_{i}")
                for i in range(4)
            ]
        )
        await agent.gather("Acme", observer([]))

        assert len(fake.calls) == 4  # max_turns, not one more

    async def test_both_tools_are_offered_every_turn(self):
        agent, fake = build_agent([interaction(call("c1", "finish_research", FINISH))])
        await agent.gather("Acme", observer([]))

        assert [tool["name"] for tool in fake.calls[0]["tools"]] == ["web_search", "finish_research"]

    async def test_the_built_in_google_search_tool_is_never_requested(self):
        """It has no free-tier quota; asking for it is an instant 429."""
        agent, fake = build_agent([interaction(call("c1", "finish_research", FINISH))])
        await agent.gather("Acme", observer([]))

        assert all(tool["type"] == "function" for tool in fake.calls[0]["tools"])


class TestWriting:
    context = ResearchContext(
        company="Acme",
        results=[SearchResult(title="T", url="https://x.test", snippet="Acme makes widgets.")],
    )

    async def test_the_whole_briefing_comes_from_a_single_request(self):
        """Five calls cost five round trips and re-sent the corpus five times."""
        agent, fake = build_agent(
            stream_events=[
                text_event("##overview\nAcme makes widgets.\n"),
                text_event('##key_people\n{"name": "Ada", "title": "CEO"}\n'),
                text_event('##financials\n{"revenue": "$4.2B", "employee_count": null, '),
                text_event('"market_cap": null, "yoy_growth": null}\n'),
            ]
        )

        chunks = [item async for item in agent.write(self.context)]
        sections = [section for section, _ in chunks]

        assert len(fake.calls) == 1, "the whole briefing must cost one request"
        assert sections[0] == "overview"
        assert ("key_people", ItemChunk({"name": "Ada", "title": "CEO"})) in chunks
        assert (
            "financials",
            ValueChunk(
                {
                    "revenue": "$4.2B",
                    "employee_count": None,
                    "market_cap": None,
                    "yoy_growth": None,
                }
            ),
        ) in chunks

    async def test_prose_still_streams_at_token_granularity(self):
        agent, _ = build_agent(
            stream_events=[
                text_event("##overview\n"),
                text_event("Acme "),
                text_event("makes widgets."),
            ]
        )

        chunks = [chunk for _, chunk in [i async for i in agent.write(self.context)]]

        # Not one lump at the end of the line -- fragments arrive as they are sent.
        assert TextDelta("Acme ") in chunks
        assert TextDelta("makes widgets.") in chunks

    async def test_writing_runs_on_the_writer_model_and_uses_no_tools(self):
        agent, fake = build_agent(stream_events=[])
        agent._writer_model = "gemini-flash-lite-latest"

        [c async for c in agent.write(self.context)]

        request = fake.calls[0]
        assert request["model"] == "gemini-flash-lite-latest"
        assert "tools" not in request
        assert "Acme makes widgets" in request["input"]
        # Shallow work, and nothing needs retaining.
        assert request["generation_config"]["thinking_level"] == "low"
        assert request["store"] is False


class TestProviderErrors:
    """Errors raised by the SDK itself, not delivered as stream events.

    The interactions transport raises from its own exception hierarchy, which is
    unrelated to `genai.errors.APIError`. A quota failure that escapes as an
    unhandled exception reaches the rep as "Research failed unexpectedly" -- so
    these use the real SDK classes rather than stand-ins.
    """

    @staticmethod
    def raising(exc: Exception) -> GeminiResearchAgent:
        class Failing:
            async def create(self, **_):
                raise exc

        return GeminiResearchAgent(
            client=SimpleNamespace(aio=SimpleNamespace(interactions=Failing())),
            search=StaticSearchClient([], delay=0),
            model="gemini-flash-latest",
        )

    @staticmethod
    def transport_error(cls, status: int, message: str = "quota"):
        import httpx

        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/interactions")
        return cls(message, response=httpx.Response(status, request=request), body=None)

    async def test_a_429_from_the_transport_is_reported_as_a_quota_limit(self):
        from google.genai._gaos.lib.compat_errors import RateLimitError

        agent = self.raising(self.transport_error(RateLimitError, 429))

        with pytest.raises(QuotaExceededError) as caught:
            await agent.gather("Samsung", observer([]))

        assert caught.value.code == "quota_exceeded"

    async def test_a_rate_limit_waits_the_delay_the_server_asked_for(self, instant_sleep):
        """The free tier meters per minute and states its own cooldown."""
        from google.genai._gaos.lib.compat_errors import RateLimitError

        agent = self.raising(
            self.transport_error(RateLimitError, 429, "Quota exceeded. Please retry in 17.47s")
        )

        with pytest.raises(QuotaExceededError):
            await agent.gather("Samsung", observer([]))

        # Two waits before the third attempt gives up, each honouring the hint.
        assert instant_sleep == [pytest.approx(17.97), pytest.approx(17.97)]

    async def test_the_stated_delay_is_capped_so_one_request_cannot_hang_forever(
        self, instant_sleep
    ):
        from google.genai._gaos.lib.compat_errors import RateLimitError

        agent = self.raising(self.transport_error(RateLimitError, 429, "Please retry in 900s"))

        with pytest.raises(QuotaExceededError):
            await agent.gather("Samsung", observer([]))

        assert instant_sleep == [backoff.MAX_BACKOFF_SECONDS] * 2

    async def test_a_rate_limited_call_that_then_succeeds_is_not_surfaced_as_an_error(
        self, instant_sleep
    ):
        from google.genai._gaos.lib.compat_errors import RateLimitError

        exc = self.transport_error(RateLimitError, 429, "Please retry in 2s")
        attempts = {"n": 0}

        class FlakyThenFine:
            async def create(self, **kwargs):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise exc
                return interaction(call("c1", "finish_research", FINISH))

        agent = GeminiResearchAgent(
            client=SimpleNamespace(aio=SimpleNamespace(interactions=FlakyThenFine())),
            search=StaticSearchClient([SearchResult(title="T", url="https://x.test", snippet="S")], delay=0),
            model="gemini-flash-latest",
        )
        # A momentary rate limit should cost a pause, not the briefing.
        context = await agent.gather("Acme", observer([]))

        assert attempts["n"] == 2
        assert instant_sleep == [pytest.approx(2.5)]
        assert context.company == "Acme Corporation"

    async def test_a_rejected_key_is_named_as_such_rather_than_a_generic_failure(self):
        from google.genai._gaos.lib.compat_errors import AuthenticationError

        agent = self.raising(self.transport_error(AuthenticationError, 401))

        with pytest.raises(AgentError) as caught:
            await agent.gather("Samsung", observer([]))

        assert "GEMINI_API_KEY" in str(caught.value)

    async def test_quota_is_also_caught_when_it_arrives_as_a_stream_event(self):
        agent, _ = build_agent(
            stream_events=[
                SimpleNamespace(event_type="error", error=SimpleNamespace(code="quota_exceeded"))
            ]
        )

        with pytest.raises(QuotaExceededError):
            [c async for c in agent.write(ResearchContext(company="Acme"))]

    async def test_a_quota_failure_while_writing_also_surfaces_as_quota(self):
        from google.genai._gaos.lib.compat_errors import RateLimitError

        agent = self.raising(self.transport_error(RateLimitError, 429))

        with pytest.raises(QuotaExceededError):
            [c async for c in agent.write(ResearchContext(company="Acme"))]
