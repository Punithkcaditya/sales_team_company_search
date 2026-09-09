"""The research agent, backed by Gemini plus an explicit web search tool.

Gemini has a built-in `google_search` tool, which would remove the need for a
separate search provider -- but it has no free-tier quota, so a free-tier key is
refused with a 429 the moment it is used. Search therefore runs client-side
through Serper (Google results as JSON), handed to the model as a `web_search`
function it can call. That keeps the whole app on free tiers, and makes the
research loop something you can read rather than something hidden in the model.

Two phases, the same `ResearchAgent` protocol as the Anthropic agent, so the
pipeline, the SSE contract, and the frontend are unchanged:

1. `gather` runs the tool loop. Gemini decides what to search for; each query is
   forwarded to the UI as it is issued, and the loop ends when the model calls
   `finish_research`.
2. `stream_section` writes each section from the gathered evidence -- prose
   streams as text, list sections stream as JSON Lines, financials come back
   schema-validated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai._gaos.errors import GenAiError as InteractionAPIError

# The interactions transport raises from its own parallel hierarchy: these are
# NOT subclasses of genai.errors.APIError or GenAiError, so catching only those
# lets a 429 escape as an unhandled exception.
from google.genai._gaos.lib.compat_errors import APIError as InteractionTransportError
from pydantic import BaseModel, ValidationError

from ..schemas import Financials, Section
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

RESEARCH_MAX_TOKENS = 8_000
SECTION_MAX_TOKENS = 2_000

PROSE_SECTIONS: frozenset[str] = frozenset({"overview"})
LIST_SECTIONS: frozenset[str] = frozenset({"key_people", "news", "risks"})


def as_gemini_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Re-shape a tool definition for Gemini's function-calling format.

    The tools themselves are declared once in `prompts.py`; only the envelope
    differs between providers, so the wording stays in one place. Gemini's
    schema dialect rejects `additionalProperties`, so it is dropped.
    """
    schema = {k: v for k, v in tool["input_schema"].items() if k != "additionalProperties"}
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": schema,
    }


RESEARCH_TOOLS = [as_gemini_tool(WEB_SEARCH_TOOL), as_gemini_tool(FINISH_RESEARCH_TOOL)]


