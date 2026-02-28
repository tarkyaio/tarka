from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class MemoryConfig:
    # Feature flags
    memory_enabled: bool
    db_auto_migrate: bool

    # Postgres connection (either dsn or parts)
    postgres_dsn: Optional[str]
    postgres_host: Optional[str]
    postgres_port: int
    postgres_db: Optional[str]
    postgres_user: Optional[str]
    postgres_password: Optional[str]


def load_memory_config() -> MemoryConfig:
    dsn = (os.getenv("POSTGRES_DSN") or "").strip() or None
    host = (os.getenv("POSTGRES_HOST") or "").strip() or None
    port_raw = (os.getenv("POSTGRES_PORT") or "").strip() or "5432"
    try:
        port = int(port_raw)
    except Exception:
        port = 5432
    db = (os.getenv("POSTGRES_DB") or "").strip() or None
    user = (os.getenv("POSTGRES_USER") or "").strip() or None
    pw = (os.getenv("POSTGRES_PASSWORD") or "").strip() or None

    return MemoryConfig(
        memory_enabled=_env_bool("MEMORY_ENABLED", False),
        db_auto_migrate=_env_bool("DB_AUTO_MIGRATE", False),
        postgres_dsn=dsn,
        postgres_host=host,
        postgres_port=port,
        postgres_db=db,
        postgres_user=user,
        postgres_password=pw,
    )


def build_postgres_dsn(cfg: MemoryConfig) -> Optional[str]:
    if cfg.postgres_dsn:
        return cfg.postgres_dsn
    if not (cfg.postgres_host and cfg.postgres_db and cfg.postgres_user and cfg.postgres_password):
        return None
    # Prefer psycopg's conninfo builder to correctly quote/escape special characters
    # (e.g., spaces, quotes) in passwords and other fields.
    try:
        from psycopg.conninfo import make_conninfo  # type: ignore[import-not-found]

        return make_conninfo(
            host=cfg.postgres_host,
            port=cfg.postgres_port,
            dbname=cfg.postgres_db,
            user=cfg.postgres_user,
            password=cfg.postgres_password,
        )
    except Exception:
        # Fallback: naive libpq conninfo. This may break if values contain spaces or quotes.
        return (
            f"host={cfg.postgres_host} port={cfg.postgres_port} dbname={cfg.postgres_db} "
            f"user={cfg.postgres_user} password={cfg.postgres_password}"
        )
