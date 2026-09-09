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
        ({"gemini_api_key": "g"}, "gemini"),
        # Anthropic has no built-in search, so one key alone is not enough to run.
        ({"anthropic_api_key": "a"}, "demo"),
        ({"serper_api_key": "s"}, "demo"),
        ({"anthropic_api_key": "a", "serper_api_key": "s"}, "anthropic"),
        # Gemini wins when both are available: one key, and search is included.
        ({"gemini_api_key": "g", "anthropic_api_key": "a", "serper_api_key": "s"}, "gemini"),
    ],
)
def test_provider_is_chosen_from_the_keys_actually_present(keys, expected):
    assert settings(**keys).provider == expected


def test_no_keys_means_demo_mode_and_therefore_no_paid_call():
    assert settings().demo_mode is True


@pytest.mark.parametrize("provider", ["gemini", "anthropic", "demo"])
def test_an_explicit_provider_overrides_auto_detection(provider):
    assert settings(llm_provider=provider, gemini_api_key="g").provider == provider
