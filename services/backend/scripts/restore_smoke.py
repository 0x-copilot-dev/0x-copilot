"""C12 backend restore-drill smoke check.

CI workflow ``.github/workflows/postgres-restore-drill.yml`` boots a
clean Postgres, applies every migration, loads
``tests/fixtures/postgres-restore/seed.sql``, then runs this script. We
verify that:

  1. Every backend table named in the manifest has the expected COUNT(*).
  2. Cross-tenant isolation holds (org_drill_a has no rows for
     org_drill_b's auth_providers and vice versa).

Exit codes:
    0  — restore drill green.
    1  — DB error or manifest mismatch.

Usage:
    .venv/bin/python scripts/restore_smoke.py
    .venv/bin/python scripts/restore_smoke.py --db-url postgres://...
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
import yaml


_LOGGER = logging.getLogger("backend.restore_smoke")

_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "postgres-restore"
    / "manifest.yaml"
)


def _load_manifest() -> dict[str, int]:
    with _MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    section = data.get("backend") or {}
    if not isinstance(section, dict):
        raise SystemExit(
            "manifest.yaml is missing the 'backend:' section or has wrong shape"
        )
    return {str(table): int(count) for table, count in section.items()}


def _table_count(cursor: Any, table: str) -> int | None:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except psycopg.errors.UndefinedTable:
        return None


def _check_cross_tenant_isolation(cursor: Any) -> list[str]:
    """The seed populates two orgs; cross-tenant lookups must never leak."""
    failures: list[str] = []
    cursor.execute(
        "SELECT COUNT(*) FROM auth_providers WHERE org_id = %s",
        ("org_drill_b",),
    )
    row = cursor.fetchone()
    if row and int(row[0]) != 1:
        failures.append(
            f"expected exactly one auth_provider for org_drill_b, got {row[0]}"
        )
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE org_id = %s AND primary_email LIKE %s",
        ("org_drill_a", "%@drill-b.example"),
    )
    row = cursor.fetchone()
    if row and int(row[0]) != 0:
        failures.append(
            f"cross-tenant leak: org_drill_a holds drill-b emails ({row[0]} rows)"
        )
    return failures


def _run(db_url: str) -> tuple[dict[str, int], list[str]]:
    expected = _load_manifest()
    counts: dict[str, int] = {}
    failures: list[str] = []
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table, want in expected.items():
                got = _table_count(cur, table)
                if got is None:
                    failures.append(f"table {table!r} missing — migration not applied?")
                    counts[table] = -1
                    continue
                counts[table] = got
                if got != want:
                    failures.append(f"{table}: expected {want} rows, got {got}")
            failures.extend(_check_cross_tenant_isolation(cur))
    return counts, failures


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="restore_smoke")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("BACKEND_DATABASE_URL")
        or os.environ.get("DATABASE_URL"),
        help="Postgres URL (defaults to BACKEND_DATABASE_URL or DATABASE_URL).",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.db_url:
        _LOGGER.error("Postgres URL required (--db-url or BACKEND_DATABASE_URL)")
        return 1

    try:
        counts, failures = _run(args.db_url)
    except Exception as exc:
        _LOGGER.error("query failed: %s", exc)
        return 1

    sys.stdout.write("backend restore smoke counts:\n")
    for table, count in counts.items():
        marker = " " if count != -1 else "!"
        sys.stdout.write(f"  {marker} {table:40s}  {count}\n")

    if failures:
        sys.stdout.write("\nFAIL:\n")
        for failure in failures:
            sys.stdout.write(f"  - {failure}\n")
        return 1

    sys.stdout.write("\nrestore drill: OK\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
