"""CLI entrypoint for the offline Postgres/in-memory -> file-store migration.

Run this **before** flipping the desktop file store on (before setting
``COPILOT_DESKTOP_FILE_STORE_V1`` / ``RUNTIME_STORE_BACKEND=file``). It changes
no default — it only copies existing history into the file store so the store is
non-empty when the operator later switches the backend.

Operator flow (see ``docs/operations/desktop-file-store-migration.md``):

    # 1. Dry-run — reports what would move, writes nothing.
    python -m runtime_adapters.migrate \
        --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
        --dest-root "$HOME/.../agent-data/v1" --org-id ORG --user-id USER --dry-run

    # 2. Real migration.
    python -m runtime_adapters.migrate \
        --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
        --dest-root "$HOME/.../agent-data/v1" --org-id ORG --user-id USER

    # 3. Verify (also runnable inline with --verify on step 2).
    python -m runtime_adapters.migrate \
        --source postgres --source-database-url "$AI_BACKEND_DATABASE_URL" \
        --dest-root "$HOME/.../agent-data/v1" --org-id ORG --user-id USER --verify-only

Only a clean verify authorises the backend flip. Any mismatch exits non-zero and
leaves the source store authoritative.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from runtime_adapters.file.migration import (
    MigrationScope,
    MigrationVerificationError,
    StoreMigrator,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m runtime_adapters.migrate",
        description="Migrate runtime conversations into the desktop file store.",
    )
    parser.add_argument(
        "--source",
        choices=("postgres", "in_memory"),
        default="postgres",
        help="Source store backend to read through the port (default: postgres).",
    )
    parser.add_argument(
        "--source-database-url",
        default=None,
        help="DATABASE_URL for the Postgres source (required when --source=postgres).",
    )
    parser.add_argument(
        "--dest-root",
        required=True,
        help="Filesystem root of the destination file store.",
    )
    parser.add_argument(
        "--org-id",
        action="append",
        default=[],
        help="Org id to migrate (repeatable). Paired positionally with --user-id.",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        default=[],
        help="User id to migrate (repeatable). Paired positionally with --org-id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would migrate and write nothing.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the equality verify pass after migrating.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip writing; only verify an already-migrated destination.",
    )
    return parser


def _resolve_scopes(
    org_ids: Sequence[str], user_ids: Sequence[str]
) -> tuple[MigrationScope, ...] | None:
    if not org_ids and not user_ids:
        return None  # auto-discover (in-memory/file sources only)
    if len(org_ids) != len(user_ids):
        raise SystemExit(
            "error: --org-id and --user-id must be provided the same number of times"
        )
    return tuple(
        MigrationScope(org_id=org, user_id=user) for org, user in zip(org_ids, user_ids)
    )


def _build_source(args: argparse.Namespace):
    if args.source == "postgres":
        if not args.source_database_url:
            raise SystemExit(
                "error: --source-database-url is required for --source=postgres"
            )
        from runtime_adapters.postgres import PostgresRuntimeApiStore

        return PostgresRuntimeApiStore(args.source_database_url, role="migrate")
    from runtime_adapters.in_memory import InMemoryRuntimeApiStore

    return InMemoryRuntimeApiStore()


async def _run(args: argparse.Namespace) -> int:
    scopes = _resolve_scopes(args.org_id, args.user_id)
    source = _build_source(args)
    dest = FileRuntimeApiStore(args.dest_root)

    await source.open()
    await dest.open()
    try:
        migrator = StoreMigrator(source=source, dest=dest, progress=_print)
        if args.verify_only:
            report = await migrator.verify(scopes=scopes)
        else:
            report = await migrator.migrate(
                scopes=scopes, dry_run=args.dry_run, verify=args.verify
            )
    except MigrationVerificationError as exc:
        _print(f"VERIFY FAILED: {exc}")
        return 2
    finally:
        await dest.close()
        await source.close()

    _print(report.summary_line())
    return 0


def _print(message: str) -> None:
    print(message, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - module CLI shim
    sys.exit(main())
