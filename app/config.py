from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "apps" / "insurance_claims" / "fixtures"
STATIC_DIR = REPO_ROOT / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_extract_model: str | None = None
    # Sent only when set; leave blank for models without reasoning controls.
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    demo_today: dt.date | None = None
    out_of_scope_threshold: int = Field(default=3, ge=1)
    max_verify_attempts: int = Field(default=3, ge=1)
    consent_scenario: str = "default"
    session_ttl_minutes: int = Field(default=30, ge=1)

    @field_validator("openai_api_key", "openai_extract_model", "openai_reasoning_effort", "demo_today", mode="before")
    @classmethod
    def _blank_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def model_configured(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())

    @property
    def extract_model(self) -> str:
        return self.openai_extract_model or self.openai_model

    def today(self) -> dt.date:
        return self.demo_today or dt.date.today()


@lru_cache
def get_settings() -> Settings:
    return Settings()
