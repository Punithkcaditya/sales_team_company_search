"""Incremental JSON-Lines parsing.

The list sections stream one JSON object per line, which lets the UI render each
executive or news item the moment it lands instead of waiting for the section to
finish. This buffer turns a token stream into completed objects.
"""

import json
from typing import Any


class JsonLineBuffer:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[dict[str, Any]]:
        """Append streamed text and return whatever complete objects it produced."""
        self._buffer += text
        objects: list[dict[str, Any]] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            parsed = _parse_line(line)
            if parsed is not None:
                objects.append(parsed)
        return objects

    def flush(self) -> list[dict[str, Any]]:
        """Parse whatever is left once the stream ends (no trailing newline)."""
        line, self._buffer = self._buffer, ""
        parsed = _parse_line(line)
        return [parsed] if parsed is not None else []


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one line, tolerating the wrappers models sometimes add.

    A malformed line is dropped rather than failing the section -- losing one
    bullet is a far better outcome than losing the report.
    """
    candidate = line.strip().removeprefix("```json").removeprefix("```").strip()
    candidate = candidate.removesuffix("```").strip().rstrip(",")
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
