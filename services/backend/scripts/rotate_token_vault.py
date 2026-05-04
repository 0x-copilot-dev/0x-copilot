"""Operator CLI: rotate MCP token-vault ciphertexts to a new KMS CMK.

Iterates ``mcp_auth_connections`` rows where ``kms_key_id`` differs from the
target key (or is NULL — those are legacy Fernet rows). For each row:

    1. Decrypt with the source vault (current ``MCP_TOKEN_VAULT_BACKEND``).
    2. Re-encrypt with the target vault (env override).
    3. UPDATE ``encrypted_access_token`` / ``encrypted_refresh_token`` /
       ``kms_key_id`` in a single transaction.

Idempotent (re-running skips already-rotated rows) and resumable (the WHERE
clause naturally narrows after each batch). Designed to run during a brief
maintenance window per the C6 rollout plan in
``docs/security/key-rotation.md``.

Examples:
    # SaaS rollout: legacy Fernet rows → AWS KMS.
    BACKEND_DATABASE_URL=... \\
    MCP_TOKEN_VAULT_BACKEND=aws_kms \\
    MCP_TOKEN_VAULT_KMS_KEY_ID=alias/prod-mcp-cmk \\
    .venv/bin/python scripts/rotate_token_vault.py rotate \\
        --target-key-id alias/prod-mcp-cmk

    # Dry run.
    .venv/bin/python scripts/rotate_token_vault.py rotate \\
        --target-key-id alias/prod-mcp-cmk --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend_app.token_vault import (
    LocalTokenVault,
    ManagedSecretTokenVault,
    TokenVault,
    TokenVaultFactory,
)


_LOGGER = logging.getLogger("backend.rotate_token_vault")


def _resolve_database_url(args: argparse.Namespace) -> str:
    if args.db_url:
        return str(args.db_url)
    url = os.environ.get("BACKEND_DATABASE_URL")
    if not url:
        raise SystemExit("BACKEND_DATABASE_URL is required (or pass --db-url)")
    return url


def _build_source_vault() -> TokenVault:
    """The 'source' vault decrypts existing rows.

    For SaaS rotation we expect mostly legacy Fernet ciphertexts; for
    re-key (CMK-A → CMK-B) the source is the prior KMS adapter. Operators
    set ``MCP_TOKEN_VAULT_LEGACY_BACKEND=local`` (or similar) when a
    transitional override is needed.
    """

    legacy = os.environ.get("MCP_TOKEN_VAULT_LEGACY_BACKEND", "").strip().lower()
    if legacy == "local":
        return LocalTokenVault()
    # Default: use whatever ``TokenVaultFactory.create()`` would build.
    # The decrypt path is self-routing — KMS adapters parse the kms_v1
    # envelope, LocalTokenVault transparently decrypts Fernet + legacy XOR.
    return TokenVaultFactory.create()


def _build_target_vault(target_key_id: str) -> TokenVault:
    backend = os.environ.get("MCP_TOKEN_VAULT_BACKEND", "").strip().lower()
    if backend != "aws_kms":
        raise SystemExit(
            "Rotation requires MCP_TOKEN_VAULT_BACKEND=aws_kms (other "
            "adapters ship in follow-up PRs); set it before running."
        )
    # Override the env-pinned key id so the operator can rotate to a new key
    # without restarting the process or rewriting env files.
    os.environ["MCP_TOKEN_VAULT_KMS_KEY_ID"] = target_key_id
    return TokenVaultFactory.create()


def _select_rows(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    target_key_id: str,
    batch_size: int,
) -> list[dict[str, Any]]:
    sql = """
        SELECT connection_id, server_id, org_id, user_id,
               encrypted_access_token, encrypted_refresh_token,
               kms_key_id
          FROM mcp_auth_connections
         WHERE kms_key_id IS DISTINCT FROM %(target)s
         ORDER BY updated_at ASC
         LIMIT %(batch)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"target": target_key_id, "batch": batch_size})
        return list(cur.fetchall())


def _rotate_row(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    row: dict[str, Any],
    source: TokenVault,
    target: ManagedSecretTokenVault,
    target_key_id: str,
    dry_run: bool,
) -> None:
    plaintext_access = source.decrypt(str(row["encrypted_access_token"]))
    plaintext_refresh: str | None = None
    if row.get("encrypted_refresh_token") is not None:
        plaintext_refresh = source.decrypt(str(row["encrypted_refresh_token"]))

    new_access = target.encrypt(plaintext_access)
    new_refresh = (
        target.encrypt(plaintext_refresh) if plaintext_refresh is not None else None
    )
    new_key_id = target.key_id_for(new_access)

    _LOGGER.info(
        "rotating connection_id=%s server_id=%s old_key_id=%s -> new_key_id=%s",
        row["connection_id"],
        row["server_id"],
        row["kms_key_id"],
        new_key_id,
    )

    if dry_run:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mcp_auth_connections
               SET encrypted_access_token = %(access)s,
                   encrypted_refresh_token = %(refresh)s,
                   kms_key_id = %(key_id)s,
                   updated_at = NOW()
             WHERE connection_id = %(id)s
               AND kms_key_id IS DISTINCT FROM %(target)s
            """,
            {
                "access": new_access,
                "refresh": new_refresh,
                "key_id": new_key_id,
                "id": row["connection_id"],
                "target": target_key_id,
            },
        )


def _run_rotate(args: argparse.Namespace) -> int:
    target_key_id = args.target_key_id
    if not target_key_id:
        raise SystemExit("--target-key-id is required")

    source = _build_source_vault()
    target_vault = _build_target_vault(target_key_id)
    if not isinstance(target_vault, ManagedSecretTokenVault):
        raise SystemExit("Target vault must be a ManagedSecretTokenVault (KMS-backed).")

    db_url = _resolve_database_url(args)
    rotated = 0
    skipped = 0
    with psycopg.connect(db_url, autocommit=False, row_factory=dict_row) as conn:
        while True:
            rows = _select_rows(
                conn, target_key_id=target_key_id, batch_size=args.batch_size
            )
            if not rows:
                break
            for row in rows:
                try:
                    _rotate_row(
                        conn,
                        row=row,
                        source=source,
                        target=target_vault,
                        target_key_id=target_key_id,
                        dry_run=args.dry_run,
                    )
                    rotated += 1
                except Exception as exc:  # noqa: BLE001 — operator-driven CLI
                    _LOGGER.exception(
                        "rotation failed for connection_id=%s: %s",
                        row["connection_id"],
                        exc,
                    )
                    skipped += 1
            if args.dry_run:
                # Without commits the WHERE clause never narrows; bail
                # after the first batch in dry-run mode.
                conn.rollback()
                break
            conn.commit()
    _LOGGER.info(
        "rotation complete: rotated=%d skipped=%d target_key_id=%s",
        rotated,
        skipped,
        target_key_id,
    )
    return 0 if skipped == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-url", help="Override BACKEND_DATABASE_URL.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rotate = sub.add_parser("rotate", help="Re-encrypt rows to a new CMK.")
    rotate.add_argument("--target-key-id", required=True)
    rotate.add_argument("--batch-size", type=int, default=100)
    rotate.add_argument("--dry-run", action="store_true")
    rotate.set_defaults(handler=_run_rotate)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = args.handler
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
