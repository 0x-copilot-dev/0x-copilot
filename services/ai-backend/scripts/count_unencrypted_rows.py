"""C7 phase 3: count rows still at ``encryption_version=0`` per target.

Operator precondition check before flipping
``RUNTIME_FIELD_ENCRYPTION_STRICT_READS=true``.  The strict-reads gate
makes any ``encryption_version=0`` read raise — so we MUST verify
backfill is complete first.

Examples:

    .venv/bin/python scripts/count_unencrypted_rows.py
    .venv/bin/python scripts/count_unencrypted_rows.py --db-url postgres://…
    .venv/bin/python scripts/count_unencrypted_rows.py --json

Exit codes:

    0   — every targeted table has zero v0 rows; safe to flip strict reads.
    2   — at least one table still holds v0 rows; backfill is incomplete.
    1   — DB error or misconfiguration.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import psycopg


_LOGGER = logging.getLogger("ai_backend.count_unencrypted_rows")


# Mirrors the targeted tables from
# ``runtime_worker.jobs.encrypt_existing_columns._DEFAULT_TARGETS`` plus
# the schema-prepared tables that don't yet have writers (we still want
# the count so operators can verify they are empty before flipping the
# gate). One row per (table, column) so the report shows where the
# remaining v0 rows live by column, not just by table.
_TARGET_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("agent_messages", ("content_text", "content_json", "metadata_json")),
    ("runtime_audit_log", ("metadata_json_redacted",)),
    (
        "runtime_events",
        ("payload_json_redacted", "metadata_json_redacted"),
    ),
    ("runtime_subagent_results", ("response_text",)),
    (
        "runtime_tool_invocations",
        ("args_json_redacted", "result_summary_json_redacted"),
    ),
    ("runtime_memory_items", ("content_summary",)),
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="count_unencrypted_rows",
        description=(
            "C7 phase 3 precondition: count rows at "
            "encryption_version=0 across targeted tables."
        ),
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("RUNTIME_DATABASE_URL"),
        help="Postgres URL (defaults to RUNTIME_DATABASE_URL).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human report.",
    )
    return parser.parse_args(argv)


def _count_for_table(cursor: psycopg.Cursor, table: str) -> int:
    cursor.execute(
        # encryption_version is the per-row flag; we count regardless of
        # whether the target columns are NULL because a v0 row holds
        # plaintext for *whichever* target columns it populated.
        f"SELECT COUNT(*) FROM {table} WHERE encryption_version = 0"
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _run(db_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table, _columns in _TARGET_TABLES:
                try:
                    counts[table] = _count_for_table(cur, table)
                except psycopg.errors.UndefinedTable:
                    # Table doesn't exist in this deploy yet — ignore.
                    counts[table] = 0
                except psycopg.errors.UndefinedColumn:
                    _LOGGER.warning(
                        "table %s missing encryption_version column; "
                        "migration 0011 may not have been applied",
                        table,
                    )
                    counts[table] = -1
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.db_url:
        _LOGGER.error(
            "Postgres URL required: pass --db-url or set RUNTIME_DATABASE_URL"
        )
        return 1

    try:
        counts = _run(args.db_url)
    except Exception as exc:
        _LOGGER.error("query failed: %s", exc)
        return 1

    if args.json:
        sys.stdout.write(
            json.dumps({"unencrypted_row_counts": counts}, sort_keys=True) + "\n"
        )
    else:
        sys.stdout.write("encryption_version=0 row counts per table:\n")
        for table, count in counts.items():
            label = "MISSING COLUMN" if count == -1 else str(count)
            sys.stdout.write(f"  {table:36s}  {label}\n")

    if any(c == -1 for c in counts.values()):
        # Schema mismatch — refuse to declare success.
        return 1
    if any(c > 0 for c in counts.values()):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
