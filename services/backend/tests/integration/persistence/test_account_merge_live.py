"""LIVE-Postgres integration test for the account-merge saga (PRD §8 gate).

Skipped when ``BACKEND_MERGE_TEST_DATABASE_URL`` is unset. Destructive — use
a disposable database. This is the backend half of the live gate the PRD has
required since the merge engine shipped: the registry SQL, the saga, the
session revocation, the lineage stamp, the 0038 audit-immutability trigger,
and the documented RLS caveat are all exercised against a REAL schema —
in-memory analogues prove none of that.

Run:

    BACKEND_MERGE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/merge_backend_test \\
        .venv/bin/python -m pytest tests/integration/persistence/test_account_merge_live.py
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason=(
        "Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres "
        "database to exercise the live account-merge gate."
    ),
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def pool(database_url: str) -> Iterator[Any]:
    pytest.importorskip("psycopg")

    from backend_app.db.migrate import MigrationRunner
    from backend_app.store import PostgresConnectionPool

    # Through the real runner (psycopg3 driver, same path production uses) —
    # never a bare yoyo.get_backend, which would pull in psycopg2.
    MigrationRunner.apply(database_url)

    # The saga is tested in the CURRENT deployment posture: RLS dormant
    # (staged do_rls.sql not applied). The RLS-enforced caveat gets its own
    # explicit test below.
    import psycopg

    undo_rls = (MIGRATIONS_DIR / "staged" / "undo_rls.sql").read_text()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(undo_rls)

    resolved = PostgresConnectionPool(database_url)
    try:
        yield resolved
    finally:
        resolved.close()


def _mk_account(pool: Any, tag: str) -> tuple[str, str]:
    """A personal org + sole admin-ish member, via the REAL identity adapter."""

    from backend_app.contracts import (
        OrganizationMemberRecord,
        OrganizationMemberSource,
        OrganizationRecord,
        UserRecord,
    )
    from backend_app.identity.store import PostgresIdentityStore

    identity = PostgresIdentityStore(pool)
    suffix = uuid.uuid4().hex[:10]
    org_id = f"org_{tag}_{suffix}"
    user_id = f"usr_{tag}_{suffix}"
    identity.create_organization(
        OrganizationRecord(org_id=org_id, display_name=tag, slug=f"{tag}-{suffix}")
    )
    identity.create_user(
        UserRecord(
            user_id=user_id,
            org_id=org_id,
            primary_email=f"{tag}-{suffix}@merge.test",
            display_name=tag,
        )
    )
    identity.add_member(
        OrganizationMemberRecord(
            org_id=org_id, user_id=user_id, source=OrganizationMemberSource.SIWE
        )
    )
    return org_id, user_id


def _seed_rows(pool: Any, org_id: str, user_id: str, *, wallet: str) -> None:
    """Rows across every strategy class, via raw SQL against the REAL DDL."""

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wallet_identities "
                "(wallet_id, address, org_id, user_id, chain_id, created_at, "
                "principal_id) "
                "VALUES (%s, %s, %s, %s, 8453, now(), %s)",
                (f"wid_{uuid.uuid4().hex}", wallet, org_id, user_id, f"prn_{user_id}"),
            )
            # Singleton class (survivor-wins).
            cur.execute(
                "INSERT INTO user_profiles (user_id, org_id, title) "
                "VALUES (%s, %s, %s)",
                (user_id, org_id, f"title-of-{user_id}"),
            )
            # Keyed class: same provider on both sides → collision.
            cur.execute(
                "INSERT INTO provider_api_keys "
                "(org_id, user_id, provider, encrypted_key, key_hint) "
                "VALUES (%s, %s, 'openai', %s, '…1234')",
                (org_id, user_id, f"enc-key-of-{user_id}"),
            )
            # tenant_id / owner_user_id naming (the CC-1 regression class).
            cur.execute(
                "INSERT INTO todos (id, tenant_id, owner_user_id, text) "
                "VALUES (%s, %s, %s, %s)",
                (f"todo_{uuid.uuid4().hex}", org_id, user_id, f"todo-{user_id}"),
            )
            # Keyed on name: same skill name both sides → collision.
            cur.execute(
                "INSERT INTO skills (skill_id, org_id, user_id, name, "
                "display_name, description, markdown, virtual_path, scope, "
                "source_type, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'shared-skill', 'Shared', 'd', 'm', %s, "
                "'user', 'inline', now(), now())",
                (f"skl_{uuid.uuid4().hex}", org_id, user_id, f"/skills/{user_id}"),
            )
            # DROP class: MFA factor + its child secret (join-drop, FK order).
            factor_id = f"mfa_{uuid.uuid4().hex}"
            cur.execute(
                "INSERT INTO mfa_factors (factor_id, org_id, user_id, kind, "
                "display_name, enrolled_at) "
                "VALUES (%s, %s, %s, 'totp', 'phone', now())",
                (factor_id, org_id, user_id),
            )
            cur.execute(
                "INSERT INTO totp_secrets (secret_id, factor_id, "
                "encrypted_secret, created_at) VALUES (%s, %s, 'enc', now())",
                (f"tot_{uuid.uuid4().hex}", factor_id),
            )
            # An active session (revoked by the saga, never adopted).
            cur.execute(
                "INSERT INTO sessions (session_id, org_id, user_id, "
                "token_hash, created_at, last_seen_at, expires_at) "
                "VALUES (%s, %s, %s, %s, now(), now(), %s)",
                (
                    f"ses_{uuid.uuid4().hex}",
                    org_id,
                    user_id,
                    uuid.uuid4().hex,
                    _now() + timedelta(hours=1),
                ),
            )


def _count(pool: Any, table: str, org_col: str, org_id: str) -> int:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE {org_col} = %s",
                (org_id,),
            )
            row = cur.fetchone()
            return int(row["n"])


def _service(pool: Any, runtime_port: Any) -> Any:
    from backend_app.identity.account_merge import (
        AccountMergeService,
        PostgresMergeData,
    )
    from backend_app.identity.account_merge_store import PostgresAccountMergeStore
    from backend_app.identity.session_store import PostgresSessionStore
    from backend_app.identity.sessions import SessionService
    from backend_app.identity.store import PostgresIdentityStore

    return AccountMergeService(
        identity_store=PostgresIdentityStore(pool),
        merge_store=PostgresAccountMergeStore(pool),
        sessions=SessionService(
            store=PostgresSessionStore(pool),
            auth_secret="merge-live-test-secret-0123456789",
            dev_mint_allowed=True,
        ),
        data_port=PostgresMergeData(pool),
        runtime_port=runtime_port,
    )


class TestAccountMergeLive:
    def test_full_saga_on_real_schema(self, pool: Any) -> None:
        from backend_app.contracts import AccountMergeState
        from backend_app.identity.account_merge import NullRuntimeMergeClient

        survivor = _mk_account(pool, "survivor")
        absorbed = _mk_account(pool, "absorbed")
        decoy = _mk_account(pool, "decoy")
        wallets = {
            tag: f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"[:42]
            for tag in ("survivor", "absorbed", "decoy")
        }
        _seed_rows(pool, *survivor, wallet=wallets["survivor"])
        _seed_rows(pool, *absorbed, wallet=wallets["absorbed"])
        _seed_rows(pool, *decoy, wallet=wallets["decoy"])

        service = _service(pool, NullRuntimeMergeClient())
        record = service.merge_for_conflict(
            survivor_org_id=survivor[0],
            survivor_user_id=survivor[1],
            absorbed_org_id=absorbed[0],
            absorbed_user_id=absorbed[1],
            proof_ref="siwe:live-gate",
        )
        assert record.state == AccountMergeState.COMPLETED

        # Wallet + todo (tenant_id naming) moved to the survivor.
        assert _count(pool, "wallet_identities", "org_id", absorbed[0]) == 0
        assert _count(pool, "wallet_identities", "org_id", survivor[0]) == 2
        assert _count(pool, "todos", "tenant_id", absorbed[0]) == 0
        assert _count(pool, "todos", "tenant_id", survivor[0]) == 2
        # Collisions resolved survivor-wins: one profile, one openai key,
        # one 'shared-skill'.
        assert _count(pool, "user_profiles", "org_id", survivor[0]) == 1
        assert _count(pool, "provider_api_keys", "org_id", survivor[0]) == 1
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT encrypted_key FROM provider_api_keys "
                    "WHERE org_id = %s AND provider = 'openai'",
                    (survivor[0],),
                )
                assert cur.fetchone()["encrypted_key"] == (f"enc-key-of-{survivor[1]}")
                # Security material dropped, incl. the FK child (join order).
                cur.execute(
                    "SELECT count(*) AS n FROM mfa_factors WHERE org_id = %s",
                    (absorbed[0],),
                )
                assert cur.fetchone()["n"] == 0
                # survivor + decoy factors keep their children; absorbed's gone.
                # (Scoped through mfa_factors — the DB accumulates across runs.)
                cur.execute(
                    "SELECT count(*) AS n FROM totp_secrets t "
                    "JOIN mfa_factors f ON f.factor_id = t.factor_id "
                    "WHERE f.org_id = ANY(%s)",
                    ([survivor[0], absorbed[0], decoy[0]],),
                )
                assert cur.fetchone()["n"] == 2
                # Absorbed sessions revoked (not adopted).
                cur.execute(
                    "SELECT count(*) AS n FROM sessions "
                    "WHERE org_id = %s AND revoked_at IS NULL",
                    (absorbed[0],),
                )
                assert cur.fetchone()["n"] == 0
                # Lineage stamped on the real users row (0038 columns).
                cur.execute(
                    "SELECT status, absorbed_into_user_id, merged_at, "
                    "deleted_at FROM users WHERE user_id = %s",
                    (absorbed[1],),
                )
                row = cur.fetchone()
                assert row["status"] == "disabled"
                assert row["absorbed_into_user_id"] == survivor[1]
                assert row["merged_at"] is not None
                # account.merged audit on BOTH orgs.
                for org in (survivor[0], absorbed[0]):
                    cur.execute(
                        "SELECT count(*) AS n FROM identity_audit_events "
                        "WHERE org_id = %s AND action = 'account.merged'",
                        (org,),
                    )
                    assert cur.fetchone()["n"] == 1

        # DECOY completely untouched.
        assert _count(pool, "wallet_identities", "org_id", decoy[0]) == 1
        assert _count(pool, "todos", "tenant_id", decoy[0]) == 1
        assert _count(pool, "user_profiles", "org_id", decoy[0]) == 1
        assert _count(pool, "mfa_factors", "org_id", decoy[0]) == 1

        # Idempotency (NFR-8): re-merge is the completed no-op.
        again = service.merge_for_conflict(
            survivor_org_id=survivor[0],
            survivor_user_id=survivor[1],
            absorbed_org_id=absorbed[0],
            absorbed_user_id=absorbed[1],
            proof_ref="siwe:live-gate",
        )
        assert again.merge_id == record.merge_id
        assert _count(pool, "wallet_identities", "org_id", survivor[0]) == 2

    def test_audit_immutability_trigger_live(self, pool: Any) -> None:
        """0038's identity_audit_events guard on a REAL database (NFR-5)."""

        import psycopg

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT audit_id FROM identity_audit_events "
                    "WHERE action = 'account.merged' LIMIT 1"
                )
                row = cur.fetchone()
                assert row is not None, "expected a merge audit row from the saga"
                with pytest.raises(psycopg.errors.RaiseException):
                    cur.execute(
                        "UPDATE identity_audit_events SET action = 'tampered' "
                        "WHERE audit_id = %s",
                        (row["audit_id"],),
                    )
        with pool.connection() as conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg.errors.RaiseException):
                    cur.execute(
                        "DELETE FROM identity_audit_events WHERE audit_id = %s",
                        (row["audit_id"],),
                    )

    def test_failure_checkpoint_then_resume(self, pool: Any) -> None:
        """NFR-3/8 on real Postgres: runtime failure stops at backend_done;
        the retry resumes and completes — nothing half-owned in between."""

        from backend_app.contracts import AccountMergeState
        from backend_app.identity.account_merge import (
            MergeRuntimeFailed,
            NullRuntimeMergeClient,
        )

        survivor = _mk_account(pool, "surv2")
        absorbed = _mk_account(pool, "abs2")
        _seed_rows(pool, *survivor, wallet=f"0x{uuid.uuid4().hex}{'a' * 10}"[:42])
        _seed_rows(pool, *absorbed, wallet=f"0x{uuid.uuid4().hex}{'b' * 10}"[:42])

        class _FailingRuntime:
            def merge(self, **kwargs: Any) -> dict[str, Any]:
                raise MergeRuntimeFailed("runtime down (injected)")

        failing = _service(pool, _FailingRuntime())
        with pytest.raises(MergeRuntimeFailed):
            failing.merge_for_conflict(
                survivor_org_id=survivor[0],
                survivor_user_id=survivor[1],
                absorbed_org_id=absorbed[0],
                absorbed_user_id=absorbed[1],
                proof_ref="siwe:resume-gate",
            )
        # Backend re-key committed; absorbed user NOT yet disabled (nothing
        # destructive past the checkpoint).
        assert _count(pool, "wallet_identities", "org_id", survivor[0]) == 2
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM users WHERE user_id = %s", (absorbed[1],)
                )
                assert cur.fetchone()["status"] == "active"

        # Retry with the runtime back: resumes from backend_done → completed.
        recovered = _service(pool, NullRuntimeMergeClient())
        record = recovered.merge_for_conflict(
            survivor_org_id=survivor[0],
            survivor_user_id=survivor[1],
            absorbed_org_id=absorbed[0],
            absorbed_user_id=absorbed[1],
            proof_ref="siwe:resume-gate",
        )
        assert record.state == AccountMergeState.COMPLETED
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM users WHERE user_id = %s", (absorbed[1],)
                )
                assert cur.fetchone()["status"] == "disabled"

    def test_rls_enforced_requires_bypass_documented_caveat(
        self, pool: Any, database_url: str
    ) -> None:
        """Prove the documented RLS caveat is REAL: with FORCE RLS applied and
        a non-superuser role, the re-key matches zero rows — so RLS-enforced
        deployments MUST run the merge under BYPASSRLS (PRD §7 / module doc).
        The gate makes the failure mode observable instead of speculative."""

        import psycopg

        from backend_app.identity.account_merge import PostgresMergeData
        from backend_app.store import PostgresConnectionPool

        app_url = os.environ.get("BACKEND_MERGE_TEST_APP_DATABASE_URL")
        if not app_url:
            pytest.skip("BACKEND_MERGE_TEST_APP_DATABASE_URL (non-superuser) unset")

        survivor = _mk_account(pool, "surv3")
        absorbed = _mk_account(pool, "abs3")
        _seed_rows(pool, *absorbed, wallet=f"0x{uuid.uuid4().hex}{'c' * 10}"[:42])

        do_rls = (MIGRATIONS_DIR / "staged" / "do_rls.sql").read_text()
        undo_rls = (MIGRATIONS_DIR / "staged" / "undo_rls.sql").read_text()
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(do_rls)
        app_pool = PostgresConnectionPool(app_url)
        try:
            counts = PostgresMergeData(app_pool).rekey(
                absorbed_org_id=absorbed[0],
                absorbed_user_id=absorbed[1],
                survivor_org_id=survivor[0],
                survivor_user_id=survivor[1],
            )
            # The documented failure mode: RLS silently hides the rows.
            assert counts.get("wallet_identities", 0) == 0
            assert _count(pool, "wallet_identities", "org_id", absorbed[0]) == 1
        finally:
            app_pool.close()
            with psycopg.connect(database_url, autocommit=True) as conn:
                conn.execute(undo_rls)
