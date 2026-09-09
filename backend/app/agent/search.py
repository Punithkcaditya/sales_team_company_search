"""Live web search, via Serper (Google Search results as JSON)."""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx
from pydantic import BaseModel

SERPER_ENDPOINT = "https://google.serper.dev/search"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    published: str | None = None

    def as_context(self) -> str:
        dateline = f" ({self.published})" if self.published else ""
        return f"- {self.title}{dateline}\n  {self.url}\n  {self.snippet}"


class SearchError(RuntimeError):
    """Search was unavailable. The agent treats this as an empty result set
    rather than failing the whole report."""


class SearchClient(Protocol):
    async def search(self, query: str, limit: int = 6) -> list[SearchResult]: ...


class SerperSearchClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def search(self, query: str, limit: int = 6) -> list[SearchResult]:
        try:
            response = await self._client.post(
                SERPER_ENDPOINT,
                headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                json={"q": query, "num": limit},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"Web search failed for {query!r}: {exc}") from exc

        return _parse_serper(response.json(), limit)

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_serper(payload: dict, limit: int) -> list[SearchResult]:
    """Flatten the parts of a Serper response the agent can actually use.

    `news` first: recency is what makes a briefing credible, and the news block
    is the only one that carries dates.
    """
    results: list[SearchResult] = []

    for item in payload.get("news", []):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                published=item.get("date"),
            )
        )

    knowledge = payload.get("knowledgeGraph") or {}
    if knowledge.get("description"):
        results.append(
            SearchResult(
                title=knowledge.get("title", "Knowledge panel"),
                url=knowledge.get("descriptionLink") or knowledge.get("website") or "",
                snippet=_knowledge_snippet(knowledge),
            )
        )

    for item in payload.get("organic", []):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                published=item.get("date"),
            )
        )

    return [r for r in results if r.title and r.snippet][:limit]


def _knowledge_snippet(knowledge: dict) -> str:
    parts = [knowledge.get("description", "")]
    for key, value in (knowledge.get("attributes") or {}).items():
        parts.append(f"{key}: {value}")
    return " | ".join(p for p in parts if p)


class StaticSearchClient:
    """Search stand-in used by demo mode and tests.

    Deliberately shaped like the real client -- same signature, same latency
    profile -- so the agent code above it never branches on which one it has.
    """

    def __init__(self, results: list[SearchResult] | None = None, delay: float = 0.35) -> None:
        self._results = results or []
        self._delay = delay
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 6) -> list[SearchResult]:
        self.queries.append(query)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._results[:limit]
