from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return "unknown"
    return re.sub(r"[^a-z0-9._-]+", "_", s)[:80]


@dataclass(frozen=True)
class SkillDraft:
    name: str
    when_json: Dict[str, Any]
    template: str
    provenance: Dict[str, Any]


def _build_draft(*, family: str, primary_driver: str, count: int, run_ids: List[str]) -> SkillDraft:
    name = f"distilled/{_slug(family)}/{_slug(primary_driver)}"
    when_json: Dict[str, Any] = {
        "all": [
            {"eq": ["features.family", family]},
            {"eq": ["verdict.primary_driver", primary_driver]},
        ]
    }
    template = (
        f"**Pattern:** `{family}` / `{primary_driver}` observed in {count} recent runs.\n\n"
        "Suggested checks:\n"
        "- Confirm the primary driver signals in the evidence.\n"
        "- Check for recent rollouts/changes.\n"
        "- Validate resource limits/requests and saturation.\n\n"
        "Suggested mitigation (suggest-only):\n"
        "- Apply the lowest-risk mitigation that unblocks users, then continue investigation.\n"
    )
    return SkillDraft(
        name=name,
        when_json=when_json,
        template=template,
        provenance={"source": "distill", "count": count, "run_ids": run_ids},
    )


def distill_skill_drafts(*, min_count: int = 3, days: int = 14, limit: int = 25) -> Tuple[bool, str, List[SkillDraft]]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", []

    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT
              COALESCE(family, 'unknown') AS family,
              COALESCE(primary_driver, 'unknown') AS primary_driver,
              COUNT(*)::int AS n,
              ARRAY_AGG(run_id::text ORDER BY created_at DESC) AS run_ids
            FROM investigation_runs
            WHERE created_at >= (now() - (%s || ' days')::interval)
            GROUP BY family, primary_driver
            HAVING COUNT(*) >= %s
            ORDER BY n DESC
            LIMIT %s;
            """,
            (int(days), int(min_count), int(limit)),
        ).fetchall()

    drafts: List[SkillDraft] = []
    for r in rows or []:
        family = str(r[0] or "unknown")
        primary_driver = str(r[1] or "unknown")
        n = int(r[2] or 0)
        run_ids = list(r[3] or [])
        drafts.append(_build_draft(family=family, primary_driver=primary_driver, count=n, run_ids=run_ids[:10]))
    return True, "ok", drafts


def write_skill_drafts(drafts: List[SkillDraft]) -> Tuple[bool, str, int]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", 0

    created = 0
    with _connect(dsn) as conn:
        with conn.transaction():
            for d in drafts:
                exists = conn.execute(
                    "SELECT 1 FROM skills WHERE name = %s AND version = 1 LIMIT 1;",
                    (d.name,),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO skills(name, version, status, when_json, template, provenance)
                    VALUES (%s, 1, 'draft', %s::jsonb, %s, %s::jsonb);
                    """,
                    (
                        d.name,
                        json.dumps(d.when_json, ensure_ascii=False),
                        d.template,
                        json.dumps(d.provenance, ensure_ascii=False),
                    ),
                )
                created += 1
    return True, "ok", created


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Distill repeated case patterns into draft skills (no LLM).")
    p.add_argument("--min-count", type=int, default=3)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--write", action="store_true", help="Insert drafts into Postgres skills table (status=draft).")
    args = p.parse_args(argv)

    ok, msg, drafts = distill_skill_drafts(min_count=args.min_count, days=args.days, limit=args.limit)
    if not ok:
        print(msg)
        return 2

    print(json.dumps([d.__dict__ for d in drafts], indent=2, sort_keys=True))
    if args.write:
        ok2, msg2, n = write_skill_drafts(drafts)
        if not ok2:
            print(msg2)
            return 2
        print(f"Inserted {n} draft skill(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
