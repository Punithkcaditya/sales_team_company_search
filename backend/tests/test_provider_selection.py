"""Which provider the app picks, and when.

Getting this wrong is the difference between a free run and a billed one, so it
is pinned down rather than left to inspection.
"""

import pytest

from app.config import Settings

BLANK = {"anthropic_api_key": None, "serper_api_key": None, "gemini_api_key": None, "_env_file": None}


def settings(**over) -> Settings:
    return Settings(**{**BLANK, **over})


@pytest.mark.parametrize(
    "keys, expected",
    [
        ({}, "demo"),
        # Neither model provider can search on its own -- Gemini's built-in
        # google_search has no free-tier quota -- so a model key alone is not
        # enough to run, and half-configured never means a half-working app.
        ({"gemini_api_key": "g"}, "demo"),
        ({"anthropic_api_key": "a"}, "demo"),
        ({"serper_api_key": "s"}, "demo"),
        ({"gemini_api_key": "g", "serper_api_key": "s"}, "gemini"),
        ({"anthropic_api_key": "a", "serper_api_key": "s"}, "anthropic"),
        # Gemini wins when both model keys are present: it is the free one.
        ({"gemini_api_key": "g", "anthropic_api_key": "a", "serper_api_key": "s"}, "gemini"),
    ],
)
def test_provider_is_chosen_from_the_keys_actually_present(keys, expected):
    assert settings(**keys).provider == expected


def test_no_keys_means_demo_mode_and_therefore_no_paid_call():
    assert settings().demo_mode is True


@pytest.mark.parametrize("provider", ["gemini", "anthropic", "demo"])
def test_an_explicit_provider_overrides_auto_detection(provider):
    pinned = settings(llm_provider=provider, gemini_api_key="g", serper_api_key="s")
    assert pinned.provider == provider


def test_a_provider_pinned_without_a_search_key_falls_back_loudly(caplog):
    """LLM_PROVIDER can select a model provider whose search key is missing.

    Running anyway would fail every search and report every company as
    "not found" -- broken research that looks like a real answer.
    """
    from app.agent.demo import DemoResearchAgent
    from app.deps import build_agent

    pinned = settings(llm_provider="gemini", gemini_api_key="g")  # no serper key

    agent, closers = build_agent(pinned)

    assert isinstance(agent, DemoResearchAgent)
    assert closers == []
    assert "SERPER_API_KEY" in caplog.text
