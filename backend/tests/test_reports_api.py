"""REST surface: listing, reading, and deleting saved reports."""

import pytest

from app.schemas import Financials, Person, ReportSections, Source


@pytest.fixture
def saved(repository):
    first = repository.create(
        "Acme",
        ReportSections(overview="Acme makes widgets.", key_people=[Person(name="Ada", title="CEO")]),
        [Source(title="Profile", url="https://x.test/a")],
    )
    second = repository.create("Globex", ReportSections(overview="Globex makes gadgets."))
    return first, second


async def test_health_reports_which_mode_the_app_is_in(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "demo", "provider": "demo"}


async def test_reports_list_is_empty_for_a_new_install(client):
    response = await client.get("/api/reports")
    assert response.status_code == 200
    assert response.json() == []


async def test_reports_are_listed_newest_first(client, saved):
    _, second = saved
    body = (await client.get("/api/reports")).json()
    assert [r["company"] for r in body] == ["Globex", "Acme"]
    assert body[0]["id"] == second.id
    # The list view carries only what the sidebar renders.
    assert set(body[0]) == {"id", "company", "created_at"}


async def test_a_single_report_round_trips_with_all_sections(client, saved):
    first, _ = saved
    body = (await client.get(f"/api/reports/{first.id}")).json()
    assert body["company"] == "Acme"
    assert body["sections"]["overview"] == "Acme makes widgets."
    assert body["sections"]["key_people"] == [{"name": "Ada", "title": "CEO"}]
    assert body["sources"] == [{"title": "Profile", "url": "https://x.test/a"}]


async def test_missing_report_returns_404_with_a_readable_message(client):
    response = await client.get("/api/reports/999")
    assert response.status_code == 404
    assert response.json() == {"message": "That report no longer exists."}


async def test_delete_removes_the_report(client, saved):
    first, _ = saved
    assert (await client.delete(f"/api/reports/{first.id}")).status_code == 204
    assert (await client.get(f"/api/reports/{first.id}")).status_code == 404
    assert [r["company"] for r in (await client.get("/api/reports")).json()] == ["Globex"]


async def test_deleting_twice_returns_404_rather_than_pretending_to_succeed(client, saved):
    first, _ = saved
    await client.delete(f"/api/reports/{first.id}")
    assert (await client.delete(f"/api/reports/{first.id}")).status_code == 404


async def test_financials_keep_their_nulls_through_storage(repository):
    report = repository.create(
        "Private Co",
        ReportSections(financials=Financials(revenue="$4M", market_cap=None)),
    )
    stored = repository.get(report.id)
    assert stored is not None
    assert stored.sections.financials.revenue == "$4M"
    assert stored.sections.financials.market_cap is None
