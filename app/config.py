"""Environment-driven configuration.

Every tunable lives here. Nothing in the codebase reads ``os.environ`` directly
and no connection string is hardcoded, so the same image runs in Compose, in CI
and in production with only the environment changing.

``get_settings`` is cached and injected as a FastAPI dependency, which lets a
test override a single value without mutating the process environment.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Dependencies ---
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "document_insights"
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout: float = Field(default=5.0, gt=0)

    # --- Rate limiting ---
    max_active_docs_per_user: int = Field(default=3, ge=1)
    rate_limit_ttl_seconds: int = Field(default=86_400, ge=60)

    # --- Content cache ---
    cache_ttl_seconds: int = Field(default=3_600, ge=1)
    inflight_ttl_seconds: int = Field(default=300, ge=1)

    # --- Simulated processing ---
    processing_min_seconds: float = Field(default=10.0, ge=0)
    processing_max_seconds: float = Field(default=30.0, ge=0)
    failure_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # --- Worker ---
    max_attempts: int = Field(default=3, ge=1)
    retry_base_seconds: float = Field(default=2.0, ge=0)
    retry_max_seconds: float = Field(default=60.0, ge=0)
    worker_concurrency: int = Field(default=10, ge=1)
    # How long XREADGROUP blocks waiting for work. The Redis socket timeout is
    # derived from this (see app/db.create_redis_client) -- a socket timeout
    # shorter than the block duration turns every idle poll into an error.
    stream_block_seconds: float = Field(default=5.0, gt=0)
    lease_seconds: int = Field(default=120, ge=1)
    sweeper_interval_seconds: float = Field(default=15.0, gt=0)
    stale_queued_seconds: int = Field(default=120, ge=1)

    # --- Input validation ---
    max_content_length: int = Field(default=100_000, ge=1)
    max_title_length: int = Field(default=300, ge=1)

    # --- Observability ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- Queue names (rarely changed; here so nothing is a magic string) ---
    stream_key: str = "stream:documents"
    consumer_group: str = "workers"
    retry_zset_key: str = "retry:documents"

    @model_validator(mode="after")
    def _check_ranges(self) -> "Settings":
        if self.processing_max_seconds < self.processing_min_seconds:
            raise ValueError(
                "PROCESSING_MAX_SECONDS must be >= PROCESSING_MIN_SECONDS"
            )
        return self

    def rate_limit_key(self, user_id: str) -> str:
        return f"ratelimit:active:{user_id}"

    def cache_key(self, user_id: str, content_hash: str) -> str:
        # Scoped per user: see README, "Cache scope" assumption.
        return f"cache:summary:{user_id}:{content_hash}"

    def inflight_key(self, user_id: str, content_hash: str) -> str:
        return f"inflight:{user_id}:{content_hash}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
