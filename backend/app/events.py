"""Server-Sent Event shapes for the research stream.

Every event is a named SSE event with a JSON payload. Named events let the
frontend dispatch without sniffing the payload, and keep new event types from
breaking older clients -- an unknown name is simply ignored.

    event: section_delta
    data: {"section": "overview", "text": "Acme is a "}

Event vocabulary:
  status        phase changes worth narrating ("Searching the web…")
  search        one query the agent decided to run
  section_start a section is about to produce content
  section_delta a fragment of prose for that section
  section_item  one completed list entry for that section
  section_end   the section's final, validated value
  done          the saved report
  error         terminal failure, with a message safe to show a user
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResearchEvent:
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data)}\n\n"


def status(phase: str, message: str) -> ResearchEvent:
    return ResearchEvent("status", {"phase": phase, "message": message})


def search(query: str) -> ResearchEvent:
    return ResearchEvent("search", {"query": query})


def section_start(section: str) -> ResearchEvent:
    return ResearchEvent("section_start", {"section": section})


def section_delta(section: str, text: str) -> ResearchEvent:
    return ResearchEvent("section_delta", {"section": section, "text": text})


def section_item(section: str, item: dict[str, Any]) -> ResearchEvent:
    return ResearchEvent("section_item", {"section": section, "item": item})


def section_end(section: str, value: Any) -> ResearchEvent:
    return ResearchEvent("section_end", {"section": section, "value": value})


def done(report: dict[str, Any]) -> ResearchEvent:
    return ResearchEvent("done", {"report": report})


def error(code: str, message: str) -> ResearchEvent:
    return ResearchEvent("error", {"code": code, "message": message})
