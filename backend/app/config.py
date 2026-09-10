"""Application settings, loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the backend package rather than the working directory, so the
# database lands in the same place however the server is launched.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = BACKEND_ROOT / "reports.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    # Gemini's built-in google_search tool has no free-tier quota, so search
    # runs through Serper instead -- both keys are free and need no card.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    # Writing from evidence already gathered is shallow work, and quotas are
    # metered per model -- so the writing pass runs on a faster model with its
    # own allowance. Set it equal to gemini_model to use just one.
    gemini_writer_model: str = "gemini-flash-lite-latest"

    # A second model provider, so a demo is not at the mercy of one free tier.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # The writing pass is shallow work, so it gets the faster model.
    groq_writer_model: str = "openai/gpt-oss-20b"

    serper_api_key: str | None = None

    # "auto" runs live research when both keys are present; "demo" forces the
    # canned agent regardless.
    llm_provider: str = "auto"
    database_path: str = str(DEFAULT_DATABASE)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Ceiling on the agent's research loop, so a confused model cannot spend
    # money forever on one request.
    # Each turn is a round trip the rep waits through, and some models will
    # keep searching rather than stopping on their own.
    max_research_turns: int = 5
    max_searches: int = 10
    # A guard on this app's own usage, not the provider's quota and not a
    # spending limit -- the free tier cannot be billed. It stops a day's
    # clicking from exhausting the provider's allowance. Zero disables it.
    daily_research_limit: int = Field(default=50, ge=0)

    @property
    def provider(self) -> str:
        """Which agent implementation to run: "gemini", "groq", or "demo".

        Demo mode is the fallback when nothing is configured. It swaps the
        provider implementation, not the pipeline -- the real agent is
        untouched either way.
        """
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.gemini_api_key and self.serper_api_key:
            return "gemini"
        if self.groq_api_key and self.serper_api_key:
            return "groq"
        return "demo"

    @property
    def demo_mode(self) -> bool:
        return self.provider == "demo"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
