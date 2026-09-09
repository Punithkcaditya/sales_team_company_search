"""Pipeline rules that protect the report from a model going off-script."""

from app.agent.pipeline import ResearchPipeline

from .conftest import FakeAgent


async def collect(agent, repository):
    return [event async for event in ResearchPipeline(agent, repository).run("Acme")]


async def test_malformed_items_are_dropped_without_losing_the_section(repository):
    agent = FakeAgent(
        people=[
            {"name": "Ada Lovelace", "title": "CEO"},
            {"name": "No title here"},  # missing a required field
            "not an object at all",
            {"name": "Grace Hopper", "title": "CTO"},
        ]
    )
    events = await collect(agent, repository)
    people = [e.data["item"] for e in events if e.event == "section_item" and e.data["section"] == "key_people"]

    assert people == [
        {"name": "Ada Lovelace", "title": "CEO"},
        {"name": "Grace Hopper", "title": "CTO"},
    ]


async def test_list_sections_are_capped_so_the_briefing_stays_scannable(repository):
    agent = FakeAgent(
        news=[{"headline": f"Item {i}", "source_url": None} for i in range(9)],
        risks=[{"risk": f"Risk {i}"} for i in range(9)],
    )
    events = await collect(agent, repository)
    report = next(e.data["report"] for e in events if e.event == "done")

    assert len(report["sections"]["news"]) == 4
    assert len(report["sections"]["risks"]) == 3


async def test_risks_are_stored_as_plain_strings(repository):
    agent = FakeAgent(risks=[{"risk": "Pending antitrust review"}])
    events = await collect(agent, repository)
    report = next(e.data["report"] for e in events if e.event == "done")

    assert report["sections"]["risks"] == ["Pending antitrust review"]


async def test_a_company_with_no_data_for_a_section_still_produces_a_report(repository):
    agent = FakeAgent(people=[], news=[], risks=[])
    events = await collect(agent, repository)
    report = next(e.data["report"] for e in events if e.event == "done")

    assert report["sections"]["key_people"] == []
    assert report["sections"]["news"] == []
    assert report["sections"]["overview"] == "Acme makes widgets."
