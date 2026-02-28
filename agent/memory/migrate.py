from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from agent.memory.config import MemoryConfig, build_postgres_dsn, load_memory_config

MIGRATIONS_DIR = Path(__file__).with_suffix("").parent / "migrations"

# Stable advisory lock key for migrations (arbitrary constant, but consistent).
MIGRATION_LOCK_KEY = 812734912734  # bigint


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str
    sql: str


def _list_migration_files() -> List[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted([p for p in MIGRATIONS_DIR.iterdir() if p.is_file() and p.name.endswith(".sql")])


def _checksum_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_migrations() -> List[Migration]:
    migrations: List[Migration] = []
    for p in _list_migration_files():
        raw = p.read_bytes()
        migrations.append(
            Migration(
                version=p.name.split(".")[0],
                path=p,
                checksum=_checksum_bytes(raw),
                sql=raw.decode("utf-8"),
            )
        )
    return migrations


def _connect(dsn: str):
    # Lazy import so the agent can run without DB deps when memory is disabled.
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def ensure_schema_migrations_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version text PRIMARY KEY,
          checksum text NOT NULL,
          applied_at timestamptz NOT NULL DEFAULT now()
        );
        """)


def _get_applied(conn) -> dict:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations;").fetchall()
    out = {}
    for r in rows:
        out[str(r[0])] = str(r[1])
    return out


def apply_migrations(
    *,
    dsn: str,
    migrations: Optional[Iterable[Migration]] = None,
) -> Tuple[int, List[str]]:
    """
    Apply pending migrations.

    Returns: (applied_count, applied_versions)
    """
    migs = list(migrations) if migrations is not None else load_migrations()
    applied_versions: List[str] = []

    with _connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_lock(%s);", (MIGRATION_LOCK_KEY,))
        try:
            ensure_schema_migrations_table(conn)
            applied = _get_applied(conn)

            for m in migs:
                prev = applied.get(m.version)
                if prev is not None:
                    if prev != m.checksum:
                        raise RuntimeError(
                            f"Migration checksum mismatch for {m.version}: db={prev[:12]} file={m.checksum[:12]}"
                        )
                    continue

                # One migration per transaction.
                with conn.transaction():
                    conn.execute(m.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s);",
                        (m.version, m.checksum),
                    )
                applied_versions.append(m.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s);", (MIGRATION_LOCK_KEY,))

    return len(applied_versions), applied_versions


def maybe_auto_migrate(cfg: Optional[MemoryConfig] = None) -> Tuple[bool, str]:
    """
    Auto-migrate on startup when DB_AUTO_MIGRATE=1 and Postgres is configured.

    Returns: (did_attempt, message)
    """
    cfg = cfg or load_memory_config()
    if not cfg.db_auto_migrate:
        return False, "DB_AUTO_MIGRATE is disabled"
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres DSN not configured"
    try:
        n, versions = apply_migrations(dsn=dsn)
        if n:
            return True, f"Applied {n} migration(s): {', '.join(versions)}"
        return True, "No pending migrations"
    except Exception as e:
        return True, f"Migration failed: {e}"


def main(argv: Optional[List[str]] = None) -> int:
    _ = argv  # unused for now; keep CLI simple.
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        print("Postgres not configured (set POSTGRES_DSN or POSTGRES_* env vars).")
        return 2
    n, versions = apply_migrations(dsn=dsn)
    if n:
        print(f"Applied {n} migration(s): {', '.join(versions)}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
