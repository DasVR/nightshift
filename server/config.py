from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_prefix="NIGHTSHIFT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    data_dir: Path = Path("data")  # root for ingested bundles + run logs (gitignored)
    host: str = "127.0.0.1"
    port: int = 8788
    log_level: str = "info"
    notify_targets: list[str] = ["discord:home"]  # hermes send targets, used by the done-notify slice
    photon_target: str | None = None  # optional second notify channel
    max_session_bytes: int = 64 * 1024 * 1024  # 64 MiB per session file
    max_bundle_bytes: int = 512 * 1024 * 1024  # 512 MiB total uncompressed bundle

    @field_validator("notify_targets", mode="before")
    @classmethod
    def _parse_notify_targets(cls, value: object) -> object:
        """Split comma-separated env strings into a clean list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def runs_dir(self) -> Path:
        """Directory for storing run state/logs."""
        return self.data_dir / "runs"

    @property
    def db_path(self) -> Path:
        """Path to the sqlite session store."""
        return self.data_dir / "nightshift.db"

    @property
    def sessions_dir(self) -> Path:
        """Directory for persisted raw session jsonl files."""
        return self.data_dir / "sessions"

    def ensure_dirs(self) -> None:
        """Create data_dir and subdirectories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings; tests need this."""
    get_settings.cache_clear()
