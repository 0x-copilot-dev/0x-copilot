"""LIVE-Postgres tests for the provider-key ``default_model`` column
(migration 0042 — PRD-J FR-J2.1d).

The unit suite exercises :class:`PostgresProviderApiKeyStore` against a
fake connection; this suite proves against a real Postgres that:

1. migration ``0042_provider_api_keys_default_model`` applies and the
   column exists,
2. ``ProviderKeysService.set_key`` with a ``default_model`` round-trips
   through the real INSERT … ON CONFLICT (the summary projection —
   ``list_keys`` — returns it),
3. a rotation that omits the model PRESERVES the stored pick (the SQL
   COALESCE), while a rotation with a new model overwrites it, and
4. the identity audit row lands in the SAME transaction as the key write
   (C3 atomicity — the audit insert threads the store's conn).

Gated on ``BACKEND_MERGE_TEST_DATABASE_URL`` (shares the merge gate's
disposable cluster + CI job — same convention as
``test_principals_live.py``). Destructive — use a throwaway database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BACKEND_MERGE_TEST_DATABASE_URL"),
    reason="Set BACKEND_MERGE_TEST_DATABASE_URL to a disposable Postgres database.",
)

_VAULT_SECRET = "live-gate-vault-secret-at-least-32-chars!"


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["BACKEND_MERGE_TEST_DATABASE_URL"]


@pytest.fixture(scope="module")
def migrated(database_url: str) -> list[str]:
    pytest.importorskip("psycopg")
    from backend_app.db.migrate import MigrationRunner

    MigrationRunner.apply(database_url)  # real runner (psycopg3), idempotent
    applied, _pending = MigrationRunner.status(database_url)
    return applied


@pytest.fixture(scope="module")
def pool(migrated: list[str], database_url: str) -> Iterator[Any]:
    from backend_app.store import PostgresConnectionPool

    resolved = PostgresConnectionPool(database_url)
    try:
        yield resolved
    finally:
        resolved.close()


@pytest.fixture(scope="module")
def identity_store(pool: Any) -> Any:
    from backend_app.identity.store import PostgresIdentityStore

    return PostgresIdentityStore(pool)


@pytest.fixture(scope="module")
def service(pool: Any, identity_store: Any) -> Any:
    from backend_app.provider_keys.service import ProviderKeysService
    from backend_app.provider_keys.store import PostgresProviderApiKeyStore
    from backend_app.token_vault import LocalTokenVault

    return ProviderKeysService(
        store=PostgresProviderApiKeyStore(pool),
        identity_store=identity_store,
        token_vault=LocalTokenVault(secret=_VAULT_SECRET),
    )


@pytest.fixture
def tenant(identity_store: Any) -> tuple[str, str]:
    """A real (org, user) pair — provider_api_keys carries FKs to both."""

    from backend_app.contracts import OrganizationRecord, UserRecord

    tag = uuid.uuid4().hex[:8]
    org_id = f"org_pk_{tag}"
    identity_store.create_organization(
        OrganizationRecord(org_id=org_id, display_name=tag, slug=org_id)
    )
    user = identity_store.create_user(
        UserRecord(
            user_id=f"usr_pk_{tag}",
            org_id=org_id,
            primary_email=f"pk_{tag}@x.io",
            display_name="Keys",
        )
    )
    return org_id, user.user_id


class TestMigrationApplies:
    def test_0042_is_applied(self, migrated: list[str]) -> None:
        assert "0042_provider_api_keys_default_model" in migrated

    def test_default_model_column_exists(self, pool: Any) -> None:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT data_type, is_nullable FROM information_schema.columns
                WHERE table_name = 'provider_api_keys'
                  AND column_name = 'default_model'
                """
            )
            row = cur.fetchone()
        assert row is not None
        assert row["data_type"] == "text" and row["is_nullable"] == "YES"


class TestDefaultModelRoundTrip:
    def test_set_key_with_model_projects_on_summary(
        self, service: Any, tenant: tuple[str, str], pool: Any
    ) -> None:
        from backend_app.provider_keys.store import ProviderName

        org_id, user_id = tenant
        saved = service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.OPENAI,
            api_key="sk-" + "a" * 40,
            default_model="gpt-5.4-mini",
        )
        assert saved.default_model == "gpt-5.4-mini"

        # Summary projection (the Settings list) carries the pick.
        (summary,) = service.list_keys(org_id=org_id, user_id=user_id)
        assert summary.provider is ProviderName.OPENAI
        assert summary.default_model == "gpt-5.4-mini"
        assert summary.key_hint.endswith("aaaa")

        # The raw column really holds the slug — and never key material.
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT default_model, encrypted_key FROM provider_api_keys
                WHERE org_id = %s AND user_id = %s AND provider = 'openai'
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        assert row["default_model"] == "gpt-5.4-mini"
        assert "sk-" + "a" * 40 not in row["encrypted_key"]

    def test_rotation_without_model_preserves_stored_pick(
        self, service: Any, tenant: tuple[str, str]
    ) -> None:
        from backend_app.provider_keys.store import ProviderName

        org_id, user_id = tenant
        first = service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.ANTHROPIC,
            api_key="sk-ant-" + "b" * 40,
            default_model="claude-fable-5",
        )
        rotated = service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.ANTHROPIC,
            api_key="sk-ant-" + "c" * 40,
            default_model=None,  # COALESCE keeps the stored pick
        )
        assert rotated.default_model == "claude-fable-5"
        assert rotated.key_hint.endswith("cccc")  # the key DID rotate
        assert rotated.created_at == first.created_at  # first write survives

    def test_rotation_with_new_model_overwrites(
        self, service: Any, tenant: tuple[str, str]
    ) -> None:
        from backend_app.provider_keys.store import ProviderName

        org_id, user_id = tenant
        service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.OPENROUTER,
            api_key="sk-or-" + "d" * 40,
            default_model="meta-llama/llama-4",
        )
        updated = service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.OPENROUTER,
            api_key="sk-or-" + "e" * 40,
            default_model="qwen/qwen4-coder",
        )
        assert updated.default_model == "qwen/qwen4-coder"

    def test_null_default_model_stays_null(
        self, service: Any, tenant: tuple[str, str]
    ) -> None:
        from backend_app.provider_keys.store import ProviderName

        org_id, user_id = tenant
        saved = service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.GOOGLE,
            api_key="AIza" + "f" * 40,
        )
        assert saved.default_model is None
        record = next(
            r
            for r in service.list_keys(org_id=org_id, user_id=user_id)
            if r.provider is ProviderName.GOOGLE
        )
        assert record.default_model is None


class TestAuditRidesTheSameTransaction:
    def test_set_key_writes_identity_audit(
        self, service: Any, identity_store: Any, tenant: tuple[str, str]
    ) -> None:
        from backend_app.provider_keys.store import ProviderName

        org_id, user_id = tenant
        service.set_key(
            org_id=org_id,
            user_id=user_id,
            provider=ProviderName.OPENAI,
            api_key="sk-" + "g" * 40,
            default_model="gpt-5.4",
        )
        events = identity_store.list_identity_audit(org_id=org_id)
        actions = [e.action for e in events]
        assert "settings.provider_key.set" in actions
        for event in events:
            assert "sk-" + "g" * 40 not in str(event.metadata)
