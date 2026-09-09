"""The research agent, backed by Gemini and its built-in Google Search tool.

Same two phases as the Anthropic agent, and the same `ResearchAgent` protocol,
so the pipeline, the SSE contract, and the frontend are all unchanged:

1. `gather` makes one grounded call. Google Search runs inside the model, so the
   queries it chooses arrive as `google_search_call` deltas mid-stream and are
   forwarded to the UI live. The response is a schema-constrained digest, and
   the URL citations become the report's sources.
2. `stream_section` writes each section from that digest, ungrounded -- prose
   streams as text, list sections stream as JSON Lines, financials come back
   schema-validated.

Only phase 1 is a grounded request, which is what the free tier meters, so one
briefing costs one grounded prompt.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field, ValidationError

from ..schemas import Financials, Section
from .base import (
    AgentError,
    ItemChunk,
    ResearchContext,
    SearchObserver,
    SectionChunk,
    TextDelta,
    ValueChunk,
)
from .jsonl import JsonLineBuffer
from .prompts import (
    GEMINI_RESEARCH_SYSTEM,
    RESEARCH_TASK,
    WRITER_SYSTEM,
    section_prompt,
    system_prompt,
)
from .search import SearchResult

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_TOOL: dict[str, Any] = {"type": "google_search"}

RESEARCH_MAX_TOKENS = 8_000
SECTION_MAX_TOKENS = 2_000

PROSE_SECTIONS: frozenset[str] = frozenset({"overview"})
LIST_SECTIONS: frozenset[str] = frozenset({"key_people", "news", "risks"})


class ResearchDigest(BaseModel):
    """The structured result of the search phase."""

    company_name: str = Field(description="The company's canonical name, typos corrected.")
    researchable: bool = Field(
        description="False if this is not a real, identifiable company.",
    )
    note: str = Field(description="If not researchable, a short human-readable reason.")
    findings: str = Field(
        description=(
            "Dense notes on everything found: what the company does, named executives "
            "and titles, developments from the last 12 months with dates, revenue, "
            "headcount, market cap, growth, and any risks. Facts only."
        )
    )


class GeminiResearchAgent:
    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    # ------------------------------------------------------------------ phase 1

    async def gather(self, company: str, on_search: SearchObserver) -> ResearchContext:
        context = ResearchContext(company=company)
        text: list[str] = []
        seen_queries: set[str] = set()

        async for kind, payload in self._stream(
            system_instruction=system_prompt(GEMINI_RESEARCH_SYSTEM),
            prompt=RESEARCH_TASK.format(company=company),
            tools=[GOOGLE_SEARCH_TOOL],
            response_schema=ResearchDigest,
            max_tokens=RESEARCH_MAX_TOKENS,
            thinking="medium",
        ):
            if kind == "text":
                text.append(payload)
            elif kind == "queries":
                # The model decides what to search for; show the rep as it happens.
                for query in payload:
                    if query and query not in seen_queries:
                        seen_queries.add(query)
                        context.queries.append(query)
                        await on_search(query)
            elif kind == "citations":
                context.results.extend(payload)

        digest = _parse_digest("".join(text))
        if digest is None:
            # Grounded output that will not parse is not worth guessing at.
            context.researchable = False
            context.note = "The research service returned an unreadable response."
            return context

        context.company = digest.company_name.strip() or company
        context.researchable = digest.researchable
        context.note = digest.note
        context.findings = digest.findings

        if not context.findings.strip():
            context.researchable = False
            context.note = context.note or "No search results matched that name."

        return context

    # ------------------------------------------------------------------ phase 2

    async def stream_section(
        self, section: Section, context: ResearchContext
    ) -> AsyncIterator[SectionChunk]:
        prompt = section_prompt(section, context.company, context.corpus())
        params = {
            "system_instruction": system_prompt(WRITER_SYSTEM),
            "prompt": prompt,
            "max_tokens": SECTION_MAX_TOKENS,
            # The judgment happened in phase 1. Writing from evidence already
            # gathered is a shallow task, and the rep is waiting.
            "thinking": "low",
        }

        if section in PROSE_SECTIONS:
            async for kind, payload in self._stream(**params):
                if kind == "text":
                    yield TextDelta(payload)

        elif section in LIST_SECTIONS:
            buffer = JsonLineBuffer()
            async for kind, payload in self._stream(**params):
                if kind == "text":
                    for value in buffer.feed(payload):
                        yield ItemChunk(value)
            for value in buffer.flush():
                yield ItemChunk(value)

        else:
            text: list[str] = []
            async for kind, payload in self._stream(**params, response_schema=Financials):
                if kind == "text":
                    text.append(payload)
            yield ValueChunk(_parse_financials("".join(text)).model_dump())

    # ------------------------------------------------------------------ transport

    async def _stream(
        self,
        *,
        system_instruction: str,
        prompt: str,
        max_tokens: int,
        thinking: str,
        tools: list[dict[str, Any]] | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Run one interaction and normalise its SSE events.

        Yields ("text", str), ("queries", list[str]) and ("citations",
        list[SearchResult]) so callers never touch the provider's event shapes.
        """
        request: dict[str, Any] = {
            "model": self._model,
            "input": prompt,
            "system_instruction": system_instruction,
            "stream": True,
            # Nothing here needs server-side conversation state, so opt out of
            # having the interaction retained.
            "store": False,
            "generation_config": {
                "max_output_tokens": max_tokens,
                "thinking_level": thinking,
            },
        }
        if tools:
            request["tools"] = tools
        if response_schema is not None:
            request["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema.model_json_schema(),
            }

        try:
            stream = await self._client.aio.interactions.create(**request)
            async for event in stream:
                for item in _interpret(event):
                    yield item
        except genai_errors.APIError as exc:
            raise _as_agent_error(exc) from exc


