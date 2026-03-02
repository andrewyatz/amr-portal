from functools import lru_cache
from pathlib import Path
import os

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variable or .env file.
    Requires DUCKDB_PATH to point to an existing DuckDB file.
    """
    duckdb_path: Path = Field(validation_alias="DUCKDB_PATH", description="Path to the DuckDB database file")
    enable_dataconnect: bool = Field(default=False, validation_alias="ENABLE_DATACONNECT", description="Enable GA4GH Data Connect API endpoints")

    model_config = SettingsConfigDict(
        # load from .env if present, or .env.test when running pytest
        env_file=".env.test" if os.getenv("TESTING") else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    @field_validator("duckdb_path", mode="after")  # type: ignore[misc]
    @classmethod
    def _validate_duckdb_path(cls, v: Path) -> Path:
        if not str(v).strip():
            raise ValueError("DUCKDB_PATH cannot be empty.")
        if not v.exists():
            raise ValueError(f"DUCKDB_PATH points to a non-existent path: {v}")
        if not v.is_file():
            raise ValueError(f"DUCKDB_PATH must be a file, not a directory: {v}")
        return v


class SettingsError(RuntimeError):
    """Raised when application settings are invalid or missing."""
    pass


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Load and cache Settings.
    Raises SettingsError with a readable message on failure.
    """
    try:
        return Settings()
    except ValidationError as e:
        details = "; ".join(
            f"{err['loc'][0]}: {err['msg']}" for err in e.errors()
        )
        raise SettingsError(f"Invalid configuration: {details}") from e
