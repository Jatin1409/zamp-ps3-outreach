"""Runtime configuration. Every secret comes from the environment (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # --- providers -------------------------------------------------------
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    tavily_api_key: str = ""
    llm_provider: str = "gemini"             # gemini | anthropic | fixture
    gemini_model: str = "gemini-3.6-flash"           # drafting / verification (stable)
    gemini_model_fast: str = "gemini-3.5-flash-lite"  # classification / scoring (stable)
    llm_min_interval_s: float = 1.0          # space calls out to respect free-tier per-minute quotas
    gemini_max_retries: int = 4
    # Free-tier quotas are per model per day (gemini-3.6-flash: 20 requests/day, observed 2026-11-05), so each tier
    # falls back down an ordered chain when a model's daily quota is used up or it is overloaded.
    gemini_fallbacks: str = "gemini-3.8-flash,gemini-3.5-flash-lite"
    gemini_fast_fallbacks: str = "gemini-3.1-flash-lite"
    search_cost_per_call_usd: float = 0.0  # Tavily free tier = $0; pay-as-you-go list price is $0.008
    search_provider: str = "tavily"          # tavily | fixture
    llm_model: str = "claude-opus-5-5"       # drafting / verification
    llm_model_fast: str = "claude-opus-5-5"  # classification / scoring (swap to a cheaper model here)
    llm_effort_fast: str = "low"
    llm_effort_draft: str = "medium"
    llm_timeout_s: float = 90.0
    search_timeout_s: float = 20.0
    provider_retries: int = 2

    # --- storage -----------------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{(ROOT / 'local.db').as_posix()}"

    # --- abuse protection ----------------------------------------------------
    access_code: str = "local-dev"
    runs_per_hour_per_client: int = 15
    runs_per_day_global: int = 150
    max_concurrent_runs: int = 3

    # --- misc ------------------------------------------------------------
    worker_enabled: bool = True
    static_dir: str = str(ROOT / "backend" / "static")
    fixtures_dir: str = str(ROOT / "backend" / "fixtures")

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        # Render/Heroku style URLs -> asyncpg driver
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.async_database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


# USD per million tokens, from the Anthropic pricing table (checked 2026-11-05).
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
    "fixture": (0.0, 0.0),
    # Gemini free tier: no charge (Google may use free-tier prompts to improve its products).
    "gemini-3.6-flash": (0.0, 0.0),
    "gemini-3.5-flash-lite": (0.0, 0.0),
    "gemini-3.8-flash": (0.0, 0.0),
}


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES_PER_MTOK.get(model, (0.0, 0.0) if model.startswith("gemini") else (4.00, 20.00))
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)
