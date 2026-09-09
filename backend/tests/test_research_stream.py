"""The SSE research endpoint: event contract, persistence, and failure modes."""

import asyncio

import pytest

from app.agent.pipeline import ResearchPipeline
from app.schemas import SECTION_ORDER

from .conftest import FakeAgent, collect_events, events_named


async def test_stream_reports_progress_then_sections_in_reading_order(client):
    events = await collect_events(client, "Acme")
    names = [name for name, _ in events]

    assert names[0] == "status"
    assert names[-1] == "done"
    # The rep sees the queries the agent chose to run.
    assert [d["query"] for d in events_named(events, "search")] == [
        "Acme overview",
        "Acme leadership",
    ]
    assert [d["section"] for d in events_named(events, "section_start")] == list(SECTION_ORDER)
    assert [d["section"] for d in events_named(events, "section_end")] == list(SECTION_ORDER)


async def test_prose_streams_in_fragments_and_lists_stream_item_by_item(client):
    events = await collect_events(client, "Acme")

    deltas = [d["text"] for d in events_named(events, "section_delta")]
    assert len(deltas) > 1, "overview should arrive progressively, not in one lump"
    assert "".join(deltas) == "Acme makes widgets."

    people = [d["item"] for d in events_named(events, "section_item") if d["section"] == "key_people"]
    assert people == [{"name": "Ada Lovelace", "title": "CEO"}]


async def test_completed_research_is_saved_and_appears_in_history(client):
    events = await collect_events(client, "Acme")
    report = events_named(events, "done")[0]["report"]

    assert report["company"] == "Acme"
    assert report["sections"]["overview"] == "Acme makes widgets."
    assert report["sections"]["financials"]["market_cap"] is None
    assert report["sources"] == [
        {"title": "Profile", "url": "https://x.test/a"},
        {"title": "News", "url": "https://x.test/b"},
    ]

    listed = (await client.get("/api/reports")).json()
    assert [r["id"] for r in listed] == [report["id"]]


@pytest.mark.parametrize("agent", [FakeAgent(researchable=False)])
async def test_unresearchable_input_ends_in_a_readable_error_and_saves_nothing(client, agent):
    events = await collect_events(client, "asdkjhasd")
    errors = events_named(events, "error")

    assert errors == [{"code": "not_found", "message": "No such company."}]
    assert events_named(events, "done") == []
    assert (await client.get("/api/reports")).json() == []


@pytest.mark.parametrize("agent", [FakeAgent(fail_at="news")])
async def test_a_failure_partway_through_keeps_what_was_already_written(client, agent):
    """The briefing is one stream now, so a failure truncates it -- but the
    sections already written are still worth saving."""
    events = await collect_events(client, "Acme")
    report = events_named(events, "done")[0]["report"]

    assert report["sections"]["overview"] == "Acme makes widgets."
    assert report["sections"]["key_people"]
    assert report["sections"]["news"] == []
    # Every section still closes, so the UI never leaves a spinner running.
    assert [d["section"] for d in events_named(events, "section_end")] == list(SECTION_ORDER)


@pytest.mark.parametrize("agent", [FakeAgent(fail_at="overview")])
async def test_a_total_provider_outage_is_reported_and_saves_nothing(client, agent):
    events = await collect_events(client, "Acme")

    assert events_named(events, "error")[0]["code"] == "agent_error"
    assert events_named(events, "done") == []
    assert (await client.get("/api/reports")).json() == []


@pytest.mark.parametrize("company", ["", "   ", "???", "x" * 101])
async def test_invalid_input_is_rejected_before_any_research_starts(client, agent, company):
    response = await client.post("/api/research", json={"company": company})
    assert response.status_code == 422
    assert isinstance(response.json()["message"], str)
    assert agent.gathered == []


async def test_concurrent_research_for_the_same_company_is_refused(client, app):
    """A rep double-clicking search should not pay for the research twice."""
    active = app.state.active_research
    assert await active.acquire("Acme")

    # Same company, different typing -- still the same run.
    response = await client.post("/api/research", json={"company": "  acme  "})
    assert response.status_code == 409
    assert "already running" in response.json()["message"]

    # A different company is unaffected.
    other = await client.post("/api/research", json={"company": "Globex"})
    assert other.status_code == 200
    await other.aread()

    await active.release("ACME")
    assert await active.acquire("Acme"), "release should be case- and space-insensitive too"


async def test_the_company_is_released_once_research_finishes(client):
    await collect_events(client, "Acme")
    events = await collect_events(client, "Acme")
    assert events_named(events, "done")


async def test_abandoning_the_stream_mid_run_saves_nothing(agent, repository):
    """What happens when the rep navigates away or hits cancel."""
    stream = ResearchPipeline(agent, repository).run("Acme")

    assert (await stream.__anext__()).event == "status"
    await stream.aclose()
    await asyncio.sleep(0)

    assert repository.list() == []
