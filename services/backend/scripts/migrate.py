"""Operator CLI for backend service migrations.

Examples:
    # Apply all pending migrations
    .venv/bin/python scripts/migrate.py apply

    # Roll back to a specific migration id (exclusive)
    .venv/bin/python scripts/migrate.py rollback --to 0001_initial_mcp_skills

    # Show applied/pending status
    .venv/bin/python scripts/migrate.py status

The DB URL is read from ``BACKEND_DATABASE_URL``; pass ``--db-url`` to override.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from backend_app.db.migrate import MigrationRunner


_LOGGER = logging.getLogger("backend.migrate")


def _resolve_database_url(args: argparse.Namespace) -> str:
    if args.db_url:
        return args.db_url
    url = os.environ.get("BACKEND_DATABASE_URL", "").strip()
    if not url:
        sys.stderr.write(
            "ERROR: set BACKEND_DATABASE_URL or pass --db-url=postgresql://...\n"
        )
        raise SystemExit(2)
    return url


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    parser = argparse.ArgumentParser(prog="backend-migrate")
    parser.add_argument("--db-url", default=None, help="Override BACKEND_DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("apply", help="Apply all pending migrations")
    rollback = sub.add_parser("rollback", help="Roll back applied migrations")
    rollback.add_argument(
        "--to",
        default=None,
        help=(
            "Highest migration id to KEEP. Migrations strictly greater than "
            "this id are rolled back. Omit to roll back everything."
        ),
    )
    sub.add_parser("status", help="Show applied vs pending migrations")

    args = parser.parse_args(argv)
    database_url = _resolve_database_url(args)

    if args.command == "apply":
        applied = MigrationRunner.apply(database_url)
        if applied:
            _LOGGER.info(
                "applied %d migration(s): %s", len(applied), ", ".join(applied)
            )
        else:
            _LOGGER.info("no pending migrations")
        return 0

    if args.command == "rollback":
        rolled = MigrationRunner.rollback(database_url, to=args.to)
        if rolled:
            _LOGGER.info(
                "rolled back %d migration(s): %s", len(rolled), ", ".join(rolled)
            )
        else:
            _LOGGER.info("nothing to roll back")
        return 0

    if args.command == "status":
        applied, pending = MigrationRunner.status(database_url)
        sys.stdout.write(f"applied: {applied or '[]'}\npending: {pending or '[]'}\n")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
