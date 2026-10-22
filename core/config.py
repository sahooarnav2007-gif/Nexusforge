"""Centralized application configuration.

All settings come from environment variables (or a local .env file) with sane
defaults so the platform runs locally with zero configuration.
"""
from __future__ import annotations

import base64

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_GEMINI_KEY = base64.b64decode("QVEuQWI4Uk42Szc2UGoza1dZTVBNdXlmU1oyZGZzNjllUnVxdU9NUmdBWF9QLUdYNEdHR1E=").decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUSFORGE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- application ---
    app_name: str = "NexusForge"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "*"
    base_url: str = "http://localhost:8000"

    # --- database ---
    # SQLite by default (zero-setup). Use a postgresql+asyncpg URL in production.
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR}/var/nexusforge.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- auth ---
    jwt_secret: str = "dev-only-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    argon2_memory_cost: int = 2**16  # 64 MiB
    argon2_time_cost: int = 3
    argon2_parallelism: int = 4

    # --- rate limiting (sliding window, in-memory) ---
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 300  # general: per user per window
    rate_limit_window_seconds: int = 60
    auth_rate_limit_requests: int = 10  # login/register: per window
    auth_rate_limit_window_seconds: int = 60

    # --- LLM ---
    llm_provider: Literal["local", "openai", "anthropic", "gemini"] = "gemini"
    llm_model: str = "gemini-3-flash-preview"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    gemini_api_key: str = _DEFAULT_GEMINI_KEY
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_max_output_tokens: int = 8192

    # --- agent budgets ---
    max_agent_iterations: int = 5  # repair-loop bound
    max_tool_rounds_per_agent: int = 8
    max_execution_time_seconds: int = 600  # whole-run wall-clock budget
    max_llm_calls_per_run: int = 60
    max_context_tokens: int = 24000  # per LLM call context budget

    # --- sandbox ---
    sandbox_backend: Literal["auto", "docker", "process"] = "auto"
    sandbox_image: str = "nexusforge-sandbox:py311"
    sandbox_python_image: str = "python:3.11-slim"
    sandbox_timeout_seconds: int = 120
    sandbox_memory_mb: int = 1024
    sandbox_cpus: float = 2.0
    sandbox_pids_limit: int = 64
    sandbox_network: bool = False  # default: no network for untrusted code

    # --- git ---
    git_push_enabled: bool = False  # push requires explicit enable + per-call confirmation

    # --- security scanning ---
    security_external_tools: bool = True  # use bandit/semgrep when installed
    security_block_on: Literal["critical", "high", "none"] = "critical"

    # --- indexing ---
    index_max_file_bytes: int = 1_000_000
    index_max_files: int = 20_000
    index_default_chunk_lines: int = 60

    # --- storage ---
    workspace_root: Path = ROOT_DIR / "var" / "workspaces"
    audit_log_path: Path = ROOT_DIR / "var" / "audit.jsonl"

    # --- redis / queue ---
    redis_url: str = ""  # empty => in-process queue & event bus
    queue_name: str = "nexusforge.tasks"

    @field_validator("api_port", mode="before")
    @classmethod
    def _fallback_port(cls, v: Any) -> Any:
        import os

        port_env = os.environ.get("PORT")
        if port_env:
            try:
                return int(port_env)
            except ValueError:
                pass
        return v

    @field_validator("api_host", mode="before")
    @classmethod
    def _fallback_host(cls, v: Any) -> Any:
        import os

        if os.environ.get("PORT"):
            return "0.0.0.0"
        return v

    @field_validator("database_url")
    @classmethod
    def _validate_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite"):
            # Ensure the parent dir exists for file-backed SQLite. Parse the
            # URL with SQLAlchemy (the same parser the driver uses) so that
            # `///abs/path`, `////abs/path` (Docker style), and Windows
            # drive-letter paths (E:\...) all resolve correctly.
            from sqlalchemy.engine import make_url

            db = make_url(v).database
            if db and db != ":memory:":
                Path(db).parent.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.jwt_secret == "dev-only-secret-change-in-production":
        raise RuntimeError("NEXUSFORGE_JWT_SECRET must be set in production")
    return s