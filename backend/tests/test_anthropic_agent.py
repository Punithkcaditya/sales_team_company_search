"""The real agent's tool loop and streaming, with the Anthropic client faked.

External APIs are mocked; the loop's actual behaviour is what is under test --
which searches run, how results are handed back, and when it stops.
"""

from types import SimpleNamespace

import pytest

from app.agent.anthropic_agent import AnthropicResearchAgent
from app.agent.base import ItemChunk, ResearchContext, TextDelta, ValueChunk
from app.agent.search import SearchError, SearchResult, StaticSearchClient
from app.schemas import Financials


def tool_use(tool_id: str, name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=payload)


def message(*blocks: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeMessages:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.parse_calls: list[dict] = []
        self.stream_text: list[str] = []
        self.parsed: object = None

    async def create(self, **kwargs):
        # Snapshot `messages`: the agent mutates one list across turns, so
        # keeping the reference would show every call the final conversation.
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return self._responses.pop(0)

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.parsed)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.stream_text)


class FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    @property
    def text_stream(self):
        async def generator():
            for chunk in self._chunks:
                yield chunk

        return generator()


def build_agent(responses: list, search=None) -> tuple[AnthropicResearchAgent, FakeMessages]:
    messages = FakeMessages(responses)
    client = SimpleNamespace(messages=messages)
    agent = AnthropicResearchAgent(
        client=client,  # type: ignore[arg-type]
        search=search or StaticSearchClient([SearchResult(title="T", url="https://x.test", snippet="S")], delay=0),
        model="claude-opus-5",
        max_turns=4,
    )
    return agent, messages


def observer(sink: list[str]):
    """An `on_search` callback that records what the agent decided to look up."""

    async def on_search(query: str) -> None:
        sink.append(query)

    return on_search


class TestGather:
    async def test_it_runs_the_searches_claude_asks_for_and_stops_at_finish_research(self):
        agent, fake = build_agent(
            [
                message(
                    tool_use("t1", "web_search", {"query": "Acme overview"}),
                    tool_use("t2", "web_search", {"query": "Acme CEO"}),
                ),
                message(
                    tool_use(
                        "t3",
                        "finish_research",
                        {"company_name": "Acme Corporation", "researchable": True, "note": "ok"},
                    )
                ),
            ]
        )
        seen: list[str] = []
        context = await agent.gather("acme", observer(seen))

        assert seen == ["Acme overview", "Acme CEO"]
        assert context.researchable is True
        # The canonical name from the agent wins over the rep's typing.
        assert context.company == "Acme Corporation"
        assert len(context.results) == 2
        assert len(fake.calls) == 2, "it should stop as soon as finish_research is called"

    async def test_parallel_searches_come_back_in_a_single_user_message(self):
        """Splitting tool results across messages teaches the model to stop batching."""
        agent, fake = build_agent(
            [
                message(
                    tool_use("t1", "web_search", {"query": "a"}),
                    tool_use("t2", "web_search", {"query": "b"}),
                ),
                message(
                    tool_use("t3", "finish_research", {"company_name": "Acme", "researchable": True, "note": ""})
                ),
            ]
        )
        await agent.gather("Acme", observer([]))

        follow_up = fake.calls[1]["messages"]
        tool_results = follow_up[-1]["content"]
        assert follow_up[-1]["role"] == "user"
        assert [block["tool_use_id"] for block in tool_results] == ["t1", "t2"]

    async def test_a_failing_search_is_reported_back_as_a_tool_error_not_a_crash(self):
        class BrokenSearch:
            async def search(self, query: str, limit: int = 6):
                raise SearchError("upstream is down")

        agent, fake = build_agent(
            [
                message(tool_use("t1", "web_search", {"query": "Acme"})),
                message(
                    tool_use("t2", "finish_research", {"company_name": "Acme", "researchable": True, "note": ""})
                ),
            ],
            search=BrokenSearch(),
        )
        context = await agent.gather("Acme", observer([]))

        result_block = fake.calls[1]["messages"][-1]["content"][0]
        assert result_block["is_error"] is True
        # No evidence at all, so we say so rather than invent a briefing.
        assert context.researchable is False

    async def test_gibberish_is_marked_unresearchable_by_the_agent(self):
        agent, _ = build_agent(
            [
                message(
                    tool_use("t1", "web_search", {"query": "qwertyuiop asdf"}),
                ),
                message(
                    tool_use(
                        "t2",
                        "finish_research",
                        {"company_name": "qwertyuiop", "researchable": False, "note": "Not a company."},
                    )
                ),
            ],
            search=StaticSearchClient([], delay=0),
        )
        context = await agent.gather("qwertyuiop asdf", observer([]))

        assert context.researchable is False
        assert context.note == "Not a company."

    async def test_the_loop_is_bounded_so_a_confused_model_cannot_run_forever(self):
        agent, fake = build_agent(
            [message(tool_use(f"t{i}", "web_search", {"query": f"q{i}"})) for i in range(4)]
        )
        await agent.gather("Acme", observer([]))
        assert len(fake.calls) == 4  # max_turns, not one more

    async def test_both_tools_are_offered_on_every_turn(self):
        agent, fake = build_agent(
            [message(tool_use("t1", "finish_research", {"company_name": "Acme", "researchable": True, "note": ""}))],
        )
        await agent.gather("Acme", observer([]))
        assert [tool["name"] for tool in fake.calls[0]["tools"]] == ["web_search", "finish_research"]


