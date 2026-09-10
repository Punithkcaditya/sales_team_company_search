"""The research agent, backed by Groq.

Identical in shape to the Gemini agent, and for the same reason: search runs
client-side through Serper and is handed to the model as a `web_search`
function, so swapping the model provider touches nothing but transport. The
prompts, the tool definitions, the section parser, and the streaming contract
are all shared.

Groq exposes an OpenAI-compatible chat API, so the differences from Gemini are
mechanical: tools nest under a "function" key, tool arguments arrive as a JSON
string rather than an object, and results go back as `role: "tool"` messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import groq

from ..schemas import Section
from . import backoff
from .base import (
    AgentError,
    QuotaExceededError,
    ResearchContext,
    SearchObserver,
    SectionChunk,
)
from .prompts import (
    FINISH_RESEARCH_TOOL,
    RESEARCH_SYSTEM,
    RESEARCH_TASK,
    WEB_SEARCH_TOOL,
    WRITER_SYSTEM,
    system_prompt,
    write_all_prompt,
)
from .search import SearchClient, SearchError, SearchResult
from .section_stream import SectionStreamParser

logger = logging.getLogger(__name__)

RESEARCH_MAX_TOKENS = 4_000
# Real source URLs are long, and five sections of them add up: too small a
# budget truncates the briefing before the last section is written.
BRIEFING_MAX_TOKENS = 8_000


def as_groq_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Re-shape a shared tool definition into OpenAI-compatible form.

    The tools are declared once in `prompts.py`; only the envelope differs
    between providers, so the wording stays in one place.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


RESEARCH_TOOLS = [as_groq_tool(WEB_SEARCH_TOOL), as_groq_tool(FINISH_RESEARCH_TOOL)]


class GroqResearchAgent:
    def __init__(
        self,
        client: groq.AsyncGroq,
        search: SearchClient,
        model: str,
        writer_model: str | None = None,
        max_turns: int = 6,
        max_searches: int = 10,
    ) -> None:
        self._client = client
        self._search = search
        self._model = model
        self._writer_model = writer_model or model
        self._max_turns = max_turns
        self._max_searches = max_searches

    # ------------------------------------------------------------------ phase 1

    async def gather(self, company: str, on_search: SearchObserver) -> ResearchContext:
        context = ResearchContext(company=company)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(RESEARCH_SYSTEM)},
            {"role": "user", "content": RESEARCH_TASK.format(company=company)},
        ]

        for _ in range(self._max_turns):
            message = await self._complete(
                messages=messages, tools=RESEARCH_TOOLS, max_tokens=RESEARCH_MAX_TOKENS
            )
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                break

            messages.append(_as_message(message))

            searches = [c for c in calls if c.function.name == "web_search"]
            finish = next((c for c in calls if c.function.name == "finish_research"), None)

            if searches:
                messages.extend(await self._run_searches(searches, context, on_search))

            if finish:
                _apply_finish(context, _arguments(finish))
                break

            if not searches or len(context.queries) >= self._max_searches:
                break
        else:
            logger.warning("Research loop hit the turn limit for %r", company)

        if not context.results:
            # No evidence at all. Say so rather than write a briefing from nothing.
            context.researchable = False
            context.note = context.note or "No search results matched that name."

        return context

    async def _run_searches(
        self, calls: list[Any], context: ResearchContext, on_search: SearchObserver
    ) -> list[dict[str, Any]]:
        """Run this turn's searches concurrently, one tool message per call."""
        queries = [str(_arguments(call).get("query", "")).strip() for call in calls]
        for query in queries:
            if query:
                context.queries.append(query)
                await on_search(query)

        outcomes = await asyncio.gather(
            *(self._search_one(query) for query in queries), return_exceptions=True
        )

        results: list[dict[str, Any]] = []
        for call, query, outcome in zip(calls, queries, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning("Search failed for %r: %s", query, outcome)
                body = f"Search failed for {query!r}. Try a different query."
            else:
                context.results.extend(outcome)
                body = _format_results(query, outcome)
            results.append({"role": "tool", "tool_call_id": call.id, "content": body})
        return results

    async def _search_one(self, query: str) -> list[SearchResult]:
        if not query:
            raise SearchError("Empty query")
        return await self._search.search(query)

    # ------------------------------------------------------------------ phase 2

    async def write(self, context: ResearchContext) -> AsyncIterator[tuple[Section, SectionChunk]]:
        """All five sections from one streamed request."""
        parser = SectionStreamParser()
        messages = [
            {"role": "system", "content": system_prompt(WRITER_SYSTEM)},
            {"role": "user", "content": write_all_prompt(context.company, context.corpus())},
        ]

        async for text in self._stream(messages, self._writer_model, BRIEFING_MAX_TOKENS):
            for item in parser.feed(text):
                yield item

        for item in parser.flush():
            yield item

    # ------------------------------------------------------------------ transport

    async def _complete(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int
    ) -> Any:
        for attempt in range(backoff.MAX_ATTEMPTS):
            started = time.monotonic()
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_completion_tokens=max_tokens,
                    temperature=0.2,
                )
                logger.info("groq %s call took %.1fs", self._model, time.monotonic() - started)
                return response.choices[0].message
            except groq.APIError as exc:
                if await backoff.wait_to_retry(exc, attempt):
                    continue
                raise _as_agent_error(exc) from exc
        raise _quota_error()

    async def _stream(
        self, messages: list[dict[str, Any]], model: str, max_tokens: int
    ) -> AsyncIterator[str]:
        """Text fragments from one streamed completion.

        Retried only while nothing has been emitted -- replaying a half-written
        briefing would duplicate text on screen.
        """
        for attempt in range(backoff.MAX_ATTEMPTS):
            emitted = False
            started = time.monotonic()
            try:
                stream = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_completion_tokens=max_tokens,
                    temperature=0.3,
                    stream=True,
                )
                async for chunk in stream:
                    text = _delta_of(chunk)
                    if text:
                        emitted = True
                        yield text
                logger.info("groq %s stream took %.1fs", model, time.monotonic() - started)
                return
            except groq.APIError as exc:
                if not emitted and await backoff.wait_to_retry(exc, attempt):
                    continue
                raise _as_agent_error(exc) from exc
        raise _quota_error()


