"""Splits one streamed response into per-section chunks.

Writing all five sections in a single request costs one round trip instead of
five, and sends the search evidence once instead of five times. The model marks
each section with a line of its own:

    ##overview
    Acme builds industrial widgets...
    ##key_people
    {"name": "Dana Reyes", "title": "CEO"}

Prose still streams at token granularity: text is emitted the moment it arrives,
and only a fragment that might be the start of a marker is held back until the
line completes. List sections are line-buffered, since half a JSON object is not
useful to anyone.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..schemas import SECTION_ORDER, Section
from .base import ItemChunk, SectionChunk, TextDelta, ValueChunk

logger = logging.getLogger(__name__)

MARKER = "##"

PROSE_SECTIONS: frozenset[str] = frozenset({"overview"})
OBJECT_SECTIONS: frozenset[str] = frozenset({"financials"})

_BY_NAME: dict[str, Section] = {name: name for name in SECTION_ORDER}


class SectionStreamParser:
    """Feed it streamed text; get back `(section, chunk)` pairs in order."""

    def __init__(self) -> None:
        self._buffer = ""
        self._section: Section | None = None

    def feed(self, text: str) -> list[tuple[Section, SectionChunk]]:
        self._buffer += text
        out: list[tuple[Section, SectionChunk]] = []

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            out.extend(self._consume_line(line, complete=True))

        # Whatever follows the last newline is a partial line. Prose can be shown
        # immediately -- unless it might be the beginning of a section marker, in
        # which case it waits for the newline that would prove it either way.
        if self._section in PROSE_SECTIONS and self._buffer and not self._buffer.startswith("#"):
            out.append((self._section, TextDelta(self._buffer)))
            self._buffer = ""

        return out

    def flush(self) -> list[tuple[Section, SectionChunk]]:
        """Handle whatever the stream ended on, with no trailing newline."""
        line, self._buffer = self._buffer, ""
        return self._consume_line(line, complete=False)

    @property
    def section(self) -> Section | None:
        return self._section

    def _consume_line(self, line: str, *, complete: bool) -> list[tuple[Section, SectionChunk]]:
        stripped = line.strip()

        if stripped.startswith(MARKER):
            name = stripped.lstrip("#").strip().lower().replace(" ", "_")
            if name in _BY_NAME:
                self._section = _BY_NAME[name]
            else:
                logger.warning("Ignoring unknown section marker %r", stripped)
            return []

        if self._section is None or not stripped:
            # Preamble before the first marker, or a blank separator line.
            return [] if self._section not in PROSE_SECTIONS else self._prose(line, complete)

        if self._section in PROSE_SECTIONS:
            return self._prose(line, complete)

        parsed = _parse_json_line(stripped)
        if parsed is None:
            return []
        if self._section in OBJECT_SECTIONS:
            return [(self._section, ValueChunk(parsed))]
        return [(self._section, ItemChunk(parsed))]

    def _prose(self, line: str, complete: bool) -> list[tuple[Section, SectionChunk]]:
        if self._section is None:
            return []
        text = line + "\n" if complete else line
        return [(self._section, TextDelta(text))] if text else []


def _parse_json_line(line: str) -> dict[str, Any] | None:
    """Parse one line, tolerating the wrappers models sometimes add.

    A malformed line is dropped rather than failing the section -- losing one
    bullet is a far better outcome than losing the report.
    """
    candidate = line.removeprefix("```json").removeprefix("```").strip()
    candidate = candidate.removesuffix("```").strip().rstrip(",")
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
