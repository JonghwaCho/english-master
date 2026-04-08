"""Application configuration via environment variables.

All secrets must come from environment variables or .env file.
Never commit secrets to source control.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Global application settings loaded from environment variables.

    For development, values can be placed in a `.env` file at the project root.
    For production, set them via the deployment platform (NCP, Docker secrets, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Application ────────────────────────────────────
    app_name: str = "English Master"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 5294
    secret_key: str = Field(default="dev-secret-change-me-in-production-32-chars-minimum")

    # ── Database ──────────────────────────────────────
    # SQLite for dev, PostgreSQL for production
    # Example prod: postgresql+psycopg://user:pass@host:5432/dbname
    database_url: str = Field(default=f"sqlite:///{BASE_DIR / 'data' / 'english_master_v2.db'}")
    sqlalchemy_echo: bool = False

    # ── Redis (Celery broker + rate limiter cache) ────
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    # ── JWT ───────────────────────────────────────────
    jwt_secret: str = Field(default="dev-jwt-secret-change-me-min-32-characters-long-string")
    jwt_access_token_minutes: int = 15
    jwt_refresh_token_days: int = 30
    jwt_algorithm: str = "HS256"

    # ── OAuth: Google ─────────────────────────────────
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:5294/auth/google/callback"

    # ── OAuth: Kakao ──────────────────────────────────
    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://127.0.0.1:5294/auth/kakao/callback"

    # ── AI Providers (server-side keys, operator-managed) ──
    ai_provider_default: Literal["gemini", "claude", "openai"] = "gemini"
    gemini_api_key: str = ""
    claude_api_key: str = ""
    openai_api_key: str = ""

    # ── Email (NCP Cloud Outbound Mailer or SMTP) ─────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@englishmaster.kr"

    # ── CORS ──────────────────────────────────────────
    cors_origins: str = "http://127.0.0.1:5294,http://localhost:5294"

    # ── Sentry ────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Rate Limiting ─────────────────────────────────
    rate_limit_storage_uri: str = "memory://"  # use redis:// in prod
    rate_limit_default: str = "200 per minute"

    # ── Tier limits (defaults, DB overrides) ──────────
    free_video_limit: int = 3
    free_ai_quota: int = 0
    basic_video_limit: int = 20
    basic_ai_quota: int = 50
    heavy_video_limit: int = 50
    heavy_ai_quota: int = 200
    vip_video_limit: int = 200
    vip_ai_quota: int = -1  # -1 = unlimited

    # ── Misc ──────────────────────────────────────────
    max_content_length_mb: int = 16
    frontend_url: str = "http://127.0.0.1:5294"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance."""
    return Settings()


# Ensure data directory exists
(BASE_DIR / "data").mkdir(exist_ok=True)
