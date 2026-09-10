"""The Groq agent's tool loop and writing pass, with the SDK faked.

Groq speaks an OpenAI-compatible API, so the shapes differ from Gemini in ways
that are easy to get subtly wrong: tools nest under "function", arguments
arrive as a JSON *string*, and results go back as `role: "tool"` messages keyed
by `tool_call_id`. These pin all three.
"""

import json
from types import SimpleNamespace

import pytest

from app.agent import backoff
from app.agent.base import (
    AgentError,
    ItemChunk,
    QuotaExceededError,
    ResearchContext,
    TextDelta,
)
from app.agent.groq_agent import GroqResearchAgent, as_groq_tool
from app.agent.prompts import WEB_SEARCH_TOOL
from app.agent.search import SearchError, SearchResult, StaticSearchClient


def tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    # The API returns arguments as a JSON string, not an object.
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def message(*calls: SimpleNamespace, content: str | None = None) -> SimpleNamespace:
    msg = SimpleNamespace(
        role="assistant", content=content, tool_calls=list(calls) or None
    )
    msg.model_dump = lambda exclude_none=False: {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls
        ],
    }
    return msg


def completion(msg: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeCompletions:
    def __init__(self, replies: list, stream_chunks: list | None = None) -> None:
        self._replies = list(replies)
        self._stream_chunks = stream_chunks or []
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        # Snapshot `messages`: the agent mutates one list across turns, so
        # keeping the reference would show every call the final conversation.
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if not kwargs.get("stream"):
            return self._replies.pop(0) if self._replies else completion(message())

        chunks = self._stream_chunks

        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()


def build_agent(replies=None, stream_chunks=None, search=None):
    fake = FakeCompletions(replies or [], stream_chunks)
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    agent = GroqResearchAgent(
        client=client,
        search=search
        or StaticSearchClient([SearchResult(title="T", url="https://x.test", snippet="S")], delay=0),
        model="llama-3.3-70b-versatile",
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
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(backoff.asyncio, "sleep", fake_sleep)
    return slept


def test_tools_are_reshaped_into_openai_form():
    """The wording lives in prompts.py; only the envelope is provider-specific."""
    tool = as_groq_tool(WEB_SEARCH_TOOL)

    assert tool["type"] == "function"
    assert tool["function"]["name"] == WEB_SEARCH_TOOL["name"]
    assert tool["function"]["description"] == WEB_SEARCH_TOOL["description"]
    assert "query" in tool["function"]["parameters"]["properties"]


class TestGather:
    async def test_it_runs_the_searches_asked_for_and_stops_at_finish_research(self):
        agent, fake = build_agent(
            [
                completion(
                    message(
                        tool_call("c1", "web_search", {"query": "Acme overview"}),
                        tool_call("c2", "web_search", {"query": "Acme CEO"}),
                    )
                ),
                completion(message(tool_call("c3", "finish_research", FINISH))),
            ]
        )
        seen: list[str] = []
        context = await agent.gather("acme", observer(seen))

        assert seen == ["Acme overview", "Acme CEO"]
        assert context.company == "Acme Corporation"
        assert context.researchable is True
        assert len(context.results) == 2
        assert len(fake.calls) == 2

    async def test_results_go_back_as_tool_messages_keyed_by_call_id(self):
        agent, fake = build_agent(
            [
                completion(
                    message(
                        tool_call("c1", "web_search", {"query": "a"}),
                        tool_call("c2", "web_search", {"query": "b"}),
                    )
                ),
                completion(message(tool_call("c3", "finish_research", FINISH))),
            ]
        )
        await agent.gather("Acme", observer([]))

        sent = fake.calls[1]["messages"]
        tool_messages = [m for m in sent if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
        # The assistant turn carrying the calls must be replayed before them.
        assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in sent)

    async def test_garbled_tool_arguments_do_not_crash_the_run(self):
        """A model can emit arguments that are not valid JSON."""
        broken = SimpleNamespace(
            id="c1", type="function",
            function=SimpleNamespace(name="web_search", arguments="{not json"),
        )
        agent, _ = build_agent(
            [
                completion(message(broken)),
                completion(message(tool_call("c2", "finish_research", FINISH))),
            ],
            search=StaticSearchClient([], delay=0),
        )

        context = await agent.gather("Acme", observer([]))

        assert context.researchable is False  # no evidence gathered

    async def test_a_failing_search_is_reported_back_rather_than_crashing(self):
        class BrokenSearch:
            async def search(self, query: str, limit: int = 6):
                raise SearchError("upstream is down")

        agent, fake = build_agent(
            [
                completion(message(tool_call("c1", "web_search", {"query": "Acme"}))),
                completion(message(tool_call("c2", "finish_research", FINISH))),
            ],
            search=BrokenSearch(),
        )
        context = await agent.gather("Acme", observer([]))

        assert "Search failed" in fake.calls[1]["messages"][-1]["content"]
        assert context.researchable is False

    async def test_gibberish_is_marked_unresearchable(self):
        agent, _ = build_agent(
            [
                completion(message(tool_call("c1", "web_search", {"query": "qwertyuiop"}))),
                completion(
                    message(
                        tool_call(
                            "c2",
                            "finish_research",
                            {"company_name": "qwertyuiop", "researchable": False,
                             "note": "Not a company."},
                        )
                    )
                ),
            ],
            search=StaticSearchClient([], delay=0),
        )
        context = await agent.gather("qwertyuiop", observer([]))

        assert context.researchable is False
        assert context.note == "Not a company."

    async def test_the_loop_is_bounded(self):
        agent, fake = build_agent(
            [completion(message(tool_call(f"c{i}", "web_search", {"query": f"q{i}"}))) for i in range(4)]
        )
        await agent.gather("Acme", observer([]))

        assert len(fake.calls) == 4

    async def test_both_tools_are_offered_every_turn(self):
        agent, fake = build_agent([completion(message(tool_call("c1", "finish_research", FINISH)))])
        await agent.gather("Acme", observer([]))

        names = [t["function"]["name"] for t in fake.calls[0]["tools"]]
        assert names == ["web_search", "finish_research"]


class TestWriting:
    context = ResearchContext(
        company="Acme",
        results=[SearchResult(title="T", url="https://x.test", snippet="Acme makes widgets.")],
    )

    async def test_the_whole_briefing_comes_from_a_single_streamed_request(self):
        agent, fake = build_agent(
            stream_chunks=[
                text_chunk("##overview\nAcme makes widgets.\n"),
                text_chunk('##key_people\n{"name": "Ada", "title": "CEO"}\n'),
            ]
        )

        chunks = [item async for item in agent.write(self.context)]

        assert len(fake.calls) == 1
        assert ("key_people", ItemChunk({"name": "Ada", "title": "CEO"})) in chunks
        assert any(isinstance(c, TextDelta) for _, c in chunks)

    async def test_the_writing_prompt_carries_the_evidence_and_no_tools(self):
        agent, fake = build_agent(stream_chunks=[])

        [c async for c in agent.write(self.context)]

        request = fake.calls[0]
        assert "Acme makes widgets" in request["messages"][-1]["content"]
        assert "tools" not in request
        assert request["stream"] is True


class TestProviderErrors:
    @staticmethod
    def raising(exc: Exception) -> GroqResearchAgent:
        class Failing:
            async def create(self, **_):
                raise exc

        return GroqResearchAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=Failing())),
            search=StaticSearchClient([], delay=0),
            model="llama-3.3-70b-versatile",
        )

    @staticmethod
    def api_error(cls, status: int, message: str):
        import httpx

        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        return cls(message, response=httpx.Response(status, request=request), body=None)

    async def test_a_429_is_reported_as_a_rate_limit(self):
        import groq

        agent = self.raising(self.api_error(groq.RateLimitError, 429, "rate limit"))

        with pytest.raises(QuotaExceededError) as caught:
            await agent.gather("Acme", observer([]))

        assert caught.value.code == "quota_exceeded"

    async def test_groqs_own_cooldown_wording_is_honoured(self, instant_sleep):
        """Groq states waits as "try again in 1m13.6s"."""
        import groq

        agent = self.raising(
            self.api_error(groq.RateLimitError, 429, "Please try again in 1m13.6s")
        )

        with pytest.raises(QuotaExceededError):
            await agent.gather("Acme", observer([]))

        assert instant_sleep == [pytest.approx(min(73.6 + 0.5, backoff.MAX_BACKOFF_SECONDS))] * 2

    async def test_a_rejected_key_is_named_as_such(self):
        import groq

        agent = self.raising(self.api_error(groq.AuthenticationError, 401, "bad key"))

        with pytest.raises(AgentError) as caught:
            await agent.gather("Acme", observer([]))

        assert "GROQ_API_KEY" in str(caught.value)
