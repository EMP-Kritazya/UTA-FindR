"""Settings loaded from environment variables / .env.

Why pydantic-settings: one typed place for every knob, values come from .env
(never hard-coded), and a missing or malformed value fails loudly at startup
instead of halfway through a crawl.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://utafindr:change-me@localhost:5344/utafindr"
    data_dir: Path = Path("./data")
    contact_email: str = "unset@example.com"
    repo_url: str = "https://github.com/unset/UTA-FindR"
    rate_limit_per_sec: float = 1.0
    http_timeout_sec: float = 60.0
    since_term: str = "Spring 2023"

    @property
    def user_agent(self) -> str:
        # Identify ourselves so UTA can see who is crawling and how to reach us.
        return f"UTAFindR/0.1 (+{self.repo_url}; contact: {self.contact_email})"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