def _delta_of(chunk: Any) -> str | None:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return None
    return getattr(choices[0].delta, "content", None)


def _as_message(message: Any) -> dict[str, Any]:
    """The assistant turn, in the shape the API accepts back."""
    if hasattr(message, "model_dump"):
        return {k: v for k, v in message.model_dump(exclude_none=True).items() if v != []}
    return dict(message)


def _arguments(call: Any) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string, and a model can garble them."""
    raw = getattr(call.function, "arguments", "") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Unparseable tool arguments: %r", raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_finish(context: ResearchContext, payload: dict[str, Any]) -> None:
    name = str(payload.get("company_name") or "").strip()
    context.company = name or context.company
    context.researchable = bool(payload.get("researchable", True))
    context.note = str(payload.get("note") or "")


def _format_results(query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"No results for {query!r}."
    body = "\n".join(result.as_context() for result in results)
    return f"Results for {query!r}:\n{body}"


def _as_agent_error(exc: groq.APIError) -> AgentError:
    """Turn provider failures into something a sales rep can act on."""
    status = backoff.status_of(exc)
    if status == 429:
        return _quota_error()
    if status in (401, 403):
        return AgentError("The research service rejected our credentials. Check GROQ_API_KEY.")
    if backoff.is_transient(exc):
        return AgentError("Lost the connection to the research service. Please try again.")
    logger.exception("Groq request failed")
    return AgentError("The research service failed unexpectedly. Try again.")


def _quota_error() -> QuotaExceededError:
    return QuotaExceededError(
        "The model's free tier is rate limited and we have hit it. Wait a moment "
        "and try again."
    )
