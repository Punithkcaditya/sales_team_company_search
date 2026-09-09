"""The research agent, backed by Claude and live Google search.

Two phases, deliberately separated:

1. `gather` runs an agentic tool loop -- Claude decides what to search for and
   keeps going until it calls `finish_research`. This is where the research
   happens; nothing is written yet.
2. `stream_section` writes one section at a time from the gathered evidence, so
   the rep watches the briefing fill in top to bottom instead of staring at a
   spinner. Prose sections stream as text; list sections stream one item at a
   time; financials come back as a single validated object.

Splitting them keeps the writing prompts small and grounded, and means a slow
search never blocks the first words appearing on screen.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import anthropic

from ..schemas import SECTION_ORDER, Financials, Section
from .base import (
    AgentError,
    ItemChunk,
    QuotaExceededError,
    ResearchContext,
    SearchObserver,
    SectionChunk,
    TextDelta,
    ValueChunk,
)
from .jsonl import JsonLineBuffer
from .prompts import (
    FINISH_RESEARCH_TOOL,
    RESEARCH_SYSTEM,
    RESEARCH_TASK,
    WEB_SEARCH_TOOL,
    WRITER_SYSTEM,
    section_prompt,
    system_prompt,
)
from .search import SearchClient, SearchError, SearchResult

logger = logging.getLogger(__name__)

RESEARCH_MAX_TOKENS = 16_000
SECTION_MAX_TOKENS = 4_000

# Which sections stream as prose, as a list, or as a single structured object.
PROSE_SECTIONS: frozenset[str] = frozenset({"overview"})
LIST_SECTIONS: frozenset[str] = frozenset({"key_people", "news", "risks"})


class AnthropicResearchAgent:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        search: SearchClient,
        model: str,
        max_turns: int = 6,
        max_searches: int = 10,
    ) -> None:
        self._client = client
        self._search = search
        self._model = model
        self._max_turns = max_turns
        self._max_searches = max_searches

    # ------------------------------------------------------------------ phase 1

    async def gather(self, company: str, on_search: SearchObserver) -> ResearchContext:
        context = ResearchContext(company=company)
        messages: list[dict] = [{"role": "user", "content": RESEARCH_TASK.format(company=company)}]

        for _ in range(self._max_turns):
            response = await self._create(
                max_tokens=RESEARCH_MAX_TOKENS,
                system=system_prompt(RESEARCH_SYSTEM),
                tools=[WEB_SEARCH_TOOL, FINISH_RESEARCH_TOOL],
                messages=messages,
            )

            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})

            searches = [t for t in tool_uses if t.name == "web_search"]
            finish = next((t for t in tool_uses if t.name == "finish_research"), None)

            if searches:
                results = await self._run_searches(searches, context, on_search)
                messages.append({"role": "user", "content": results})

            if finish:
                _apply_finish(context, finish.input)
                break

            if len(context.queries) >= self._max_searches:
                break
        else:
            logger.warning("Research loop hit the turn limit for %r", company)

        if not context.results:
            # Nothing came back at all. Rather than write a briefing out of thin
            # air, tell the rep we could not find the company.
            context.researchable = False
            context.note = context.note or "No search results matched that name."

        return context

    async def _run_searches(
        self,
        tool_uses: list,
        context: ResearchContext,
        on_search: SearchObserver,
    ) -> list[dict]:
        """Run the turn's searches concurrently, one tool_result per call.

        All results go back in a single user message -- splitting them across
        messages teaches the model to stop batching its calls.
        """
        queries = [str(tool.input.get("query", "")).strip() for tool in tool_uses]
        for query in queries:
            if query:
                context.queries.append(query)
                await on_search(query)

        outcomes = await asyncio.gather(
            *(self._search_one(query) for query in queries), return_exceptions=True
        )

        tool_results: list[dict] = []
        for tool, query, outcome in zip(tool_uses, queries, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning("Search failed for %r: %s", query, outcome)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool.id,
                        "content": f"Search failed for {query!r}. Try a different query.",
                        "is_error": True,
                    }
                )
                continue

            context.results.extend(outcome)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool.id,
                    "content": _format_results(query, outcome),
                }
            )
        return tool_results

    async def _search_one(self, query: str) -> list[SearchResult]:
        if not query:
            raise SearchError("Empty query")
        return await self._search.search(query)

    # ------------------------------------------------------------------ phase 2

    async def write(self, context: ResearchContext) -> AsyncIterator[tuple[Section, SectionChunk]]:
        """One call per section.

        Claude is not on a free-tier request budget here, so the extra round
        trips buy tighter, better-grounded prompts rather than costing quota.
        """
        for section in SECTION_ORDER:
            async for chunk in self._stream_one(section, context):
                yield section, chunk

    async def _stream_one(
        self, section: Section, context: ResearchContext
    ) -> AsyncIterator[SectionChunk]:
        prompt = section_prompt(section, context.company, context.corpus())

        if section in PROSE_SECTIONS:
            async for chunk in self._stream_text(prompt):
                yield chunk
        elif section in LIST_SECTIONS:
            async for chunk in self._stream_items(prompt):
                yield chunk
        else:
            yield await self._structured_financials(prompt)

    async def _stream_text(self, prompt: str) -> AsyncIterator[SectionChunk]:
        try:
            async with self._client.messages.stream(**self._writer_params(prompt)) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
        except anthropic.APIError as exc:
            raise _as_agent_error(exc) from exc

    async def _stream_items(self, prompt: str) -> AsyncIterator[SectionChunk]:
        buffer = JsonLineBuffer()
        try:
            async with self._client.messages.stream(**self._writer_params(prompt)) as stream:
                async for text in stream.text_stream:
                    for value in buffer.feed(text):
                        yield ItemChunk(value)
        except anthropic.APIError as exc:
            raise _as_agent_error(exc) from exc

        for value in buffer.flush():
            yield ItemChunk(value)

    async def _structured_financials(self, prompt: str) -> SectionChunk:
        """The one section where a half-rendered value is worse than a late one,
        so it arrives whole and schema-validated."""
        try:
            response = await self._client.messages.parse(
                output_format=Financials, **self._writer_params(prompt)
            )
        except anthropic.APIError as exc:
            raise _as_agent_error(exc) from exc
        return ValueChunk((response.parsed_output or Financials()).model_dump())

    def _writer_params(self, prompt: str) -> dict:
        return {
            "model": self._model,
            "max_tokens": SECTION_MAX_TOKENS,
            "system": system_prompt(WRITER_SYSTEM),
            # The judgment happened in phase 1. Writing from supplied evidence is
            # a low-effort task, and the rep is waiting.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": prompt}],
        }

    async def _create(self, **kwargs):
        try:
            return await self._client.messages.create(
                model=self._model, thinking={"type": "adaptive"}, **kwargs
            )
        except anthropic.APIError as exc:
            raise _as_agent_error(exc) from exc


def _apply_finish(context: ResearchContext, payload: dict) -> None:
    name = str(payload.get("company_name") or "").strip()
    context.company = name or context.company
    context.researchable = bool(payload.get("researchable", True))
    context.note = str(payload.get("note") or "")


def _format_results(query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"No results for {query!r}."
    body = "\n".join(result.as_context() for result in results)
    return f"Results for {query!r}:\n{body}"


def _as_agent_error(exc: anthropic.APIError) -> AgentError:
    """Turn provider failures into something a sales rep can act on."""
    if isinstance(exc, anthropic.RateLimitError):
        return QuotaExceededError("The AI provider's request or token limit has been reached. Please try again later.")
    if isinstance(exc, anthropic.AuthenticationError):
        return AgentError("The research service rejected our credentials. Check the API key.")
    if isinstance(exc, anthropic.APIConnectionError):
        return AgentError("Could not reach the research service. Check your connection.")
    logger.exception("Anthropic request failed")
    return AgentError("The research service failed unexpectedly. Try again.")
