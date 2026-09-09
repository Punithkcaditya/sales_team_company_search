from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database
from app.usage import DailyUsage
from .conftest import collect_events


async def test_demo_usage_does_not_claim_a_provider_token_balance(client):
    body = (await client.get("/api/usage")).json()
    assert body["mode"] == "demo"
    assert body["remaining"] is None
    assert body["provider_tokens_remaining"] is None
    await collect_events(client, "Acme")
    assert (await client.get("/api/usage")).json()["used_today"] == 0


async def test_last_allowance_blocks_next_attempt_before_calling_agent(client, settings, agent, app):
    settings.llm_provider = "gemini"
    settings.daily_research_limit = 1
    await collect_events(client, "Acme")
    body = (await client.get("/api/usage")).json()
    assert body["remaining"] == 0
    assert body["used_today"] == 1
    response = await client.post("/api/research", json={"company": "Globex"})
    assert response.status_code == 429
    assert response.json()["code"] == "daily_limit"
    assert agent.gathered == ["Acme"]
    assert await app.state.active_research.acquire("Globex")


async def test_failed_attempt_uses_allowance_but_invalid_and_duplicate_inputs_do_not(client, settings, agent, app):
    settings.llm_provider = "gemini"
    settings.daily_research_limit = 3
    assert (await client.post("/api/research", json={"company": "   "})).status_code == 422
    await app.state.active_research.acquire("Acme")
    assert (await client.post("/api/research", json={"company": "Acme"})).status_code == 409
    assert (await client.get("/api/usage")).json()["used_today"] == 0
    agent.researchable = False
    await collect_events(client, "Globex")
    assert (await client.get("/api/usage")).json()["used_today"] == 1


def test_allowance_survives_restart_and_resets_on_next_utc_day(tmp_path, settings):
    settings.llm_provider = "gemini"
    settings.daily_research_limit = 1
    path = str(tmp_path / "usage.db")
    now = datetime(2026, 9, 9, 23, 59, tzinfo=timezone.utc)
    assert DailyUsage(Database(path)).reserve(settings, now)
    restarted = DailyUsage(Database(path))
    assert not restarted.reserve(settings, now)
    assert restarted.snapshot(settings, now)["resets_at"] == "2026-09-10T00:00:00+00:00"
    tomorrow = now + timedelta(minutes=2)
    assert restarted.snapshot(settings, tomorrow)["remaining"] == 1
    assert restarted.reserve(settings, tomorrow)


def test_concurrent_visitors_cannot_exceed_shared_allowance(tmp_path, settings):
    settings.llm_provider = "gemini"
    settings.daily_research_limit = 3
    usage = DailyUsage(Database(str(tmp_path / "usage.db")))
    with ThreadPoolExecutor(max_workers=6) as pool:
        accepted = list(pool.map(lambda _: usage.reserve(settings), range(12)))
    assert sum(accepted) == 3
    assert usage.snapshot(settings)["remaining"] == 0


def test_zero_disables_app_limit_without_claiming_unlimited_provider_usage(repository, settings):
    settings.llm_provider = "gemini"
    settings.daily_research_limit = 0
    usage = DailyUsage(repository.db)
    for _ in range(25):
        assert usage.reserve(settings)
    body = usage.snapshot(settings)
    assert body["daily_limit"] is None
    assert body["remaining"] is None
    assert body["provider_tokens_remaining"] is None
    assert body["used_today"] == 25


def test_negative_daily_limit_is_rejected(settings):
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(_env_file=None, daily_research_limit=-1)
