"""Turns an agent's output into the stream of events the frontend renders.

This module owns ordering, validation, and persistence. It knows nothing about
the model or the search provider -- swap the agent and this is unchanged.
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
from .base import (
    AgentError,
    ItemChunk,
    QuotaExceededError,
    ResearchAgent,
    ResearchContext,
    TextDelta,
    ValueChunk,
)

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
        writer = _SectionWriter(sections)
        try:
            async for section, chunk in self._agent.write(context):
                for event in writer.accept(section, chunk):
                    yield event
        except QuotaExceededError as exc:
            yield events.error(exc.code, str(exc))
            return
        except Exception:
            logger.exception("Writing the briefing failed for %r", context.company)
            if not writer.has_content:
                yield events.error(
                    "agent_error",
                    "The research service was unreachable, so no briefing could be written.",
                )
                return
        # Close whatever is still open, and resolve sections the model skipped,
        # so the UI never leaves a section spinning.
        for event in writer.finish():
            yield event

        report = await asyncio.to_thread(
            self._repository.create, context.company, sections, context.sources()
        )
        yield events.done(report.model_dump())


class _SectionWriter:
    """Turns a stream of `(section, chunk)` pairs into section events.

    The agent may write every section in one response, so section boundaries are
    inferred from the tags rather than from separate calls: a new tag closes the
    previous section and opens the next.
    """

    def __init__(self, sections: ReportSections) -> None:
        self._sections = sections
        self._current: Section | None = None
        self._started: list[Section] = []
        self._text: list[str] = []
        self._items: list[BaseModel] = []

    @property
    def has_content(self) -> bool:
        return bool(self._started)

    def accept(self, section: Section, chunk: object) -> list[ResearchEvent]:
        out: list[ResearchEvent] = []

        if section != self._current:
            out.extend(self._close_current())
            self._current = section
            if section not in self._started:
                self._started.append(section)
                out.append(events.section_start(section))

        if isinstance(chunk, TextDelta):
            self._text.append(chunk.text)
            out.append(events.section_delta(section, chunk.text))

        elif isinstance(chunk, ItemChunk) and section in _LIST_SECTIONS:
            model, limit = _LIST_SECTIONS[section]
            if len(self._items) < limit:
                item = _coerce(model, chunk.value)
                if item is not None:
                    self._items.append(item)
                    out.append(events.section_item(section, item.model_dump()))

        elif isinstance(chunk, ValueChunk) and section == "financials":
            self._sections.financials = _coerce(Financials, chunk.value) or Financials()

        return out

    def finish(self) -> list[ResearchEvent]:
        out = self._close_current()
        for section in SECTION_ORDER:
            if section not in self._started:
                # The model skipped it entirely; report it as empty rather than
                # leaving its skeleton on screen forever.
                out.append(events.section_start(section))
                out.append(events.section_end(section, _section_value(self._sections, section)))
        return out

    def _close_current(self) -> list[ResearchEvent]:
        section, self._current = self._current, None
        if section is None:
            return []

        if section == "overview":
            self._sections.overview = "".join(self._text).strip()
        elif section == "risks":
            # Risks are stored as plain strings; the wrapper object exists only
            # to give the model a stable line format to emit.
            self._sections.risks = [item.risk for item in self._items]  # type: ignore[attr-defined]
        elif section in _LIST_SECTIONS:
            setattr(self._sections, section, list(self._items))

        self._text.clear()
        self._items = []
        return [events.section_end(section, _section_value(self._sections, section))]


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