def _interpret(event: Any) -> list[tuple[str, Any]]:
    """Translate one SSE event into zero or more normalised chunks."""
    event_type = getattr(event, "event_type", None)

    if event_type == "step.delta":
        delta = getattr(event, "delta", None)
        delta_type = getattr(delta, "type", None)
        if delta_type == "text":
            return [("text", delta.text)]
        if delta_type == "google_search_call":
            queries = getattr(getattr(delta, "arguments", None), "queries", None)
            return [("queries", list(queries))] if queries else []
        return []

    if event_type == "interaction.completed":
        sources = _citations(getattr(event, "interaction", None))
        return [("citations", sources)] if sources else []

    if event_type == "error":
        raise AgentError("The research service reported an error. Please try again.")

    return []


def _citations(interaction: Any) -> list[SearchResult]:
    """Pull the grounding citations out of a completed interaction.

    Streaming lifecycle payloads may omit fields, so every hop is guarded --
    missing sources cost a footer, not the report.
    """
    found: dict[str, SearchResult] = {}
    for step in getattr(interaction, "steps", None) or []:
        for block in getattr(step, "content", None) or []:
            for annotation in getattr(block, "annotations", None) or []:
                url = getattr(annotation, "url", None)
                if getattr(annotation, "type", None) == "url_citation" and url:
                    found.setdefault(
                        url,
                        SearchResult(
                            title=getattr(annotation, "title", None) or url,
                            url=url,
                            snippet="Cited by Google Search grounding.",
                        ),
                    )
    return list(found.values())


def _parse_digest(text: str) -> ResearchDigest | None:
    try:
        return ResearchDigest.model_validate_json(text)
    except ValidationError:
        pass
    # A schema-constrained response should already be clean JSON; this is the
    # belt-and-braces path for a model that wrapped it in prose or a fence.
    extracted = _extract_json_object(text)
    if extracted is None:
        logger.warning("Could not parse research digest from %r", text[:200])
        return None
    try:
        return ResearchDigest.model_validate(extracted)
    except ValidationError:
        logger.warning("Research digest failed validation: %r", extracted)
        return None


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


def _as_agent_error(exc: genai_errors.APIError) -> AgentError:
    """Turn provider failures into something a sales rep can act on."""
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status == 429:
        return AgentError(
            "We've hit today's free research limit. Try again later, or check the "
            "quota in Google AI Studio."
        )
    if status in (401, 403):
        return AgentError("The research service rejected our credentials. Check GEMINI_API_KEY.")
    logger.exception("Gemini request failed")
    return AgentError("The research service failed unexpectedly. Try again.")
