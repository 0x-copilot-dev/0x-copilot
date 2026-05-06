"""Tests for the PR B3 / 8.0.3g personal API-key routes.

Coverage matches the plan's "6 tests" line-item: list/create/delete,
rotate, last-used stamp via the auth verifier, scope-narrowing,
revoked-row rejection, and malformed-prefix parsing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.api_keys.auth import (
    ApiKeyHasher,
    InvalidApiKey,
    parse_bearer,
    render_bearer,
)
from backend_app.api_keys.store import InMemoryApiKeyStore
from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore


_PEPPER = b"test-pepper-bytes-2026-05-06!!!"


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, 9, 1, 24, tzinfo=timezone.utc),
        )
    )
    return store


def _client(
    *,
    identity_store: InMemoryIdentityStore | None = None,
    api_key_store: InMemoryApiKeyStore | None = None,
    api_key_pepper: bytes | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryApiKeyStore]:
    identity = identity_store or _seeded_identity()
    keys = api_key_store or InMemoryApiKeyStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        api_key_store=keys,
        api_key_pepper=api_key_pepper or _PEPPER,
    )
    return TestClient(app), identity, keys


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestBearerParser:
    def test_valid_bearer_round_trips(self) -> None:
        hasher = ApiKeyHasher(server_pepper=_PEPPER)
        prefix, secret = hasher.mint()
        bearer = render_bearer(prefix, secret)
        parsed = parse_bearer(bearer)
        assert parsed.prefix == prefix
        assert parsed.secret == secret

    def test_malformed_prefix_rejected(self) -> None:
        # Wrong sentinel.
        try:
            parse_bearer("atlas_sk_abcd_efgh")
        except InvalidApiKey:
            pass
        else:
            raise AssertionError("expected InvalidApiKey")
        # Right sentinel, wrong segment count.
        try:
            parse_bearer("atlas_pk_abcd")
        except InvalidApiKey:
            pass
        else:
            raise AssertionError("expected InvalidApiKey")
        # Right sentinel, non-hex prefix.
        try:
            parse_bearer("atlas_pk_zzzz_abcd")
        except InvalidApiKey:
            pass
        else:
            raise AssertionError("expected InvalidApiKey")


class TestCreateAndList:
    def test_create_returns_plaintext_once_then_listing_omits_it(self) -> None:
        client, identity, store = _client()

        # Create.
        response = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "ci-bot"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        plaintext = body["plaintext"]
        # Plaintext follows the wire shape so a CI bot can use it
        # verbatim as `Authorization: Bearer <plaintext>`.
        assert plaintext.startswith("atlas_pk_")
        api_key_id = body["key"]["id"]

        # List — plaintext field absent.
        response = client.get("/internal/v1/me/api-keys", params=_params())
        assert response.status_code == 200
        keys = response.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["id"] == api_key_id
        assert "plaintext" not in keys[0]
        assert keys[0]["label"] == "ci-bot"

        # Audit row landed.
        events = identity.list_identity_audit(org_id="org_acme")
        create_events = [e for e in events if e.action == "api_key.create"]
        assert len(create_events) == 1
        assert create_events[0].metadata["api_key_id"] == api_key_id

        # The stored row hashes the secret, not stores it.
        rows = store.list_for_user(org_id="org_acme", user_id="usr_sarah")
        assert len(rows) == 1
        assert rows[0].secret_hash != plaintext


class TestRevoke:
    def test_revoke_removes_from_listing_and_audit(self) -> None:
        client, identity, store = _client()
        created = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "deploy-bot"},
        ).json()
        api_key_id = created["key"]["id"]

        response = client.delete(
            f"/internal/v1/me/api-keys/{api_key_id}", params=_params()
        )
        assert response.status_code == 204
        assert (
            client.get("/internal/v1/me/api-keys", params=_params()).json()["keys"]
            == []
        )
        events = identity.list_identity_audit(org_id="org_acme")
        revoke_events = [e for e in events if e.action == "api_key.revoke"]
        assert len(revoke_events) == 1

    def test_revoke_unknown_id_is_404(self) -> None:
        client, _i, _s = _client()
        response = client.delete(
            "/internal/v1/me/api-keys/apikey_does_not_exist", params=_params()
        )
        assert response.status_code == 404


class TestRotate:
    def test_rotate_creates_linked_row_and_revokes_old(self) -> None:
        client, identity, store = _client()
        old = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "deploy-bot"},
        ).json()
        old_id = old["key"]["id"]

        response = client.post(
            f"/internal/v1/me/api-keys/{old_id}/rotate", params=_params()
        )
        assert response.status_code == 201, response.text
        new = response.json()
        assert new["key"]["rotated_from_id"] == old_id
        assert new["plaintext"] != old["plaintext"]

        # Old key revoked, new key active and listed.
        active = store.list_for_user(org_id="org_acme", user_id="usr_sarah")
        assert len(active) == 1
        assert active[0].id == new["key"]["id"]
        # All rows (incl. revoked) shows both with rotated_from linkage.
        all_rows = store.list_for_user(
            org_id="org_acme", user_id="usr_sarah", include_revoked=True
        )
        ids = {row.id: row for row in all_rows}
        assert ids[old_id].revoked_at is not None
        assert ids[new["key"]["id"]].rotated_from_id == old_id

        events = identity.list_identity_audit(org_id="org_acme")
        rotate_events = [e for e in events if e.action == "api_key.rotate"]
        assert len(rotate_events) == 1
        meta = rotate_events[0].metadata or {}
        assert meta["old_api_key_id"] == old_id


class TestBearerVerification:
    def test_hash_verify_accepts_correct_secret_and_rejects_wrong(self) -> None:
        hasher = ApiKeyHasher(server_pepper=_PEPPER)
        _prefix, secret = hasher.mint()
        stored_hash = hasher.hash(secret)
        assert hasher.verify(secret, stored_hash) is True
        assert hasher.verify(secret + "x", stored_hash) is False
        # And a different pepper computes a different hash for the same
        # secret — pepper rotation is the emergency invalidation lever.
        rotated = ApiKeyHasher(server_pepper=b"different-pepper-2026-rotated!!")
        assert rotated.verify(secret, stored_hash) is False

    def test_revoked_row_is_invisible_to_active_lookup(self) -> None:
        store = InMemoryApiKeyStore()
        client, _i, _s = _client(api_key_store=store)
        created = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "old"},
        ).json()
        prefix = created["key"]["key_prefix"]
        assert store.find_active_by_prefix(key_prefix=prefix) is not None
        client.delete(
            f"/internal/v1/me/api-keys/{created['key']['id']}", params=_params()
        )
        # Revoked rows MUST NOT surface to the auth path. The middleware
        # treats absence as 401, not 403, so revoked-vs-nonexistent isn't
        # leaked across the network.
        assert store.find_active_by_prefix(key_prefix=prefix) is None


class TestVerifyEndpoint:
    """The ``POST /internal/v1/auth/api-keys/verify`` endpoint is the
    facade's entry point for ``atlas_pk_*`` bearer auth."""

    def test_valid_bearer_returns_identity_and_stamps_last_used(self) -> None:
        client, _i, store = _client()
        created = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "ci-bot", "scopes": ["runtime:use"]},
        ).json()
        plaintext = created["plaintext"]
        api_key_id = created["key"]["id"]

        # Pre-verify: last_used_at is None.
        before = store.list_for_user(org_id="org_acme", user_id="usr_sarah")[0]
        assert before.last_used_at is None

        response = client.post(
            "/internal/v1/auth/api-keys/verify",
            json={"bearer": plaintext},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["org_id"] == "org_acme"
        assert body["user_id"] == "usr_sarah"
        assert body["api_key_id"] == api_key_id
        assert body["scopes"] == ["runtime:use"]

        # Post-verify: last_used_at stamped.
        after = store.list_for_user(org_id="org_acme", user_id="usr_sarah")[0]
        assert after.last_used_at is not None

    def test_unknown_prefix_is_401(self) -> None:
        client, _i, _s = _client()
        # Well-formed shape but no row backing it.
        bearer = "atlas_pk_" + "0" * 12 + "_" + "0" * 48
        response = client.post(
            "/internal/v1/auth/api-keys/verify",
            json={"bearer": bearer},
        )
        assert response.status_code == 401

    def test_wrong_secret_is_401(self) -> None:
        client, _i, store = _client()
        client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "ci-bot"},
        )
        # Same prefix, wrong secret.
        row = store.list_for_user(org_id="org_acme", user_id="usr_sarah")[0]
        bad_bearer = f"atlas_pk_{row.key_prefix}_" + "f" * 48
        response = client.post(
            "/internal/v1/auth/api-keys/verify",
            json={"bearer": bad_bearer},
        )
        assert response.status_code == 401

    def test_revoked_key_is_401(self) -> None:
        client, _i, _s = _client()
        created = client.post(
            "/internal/v1/me/api-keys",
            params=_params(),
            json={"label": "ephemeral"},
        ).json()
        client.delete(
            f"/internal/v1/me/api-keys/{created['key']['id']}", params=_params()
        )
        response = client.post(
            "/internal/v1/auth/api-keys/verify",
            json={"bearer": created["plaintext"]},
        )
        assert response.status_code == 401
