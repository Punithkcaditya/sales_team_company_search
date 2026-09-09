"""The parts of the real agent that do not need a live provider:
the JSON-Lines stream buffer and the Serper response parser."""

import httpx
import pytest

from app.agent.jsonl import JsonLineBuffer
from app.agent.search import SearchError, SerperSearchClient


class TestJsonLineBuffer:
    def test_objects_are_emitted_as_soon_as_their_line_completes(self):
        buffer = JsonLineBuffer()

        assert buffer.feed('{"name": "Ada"') == []
        assert buffer.feed(', "title": "CEO"}\n{"name": "Gr') == [{"name": "Ada", "title": "CEO"}]
        assert buffer.feed('ace", "title": "CTO"}') == []
        assert buffer.flush() == [{"name": "Grace", "title": "CTO"}]

    def test_trailing_content_without_a_newline_is_not_lost(self):
        buffer = JsonLineBuffer()
        buffer.feed('{"risk": "Antitrust review"}')
        assert buffer.flush() == [{"risk": "Antitrust review"}]

    @pytest.mark.parametrize(
        "text",
        [
            '```json\n{"name": "Ada", "title": "CEO"}\n```\n',  # fenced
            '{"name": "Ada", "title": "CEO"},\n',  # array-style trailing comma
            'Here you go:\n{"name": "Ada", "title": "CEO"}\n',  # preamble line
        ],
    )
    def test_common_model_formatting_slips_are_tolerated(self, text):
        buffer = JsonLineBuffer()
        assert buffer.feed(text) + buffer.flush() == [{"name": "Ada", "title": "CEO"}]

    def test_unparseable_lines_are_skipped_rather_than_raising(self):
        buffer = JsonLineBuffer()
        assert buffer.feed('{"broken": \n{"name": "Ada", "title": "CEO"}\n') == [
            {"name": "Ada", "title": "CEO"}
        ]


class TestSerperSearchClient:
    @staticmethod
    def client_returning(payload: dict, status_code: int = 200) -> SerperSearchClient:
        transport = httpx.MockTransport(lambda _: httpx.Response(status_code, json=payload))
        return SerperSearchClient("key", client=httpx.AsyncClient(transport=transport))

    async def test_news_comes_first_because_recency_is_what_makes_a_briefing_credible(self):
        search = self.client_returning(
            {
                "organic": [{"title": "About", "link": "https://x.test/about", "snippet": "Profile."}],
                "news": [
                    {
                        "title": "Acme acquires Foo",
                        "link": "https://x.test/news",
                        "snippet": "Deal closed.",
                        "date": "2 days ago",
                    }
                ],
            }
        )
        results = await search.search("Acme news")

        assert [r.title for r in results] == ["Acme acquires Foo", "About"]
        assert results[0].published == "2 days ago"

    async def test_the_knowledge_panel_is_folded_in_as_a_result(self):
        search = self.client_returning(
            {
                "knowledgeGraph": {
                    "title": "Acme Corp",
                    "description": "An industrial manufacturer.",
                    "website": "https://acme.test",
                    "attributes": {"Founded": "1952"},
                }
            }
        )
        results = await search.search("Acme")

        assert results[0].title == "Acme Corp"
        assert "Founded: 1952" in results[0].snippet

    async def test_results_missing_a_title_or_snippet_are_dropped(self):
        search = self.client_returning(
            {"organic": [{"link": "https://x.test/a", "snippet": "no title"}, {"title": "Real", "link": "https://x.test/b", "snippet": "ok"}]}
        )
        assert [r.title for r in await search.search("Acme")] == ["Real"]

    async def test_a_failing_search_raises_a_typed_error(self):
        search = self.client_returning({"message": "quota exceeded"}, status_code=429)
        with pytest.raises(SearchError):
            await search.search("Acme")