class TestSectionStreaming:
    context = ResearchContext(
        company="Acme",
        results=[SearchResult(title="T", url="https://x.test", snippet="S")],
    )

    async def test_prose_sections_stream_as_text_fragments(self):
        agent, fake = build_agent([])
        fake.stream_text = ["Acme ", "makes ", "widgets."]

        chunks = [c async for c in agent.stream_section("overview", self.context)]

        assert chunks == [TextDelta("Acme "), TextDelta("makes "), TextDelta("widgets.")]

    async def test_list_sections_emit_each_entry_as_its_line_completes(self):
        agent, fake = build_agent([])
        fake.stream_text = ['{"name": "Ada", "title": "CEO"}\n{"name": "Grace",', ' "title": "CTO"}']

        chunks = [c async for c in agent.stream_section("key_people", self.context)]

        assert chunks == [
            ItemChunk({"name": "Ada", "title": "CEO"}),
            ItemChunk({"name": "Grace", "title": "CTO"}),
        ]

    async def test_financials_come_back_whole_and_schema_validated(self):
        agent, fake = build_agent([])
        fake.parsed = Financials(revenue="$4.2B", employee_count="8,000", market_cap=None, yoy_growth="18%")

        chunks = [c async for c in agent.stream_section("financials", self.context)]

        assert chunks == [
            ValueChunk(
                {"revenue": "$4.2B", "employee_count": "8,000", "market_cap": None, "yoy_growth": "18%"}
            )
        ]
        assert fake.parse_calls[0]["output_format"] is Financials

    async def test_a_refused_or_empty_structured_response_yields_empty_financials(self):
        agent, fake = build_agent([])
        fake.parsed = None

        chunks = [c async for c in agent.stream_section("financials", self.context)]

        assert chunks == [ValueChunk({"revenue": None, "employee_count": None, "market_cap": None, "yoy_growth": None})]

    @pytest.mark.parametrize("section", ["overview", "key_people", "news", "risks", "financials"])
    async def test_every_section_prompt_carries_the_gathered_evidence(self, section):
        agent, fake = build_agent([])
        fake.stream_text = []
        fake.parsed = Financials()

        [c async for c in agent.stream_section(section, self.context)]

        call = (fake.calls + fake.parse_calls)[0]
        prompt = call["messages"][0]["content"]
        assert "https://x.test" in prompt and "Acme" in prompt
