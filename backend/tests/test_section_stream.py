"""The parser that splits one streamed response into per-section chunks.

This is the piece that makes a single request behave like five, so it carries
the streaming UX: prose must still arrive token by token, and a malformed line
must cost one bullet rather than the section.
"""

import pytest

from app.agent.base import ItemChunk, TextDelta, ValueChunk
from app.agent.section_stream import SectionStreamParser


def drain(parser: SectionStreamParser, *chunks: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for chunk in chunks:
        out.extend(parser.feed(chunk))
    out.extend(parser.flush())
    return out


def test_sections_are_split_on_their_markers():
    out = drain(
        SectionStreamParser(),
        "##overview\nAcme makes widgets.\n",
        '##key_people\n{"name": "Ada", "title": "CEO"}\n',
        '##risks\n{"risk": "Antitrust review"}\n',
    )

    assert [section for section, _ in out] == ["overview", "key_people", "risks"]
    assert out[1] == ("key_people", ItemChunk({"name": "Ada", "title": "CEO"}))
    assert out[2] == ("risks", ItemChunk({"risk": "Antitrust review"}))


def test_prose_arrives_as_it_streams_not_line_by_line():
    """The whole point of streaming: the rep watches it being written."""
    parser = SectionStreamParser()
    parser.feed("##overview\n")

    assert parser.feed("Acme ") == [("overview", TextDelta("Acme "))]
    assert parser.feed("makes ") == [("overview", TextDelta("makes "))]
    assert parser.feed("widgets.") == [("overview", TextDelta("widgets."))]


def test_a_fragment_that_might_start_a_marker_is_held_until_it_is_settled():
    """Emitting "#" immediately would print the marker into the briefing."""
    parser = SectionStreamParser()
    parser.feed("##overview\nAcme.\n")

    assert parser.feed("#") == []
    assert parser.feed("#key_people\n") == []
    assert parser.section == "key_people"


def test_markers_split_across_chunks_are_still_recognised():
    out = drain(SectionStreamParser(), "##over", "view\nAcme makes widgets.\n")

    assert [section for section, _ in out] == ["overview"]
    assert "".join(c.text for _, c in out) == "Acme makes widgets.\n"


def test_json_objects_split_across_chunks_are_reassembled():
    out = drain(
        SectionStreamParser(), '##key_people\n{"name": "Ada", ', '"title": "CEO"}\n'
    )

    assert out == [("key_people", ItemChunk({"name": "Ada", "title": "CEO"}))]


def test_financials_come_back_as_one_value_not_a_list_item():
    out = drain(
        SectionStreamParser(),
        '##financials\n{"revenue": "$4.2B", "market_cap": null}\n',
    )

    assert out == [("financials", ValueChunk({"revenue": "$4.2B", "market_cap": None}))]


def test_a_malformed_line_costs_one_bullet_not_the_section():
    out = drain(
        SectionStreamParser(),
        '##key_people\n{"name": "Ada", "title": "CEO"}\n',
        "{ this is not json\n",
        '{"name": "Grace", "title": "CTO"}\n',
    )

    assert [chunk.value["name"] for _, chunk in out] == ["Ada", "Grace"]


@pytest.mark.parametrize(
    "line",
    [
        '```json\n{"name": "Ada", "title": "CEO"}\n```\n',
        '{"name": "Ada", "title": "CEO"},\n',
    ],
)
def test_common_model_formatting_slips_are_tolerated(line):
    out = drain(SectionStreamParser(), "##key_people\n", line)

    assert [chunk.value for _, chunk in out] == [{"name": "Ada", "title": "CEO"}]


def test_text_before_the_first_marker_is_ignored():
    """Models sometimes open with "Here is the briefing:"."""
    out = drain(
        SectionStreamParser(), "Here is the briefing:\n##overview\nAcme makes widgets.\n"
    )

    assert [section for section, _ in out] == ["overview"]
    assert "".join(c.text for _, c in out) == "Acme makes widgets.\n"


def test_an_unknown_marker_does_not_derail_the_sections_that_follow():
    out = drain(
        SectionStreamParser(),
        "##summary\nignored\n",
        '##key_people\n{"name": "Ada", "title": "CEO"}\n',
    )

    assert [section for section, _ in out] == ["key_people"]


def test_a_section_with_no_content_simply_yields_nothing():
    out = drain(SectionStreamParser(), "##news\n\n##risks\n")

    assert out == []
