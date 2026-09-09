"""Turns an agent's output into the stream of events the frontend renders.

This module owns ordering, validation, and persistence. It knows nothing about
Claude or Serper -- swap the agent and this is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, ValidationError

from .. import events
from ..events import ResearchEvent
from ..repository import ReportRepository
from ..schemas import (
    SECTION_ORDER,
    Financials,
    NewsItem,
    Person,
    ReportSections,
    RiskItem,
    Section,
)
from .base import AgentError, ItemChunk, QuotaExceededError, ResearchAgent, ResearchContext, TextDelta, ValueChunk

logger = logging.getLogger(__name__)

# How each list section validates, and how many entries are worth a rep's time.
# The brief asks for 3-4 news items and 2-3 risks; more is noise, not value.
_LIST_SECTIONS: dict[str, tuple[type[BaseModel], int]] = {
    "key_people": (Person, 6),
    "news": (NewsItem, 4),
    "risks": (RiskItem, 3),
}


class ResearchPipeline:
    def __init__(self, agent: ResearchAgent, repository: ReportRepository) -> None:
        self._agent = agent
        self._repository = repository

    async def run(self, company: str) -> AsyncIterator[ResearchEvent]:
        """Yield the full research stream. Abandoning this generator (the client
        navigated away, or hit cancel) tears the agent down and saves nothing."""
        yield events.status("searching", f"Researching {company}…")

        # `gather` is a coroutine, not a generator, so its progress reaches us
        # through a queue: the task pushes each query as the agent decides to run
        # it, we drain them into events, and the sentinel in `finally` releases
        # the drain whether the task succeeded or raised.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def gather_task() -> ResearchContext:
            try:
                return await self._agent.gather(company, queue.put)
            finally:
                await queue.put(None)

        task = asyncio.create_task(gather_task())
        try:
            while (query := await queue.get()) is not None:
                yield events.search(query)
            context = await task
        except AgentError as exc:
            yield events.error(exc.code, str(exc))
            return
        except Exception:
            logger.exception("Research failed for %r", company)
            yield events.error("agent_error", "Research failed unexpectedly. Please try again.")
            return
        finally:
            if not task.done():
                task.cancel()

        if not context.researchable:
            yield events.error(
                "not_found",
                context.note or f"We could not find a company called “{company}”.",
            )
            return

        yield events.status("writing", f"Writing the briefing on {context.company}…")

        sections = ReportSections()
        failures = 0
        for section in SECTION_ORDER:
            try:
                async for event in self._run_section(section, context, sections):
                    yield event
            except QuotaExceededError as exc:
                # Further section calls would consume attempts without fixing the quota.
                yield events.error(exc.code, str(exc))
                return
            except Exception:
                # One flaky section should not cost the rep the other four.
                logger.exception("Section %s failed for %r", section, context.company)
                failures += 1
                yield events.section_end(section, _section_value(sections, section))

        if failures == len(SECTION_ORDER):
            yield events.error(
                "agent_error",
                "The research service was unreachable, so no briefing could be written.",
            )
            return

        report = await asyncio.to_thread(
            self._repository.create, context.company, sections, context.sources()
        )
        yield events.done(report.model_dump())

    async def _run_section(
        self, section: Section, context: ResearchContext, sections: ReportSections
    ) -> AsyncIterator[ResearchEvent]:
        yield events.section_start(section)

        if section in _LIST_SECTIONS:
            async for event in self._run_list_section(section, context, sections):
                yield event
        elif section == "financials":
            async for chunk in self._agent.stream_section(section, context):
                if isinstance(chunk, ValueChunk):
                    sections.financials = _coerce(Financials, chunk.value) or Financials()
        else:
            text: list[str] = []
            async for chunk in self._agent.stream_section(section, context):
                if isinstance(chunk, TextDelta):
                    text.append(chunk.text)
                    yield events.section_delta(section, chunk.text)
            sections.overview = "".join(text).strip()

        yield events.section_end(section, _section_value(sections, section))

    async def _run_list_section(
        self, section: Section, context: ResearchContext, sections: ReportSections
    ) -> AsyncIterator[ResearchEvent]:
        model, limit = _LIST_SECTIONS[section]
        collected: list[BaseModel] = []

        async for chunk in self._agent.stream_section(section, context):
            if not isinstance(chunk, ItemChunk) or len(collected) >= limit:
                continue
            item = _coerce(model, chunk.value)
            if item is None:
                continue
            collected.append(item)
            yield events.section_item(section, item.model_dump())

        # Risks are stored as plain strings; the wrapper object exists only to
        # give the model a stable line format to emit.
        if section == "risks":
            sections.risks = [item.risk for item in collected]  # type: ignore[attr-defined]
        else:
            setattr(sections, section, collected)


def _coerce(model: type[BaseModel], value: object) -> BaseModel | None:
    """Validate one streamed value, dropping it if the model went off-script.

    Losing a single bullet is a far better outcome than losing the report.
    """
    try:
        return model.model_validate(value)
    except ValidationError:
        logger.warning("Discarded malformed %s payload: %r", model.__name__, value)
        return None


def _section_value(sections: ReportSections, section: Section) -> object:
    value = getattr(sections, section)
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
    return value
