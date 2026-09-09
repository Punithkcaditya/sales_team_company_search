"""The seam between the research pipeline and whichever LLM sits behind it.

`ResearchPipeline` only knows about this protocol, so the live agent and the
demo agent are interchangeable and tests can inject a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..schemas import Section, Source
from .search import SearchResult

# Called with each query the agent decides to run, before it runs, so the UI can
# show the rep what the agent is actually doing.
SearchObserver = Callable[[str], Awaitable[None]]


@dataclass
class ResearchContext:
    """Everything the agent learned during the search phase.

    `researchable` is False when the input does not resolve to a real company --
    gibberish, a typo with no plausible match, an empty web footprint.
    """

    company: str
    researchable: bool = True
    note: str = ""
    queries: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    # Set when the search phase hands back written notes rather than raw hits.
    # Sections are written from whichever of the two exists.
    findings: str = ""

    def corpus(self) -> str:
        """Everything the search phase learned, formatted for a prompt."""
        parts = [self.findings.strip()] if self.findings.strip() else []
        parts += [result.as_context() for result in self.results]
        return "\n".join(parts) if parts else "(no search results)"

    def sources(self) -> list[Source]:
        seen: set[str] = set()
        sources: list[Source] = []
        for result in self.results:
            if result.url and result.url not in seen:
                seen.add(result.url)
                sources.append(Source(title=result.title, url=result.url))
        return sources


@dataclass
class TextDelta:
    """A fragment of prose, for sections that stream token by token."""

    text: str


@dataclass
class ItemChunk:
    """One completed list entry, for sections that stream item by item."""

    value: dict[str, Any]


@dataclass
class ValueChunk:
    """A whole section value, for sections that only make sense complete."""

    value: Any


SectionChunk = TextDelta | ItemChunk | ValueChunk


class ResearchAgent(Protocol):
    async def gather(self, company: str, on_search: SearchObserver) -> ResearchContext: ...

    def write(self, context: ResearchContext) -> AsyncIterator[tuple[Section, SectionChunk]]:
        """Stream the whole briefing, each chunk tagged with its section.

        Sections arrive in `SECTION_ORDER`.
        """
        ...


class AgentError(RuntimeError):
    """The agent could not complete research for a reason worth showing a user."""

    code = "agent_error"


class QuotaExceededError(AgentError):
    """Stop the run when the provider refuses further usage."""

    code = "quota_exceeded"