class GeminiResearchAgent:
    def __init__(
        self,
        client: genai.Client,
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
        payload: Any = RESEARCH_TASK.format(company=company)
        previous_id: str | None = None

        for _ in range(self._max_turns):
            interaction = await self._create(
                input=payload,
                system_instruction=system_prompt(RESEARCH_SYSTEM),
                tools=RESEARCH_TOOLS,
                max_tokens=RESEARCH_MAX_TOKENS,
                thinking="medium",
                previous_interaction_id=previous_id,
            )
            previous_id = getattr(interaction, "id", None)

            calls = [
                step
                for step in (getattr(interaction, "steps", None) or [])
                if getattr(step, "type", None) == "function_call"
            ]
            if not calls:
                break

            searches = [call for call in calls if call.name == "web_search"]
            finish = next((call for call in calls if call.name == "finish_research"), None)

            if searches:
                payload = await self._run_searches(searches, context, on_search)

            if finish:
                _apply_finish(context, finish.arguments or {})
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
        """Run this turn's searches concurrently and return one result per call."""
        queries = [str((call.arguments or {}).get("query", "")).strip() for call in calls]
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
            results.append(
                {
                    "type": "function_result",
                    "name": call.name,
                    "call_id": call.id,
                    "result": [{"type": "text", "text": body}],
                }
            )
        return results

    async def _search_one(self, query: str) -> list[SearchResult]:
        if not query:
            raise SearchError("Empty query")
        return await self._search.search(query)

    # ------------------------------------------------------------------ phase 2

    async def stream_section(
        self, section: Section, context: ResearchContext
    ) -> AsyncIterator[SectionChunk]:
        params = {
            "system_instruction": system_prompt(WRITER_SYSTEM),
            "prompt": section_prompt(section, context.company, context.corpus()),
            "max_tokens": SECTION_MAX_TOKENS,
            # The judgment happened in phase 1. Writing from evidence already
            # gathered is a shallow task, and the rep is waiting.
            "thinking": "low",
        }

        if section in PROSE_SECTIONS:
            async for text in self._stream_text(**params):
                yield TextDelta(text)

        elif section in LIST_SECTIONS:
            buffer = JsonLineBuffer()
            async for text in self._stream_text(**params):
                for value in buffer.feed(text):
                    yield ItemChunk(value)
            for value in buffer.flush():
                yield ItemChunk(value)

        else:
            collected: list[str] = []
            async for text in self._stream_text(**params, response_schema=Financials):
                collected.append(text)
            yield ValueChunk(_parse_financials("".join(collected)).model_dump())

    # ------------------------------------------------------------------ transport

    async def _create(
        self,
        *,
        input: Any,
        system_instruction: str,
        max_tokens: int,
        thinking: str,
        tools: list[dict[str, Any]] | None = None,
        previous_interaction_id: str | None = None,
    ) -> Any:
        """One non-streaming interaction, for the tool loop.

        The loop needs conversation state across turns, which `store` provides
        via `previous_interaction_id` rather than resending the transcript.
        """
        request: dict[str, Any] = {
            "model": self._model,
            "input": input,
            "system_instruction": system_instruction,
            "store": True,
            "generation_config": {
                "max_output_tokens": max_tokens,
                "thinking_level": thinking,
            },
        }
        if tools:
            request["tools"] = tools
        if previous_interaction_id:
            request["previous_interaction_id"] = previous_interaction_id

        for attempt in range(_MAX_ATTEMPTS):
            try:
                return await self._client.aio.interactions.create(**request)
            except (
                genai_errors.APIError,
                InteractionAPIError,
                InteractionTransportError,
            ) as exc:
                if await _wait_to_retry(exc, attempt):
                    continue
                raise _as_agent_error(exc) from exc
        raise _quota_error()

    async def _stream_text(
        self,
        *,
        system_instruction: str,
        prompt: str,
        max_tokens: int,
        thinking: str,
        response_schema: type[BaseModel] | None = None,
    ) -> AsyncIterator[str]:
        """Text fragments from one streamed interaction. Section writing needs no
        tools and no history, so nothing is stored.

        A long-lived streaming connection can drop before it delivers anything.
        That is retried once -- but only while nothing has been emitted yet,
        since replaying a half-written section would duplicate text on screen.
        """
        request: dict[str, Any] = {
            "model": self._model,
            "input": prompt,
            "system_instruction": system_instruction,
            "stream": True,
            "store": False,
            "generation_config": {
                "max_output_tokens": max_tokens,
                "thinking_level": thinking,
            },
        }
        if response_schema is not None:
            request["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema.model_json_schema(),
            }

        for attempt in range(_MAX_ATTEMPTS):
            emitted = False
            try:
                stream = await self._client.aio.interactions.create(**request)
                async for event in stream:
                    text = _text_of(event)
                    if text:
                        emitted = True
                        yield text
                return
            except (
                genai_errors.APIError,
                InteractionAPIError,
                InteractionTransportError,
            ) as exc:
                # Replaying a half-written section would duplicate text on
                # screen, so only a stream that produced nothing may be retried.
                if not emitted and await _wait_to_retry(exc, attempt):
                    continue
                raise _as_agent_error(exc) from exc
        raise _quota_error()


def _text_of(event: Any) -> str | None:
    """The text fragment in one SSE event, if it carries one."""
    event_type = getattr(event, "event_type", None)

    if event_type == "step.delta":
        delta = getattr(event, "delta", None)
        return delta.text if getattr(delta, "type", None) == "text" else None

    if event_type == "error":
        error = getattr(event, "error", None)
        code = str(getattr(error, "code", "") or "").lower().rstrip("/").rsplit("/", 1)[-1]
        if code in {"quota_exceeded", "rate_limit_exceeded", "too_many_requests", "resource_exhausted", "429"}:
            raise _quota_error()
        raise AgentError("The research service reported an error. Please try again.")

    return None


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


def _parse_financials(text: str) -> Financials:
    """Empty financials beat wrong ones, so every failure path returns blanks."""
    try:
        return Financials.model_validate_json(text)
    except ValidationError:
        pass
    extracted = _extract_json_object(text)
    if extracted is not None:
        try:
            return Financials.model_validate(extracted)
        except ValidationError:
            pass
    logger.warning("Financials failed validation: %r", text[:200])
    return Financials()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_MAX_ATTEMPTS = 3
_MAX_BACKOFF_SECONDS = 30.0
# The free tier's 429 carries its own cooldown: "Please retry in 17.47s".
_RETRY_HINT = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)


async def _wait_to_retry(exc: Exception, attempt: int) -> bool:
    """Sleep and report whether the call is worth another attempt.

    The free tier meters requests per minute, so a 429 is a pause rather than a
    dead end -- and the server states how long to wait. Honouring that turns a
    burst of section writes into a short delay instead of a failed briefing.
    """
    if attempt >= _MAX_ATTEMPTS - 1:
        return False

    delay: float | None = None
    if _status_of(exc) == 429:
        hint = _RETRY_HINT.search(str(exc))
        delay = min(float(hint.group(1)) + 0.5, _MAX_BACKOFF_SECONDS) if hint else 5.0
    elif _is_transient(exc):
        delay = 1.0 * (attempt + 1)

    if delay is None:
        return False

    logger.warning("Retrying in %.1fs after: %s", delay, str(exc)[:120])
    await asyncio.sleep(delay)
    return True


def _is_transient(exc: Exception) -> bool:
    """A dropped connection or a 5xx is worth one more attempt; a 429 is not."""
    status = _status_of(exc)
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError"} or (
        status is not None and status >= 500
    )


def _as_agent_error(exc: Exception) -> AgentError:
    """Turn provider failures into something a sales rep can act on."""
    status = _status_of(exc)
    if status == 429:
        return _quota_error()
    if status in (401, 403):
        return AgentError("The research service rejected our credentials. Check GEMINI_API_KEY.")
    if _is_transient(exc):
        return AgentError("Lost the connection to the research service. Please try again.")
    logger.exception("Gemini request failed")
    return AgentError("The research service failed unexpectedly. Try again.")


def _status_of(exc: Exception) -> int | None:
    """The HTTP status, wherever this SDK's several error types keep it.

    `genai.errors.APIError` uses `code`; the interactions transport uses
    `status_code`. Either may be absent or non-numeric.
    """
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _quota_error() -> QuotaExceededError:
    return QuotaExceededError(
        "Gemini's free tier is rate limited and we have hit it. It resets within a "
        "minute -- wait a moment and try again."
    )
