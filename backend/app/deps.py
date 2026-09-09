"""Application wiring: what gets built once, and what each request gets."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import anthropic
import httpx
from fastapi import Request
from google import genai

from .agent.anthropic_agent import AnthropicResearchAgent
from .agent.base import ResearchAgent
from .agent.demo import DemoResearchAgent
from .agent.gemini_agent import GeminiResearchAgent
from .agent.search import SerperSearchClient
from .config import Settings
from .db import Database
from .repository import ReportRepository

logger = logging.getLogger(__name__)


class ActiveResearch:
    """Guards against two concurrent runs for the same company.

    A rep double-clicking search should not pay for the research twice, and the
    second stream would only race the first to save a near-identical report.
    """

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def key(company: str) -> str:
        return " ".join(company.lower().split())

    async def acquire(self, company: str) -> bool:
        async with self._lock:
            key = self.key(company)
            if key in self._active:
                return False
            self._active.add(key)
            return True

    async def release(self, company: str) -> None:
        async with self._lock:
            self._active.discard(self.key(company))


def build_agent(settings: Settings) -> tuple[ResearchAgent, list]:
    """Return the agent for the configured provider, plus anything to close."""
    provider = settings.provider

    if provider == "gemini":
        logger.info("Using Gemini (%s) with built-in Google Search.", settings.gemini_model)
        client = genai.Client(api_key=settings.gemini_api_key)
        return GeminiResearchAgent(client=client, model=settings.gemini_model), []

    if provider == "anthropic":
        logger.info("Using Claude (%s) with Serper search.", settings.anthropic_model)
        http_client = httpx.AsyncClient(timeout=15.0)
        anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        agent = AnthropicResearchAgent(
            client=anthropic_client,
            search=SerperSearchClient(settings.serper_api_key or "", client=http_client),
            model=settings.anthropic_model,
            max_turns=settings.max_research_turns,
            max_searches=settings.max_searches,
        )
        return agent, [http_client.aclose, anthropic_client.close]

    logger.warning("No API key set - running in demo mode with canned research.")
    return DemoResearchAgent(), []


@asynccontextmanager
async def lifespan(app) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.repository = ReportRepository(Database(settings.database_path))
    app.state.active_research = ActiveResearch()
    agent, closers = build_agent(settings)
    app.state.agent = agent
    try:
        yield
    finally:
        for close in closers:
            await close()


def get_repository(request: Request) -> ReportRepository:
    return request.app.state.repository


def get_agent(request: Request) -> ResearchAgent:
    return request.app.state.agent


def get_active_research(request: Request) -> ActiveResearch:
    return request.app.state.active_research
