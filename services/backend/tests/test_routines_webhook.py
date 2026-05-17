"""Tests for the Routines webhook ingest (P5-A3).

Covers cross-audit §2.4 (rotating secret + 7-day grace + optional IP
allowlist) and §9.7 Q6 (HMAC-of-payload signature). Each test asserts
the response status, the response body, AND the audit row — audit
parity with the wire response is the binding compliance control here.

Tests deliberately reach into the validator + store rather than going
through ``create_app`` because the route module is mounted by the
P5-A1 wiring (CRUD app composition), which is a peer agent's surface.
A small FastAPI app is built per test so the public ingest route is
exercised in HTTP shape without depending on the rest of the backend
boot.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.routines.webhook import (
    GRACE_WINDOW,
    InMemoryRoutineWebhookStore,
    RoutineWebhookValidator,
    compute_signature_header,
)
from backend_app.routines.webhook_routes import (
    register_routines_webhook_routes,
)
from backend_app.token_vault import LocalTokenVault


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_TRIGGER_ID = "trg_acme_alpha"
_ROUTINE_ID = "rtn_acme_alpha"
_ORG_ID = "org_acme"
_OWNER_USER_ID = "usr_sarah"


class _RecordingEnqueuer:
    """In-memory ``RoutineFireEnqueuer`` that records every call so the
    test can assert on payload + identity passthrough."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue_webhook_fire(
        self,
        *,
        org_id: str,
        routine_id: str,
        trigger_id: str,
        payload,
        source_ip,
    ):
        fire_id = f"fire_{len(self.calls) + 1:08d}"
        self.calls.append(
            {
                "org_id": org_id,
                "routine_id": routine_id,
                "trigger_id": trigger_id,
                "payload": payload,
                "source_ip": source_ip,
                "fire_id": fire_id,
            }
        )
        return fire_id, None


class _FrozenClock:
    """Monotonic, mutable clock for grace-window tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


@pytest.fixture
def clock() -> _FrozenClock:
    return _FrozenClock()


@pytest.fixture
def vault() -> LocalTokenVault:
    return LocalTokenVault(secret=_VAULT_SECRET)


@pytest.fixture
def webhook_store() -> InMemoryRoutineWebhookStore:
    return InMemoryRoutineWebhookStore()


@pytest.fixture
def identity_store() -> InMemoryIdentityStore:
    return InMemoryIdentityStore()


@pytest.fixture
def validator(
    webhook_store: InMemoryRoutineWebhookStore,
    vault: LocalTokenVault,
    clock: _FrozenClock,
) -> RoutineWebhookValidator:
    return RoutineWebhookValidator(store=webhook_store, token_vault=vault, clock=clock)


@pytest.fixture
def enqueuer() -> _RecordingEnqueuer:
    return _RecordingEnqueuer()


@pytest.fixture
def app(
    validator: RoutineWebhookValidator,
    identity_store: InMemoryIdentityStore,
    enqueuer: _RecordingEnqueuer,
) -> FastAPI:
    app = FastAPI()
    register_routines_webhook_routes(
        app,
        validator=validator,
        identity_store=identity_store,
        fire_enqueuer=enqueuer,
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _seed_secret(
    validator: RoutineWebhookValidator,
    *,
    ip_allowlist: tuple[str, ...] = (),
    trigger_id: str = _TRIGGER_ID,
    org_id: str = _ORG_ID,
    owner_user_id: str = _OWNER_USER_ID,
) -> str:
    """Rotate to seed a fresh secret; return the plaintext."""

    validator.rotate_secret(
        trigger_id=trigger_id,
        org_id=org_id,
        owner_user_id=owner_user_id,
        routine_id=_ROUTINE_ID,
        ip_allowlist=ip_allowlist,
    )
    plaintext = validator.consume_reveal(trigger_id=trigger_id)
    assert plaintext is not None
    return plaintext


def _last_audit(
    identity_store: InMemoryIdentityStore, *, action: str
) -> dict[str, object]:
    rows = [row for row in identity_store.identity_audit_events if row.action == action]
    assert rows, f"expected an audit row with action={action!r}"
    return dict(rows[-1].metadata)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_valid_secret_returns_202_and_enqueues_fire(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
        enqueuer: _RecordingEnqueuer,
    ) -> None:
        secret = _seed_secret(validator)
        body = {"event": "issue.opened", "id": 42}
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": secret},
            json=body,
        )
        assert response.status_code == 202
        envelope = response.json()
        assert envelope["fire_id"].startswith("fire_")
        assert envelope["run_id"] is None

        # Enqueuer received the decoded payload + tenant.
        assert len(enqueuer.calls) == 1
        call = enqueuer.calls[0]
        assert call["org_id"] == _ORG_ID
        assert call["routine_id"] == _ROUTINE_ID
        assert call["payload"] == body

        # Audit row stamped, with the auth method and source_ip.
        meta = _last_audit(identity_store, action="routine.fire_webhook")
        assert meta["trigger_id"] == _TRIGGER_ID
        assert meta["auth_method"] == "secret"
        assert meta["matched_grace"] is False
        assert meta["fire_id"] == envelope["fire_id"]


# ---------------------------------------------------------------------------
# Failed-auth
# ---------------------------------------------------------------------------


class TestFailedAuth:
    def test_bad_secret_returns_401_and_audits_unauthorized(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
        enqueuer: _RecordingEnqueuer,
    ) -> None:
        _seed_secret(validator)
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": "wrong-secret"},
            json={"hi": True},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "bad_secret"
        assert enqueuer.calls == []
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "bad_secret"
        assert meta["auth_method"] == "secret"

    def test_missing_secret_and_signature_returns_401(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        _seed_secret(validator)
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            json={"hi": True},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "missing_secret"
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "missing_secret"
        assert meta["auth_method"] == "none"


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------


class TestIpAllowlist:
    def test_source_ip_outside_allowlist_returns_401(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        # Allowlist with one /24 unrelated to the test client's IP.
        secret = _seed_secret(validator, ip_allowlist=("203.0.113.0/24",))
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={
                "x-atlas-routine-secret": secret,
                "x-forwarded-for": "192.0.2.10",
            },
            json={"hi": True},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "ip_not_allowed"
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "ip_not_allowed"
        # Audit records the *attempted* method even on a failure.
        assert meta["auth_method"] == "secret"

    def test_source_ip_in_allowlist_passes(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        enqueuer: _RecordingEnqueuer,
    ) -> None:
        secret = _seed_secret(validator, ip_allowlist=("203.0.113.0/24",))
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={
                "x-atlas-routine-secret": secret,
                "x-forwarded-for": "203.0.113.55",
            },
            json={"hi": True},
        )
        assert response.status_code == 202
        assert len(enqueuer.calls) == 1
        assert enqueuer.calls[0]["source_ip"] == "203.0.113.55"

    def test_empty_allowlist_means_no_restriction(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
    ) -> None:
        secret = _seed_secret(validator)  # ip_allowlist defaults to ()
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": secret},
            json={"hi": True},
        )
        assert response.status_code == 202


# ---------------------------------------------------------------------------
# HMAC signature
# ---------------------------------------------------------------------------


class TestHmacSignature:
    def test_valid_signature_alone_passes(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        enqueuer: _RecordingEnqueuer,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        secret = _seed_secret(validator)
        body = b'{"event":"issue.opened","id":42}'
        signature = compute_signature_header(body=body, secret=secret)
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={
                "x-atlas-routine-signature": signature,
                "content-type": "application/json",
            },
            content=body,
        )
        assert response.status_code == 202
        assert len(enqueuer.calls) == 1
        meta = _last_audit(identity_store, action="routine.fire_webhook")
        assert meta["auth_method"] == "signature"

    def test_invalid_signature_returns_401(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        _seed_secret(validator)
        body = b'{"event":"issue.opened","id":42}'
        bad_sig = "hmac-sha256=" + "0" * 64
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-signature": bad_sig},
            content=body,
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "bad_signature"
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "bad_signature"

    def test_signature_computed_over_raw_body_not_parsed_json(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        enqueuer: _RecordingEnqueuer,
    ) -> None:
        """Whitespace-sensitive: signature MUST be over raw bytes."""

        secret = _seed_secret(validator)
        # Body with non-canonical whitespace — re-encoding would change it.
        body = b'{ "event" : "issue.opened" ,  "id" : 42 }'
        signature = compute_signature_header(body=body, secret=secret)
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={
                "x-atlas-routine-signature": signature,
                "content-type": "application/json",
            },
            content=body,
        )
        assert response.status_code == 202
        assert len(enqueuer.calls) == 1

    def test_signature_plus_secret_both_required_to_match(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        """When both headers are present, both must validate against the
        same secret. A bad signature with a good secret still 401s."""

        secret = _seed_secret(validator)
        body = b'{"x":1}'
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={
                "x-atlas-routine-secret": secret,
                "x-atlas-routine-signature": "hmac-sha256=" + "0" * 64,
            },
            content=body,
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "bad_signature"


# ---------------------------------------------------------------------------
# Rotation + grace window
# ---------------------------------------------------------------------------


class TestRotationGraceWindow:
    def test_old_secret_works_within_grace_window(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        clock: _FrozenClock,
        identity_store: InMemoryIdentityStore,
        enqueuer: _RecordingEnqueuer,
    ) -> None:
        old_secret = _seed_secret(validator)
        # Rotate — old enters grace; new becomes current.
        validator.rotate_secret(
            trigger_id=_TRIGGER_ID,
            org_id=_ORG_ID,
            owner_user_id=_OWNER_USER_ID,
            routine_id=_ROUTINE_ID,
        )
        new_secret = validator.consume_reveal(trigger_id=_TRIGGER_ID)
        assert new_secret is not None
        assert old_secret != new_secret

        # Advance just under the grace window.
        clock.advance(GRACE_WINDOW - timedelta(hours=1))

        # New secret works (auth_method=secret, matched_grace=False).
        r_new = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": new_secret},
            json={"x": 1},
        )
        assert r_new.status_code == 202

        # Old secret ALSO works during grace — matched_grace stamped.
        r_old = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": old_secret},
            json={"x": 2},
        )
        assert r_old.status_code == 202
        meta = _last_audit(identity_store, action="routine.fire_webhook")
        assert meta["matched_grace"] is True

    def test_old_secret_rejected_after_grace_window_expires(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        clock: _FrozenClock,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        old_secret = _seed_secret(validator)
        validator.rotate_secret(
            trigger_id=_TRIGGER_ID,
            org_id=_ORG_ID,
            owner_user_id=_OWNER_USER_ID,
            routine_id=_ROUTINE_ID,
        )
        _ = validator.consume_reveal(trigger_id=_TRIGGER_ID)

        # Push past the 7-day grace window.
        clock.advance(GRACE_WINDOW + timedelta(seconds=1))

        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": old_secret},
            json={"x": 1},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "bad_secret"
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "bad_secret"


# ---------------------------------------------------------------------------
# Reveal (one-shot)
# ---------------------------------------------------------------------------


class TestReveal:
    def test_consume_reveal_returns_plaintext_once_then_none(
        self,
        validator: RoutineWebhookValidator,
    ) -> None:
        validator.rotate_secret(
            trigger_id=_TRIGGER_ID,
            org_id=_ORG_ID,
            owner_user_id=_OWNER_USER_ID,
            routine_id=_ROUTINE_ID,
        )
        first = validator.consume_reveal(trigger_id=_TRIGGER_ID)
        second = validator.consume_reveal(trigger_id=_TRIGGER_ID)
        assert first is not None and len(first) >= 32
        assert second is None

    def test_secret_at_rest_is_ciphertext_not_plaintext(
        self,
        validator: RoutineWebhookValidator,
        webhook_store: InMemoryRoutineWebhookStore,
    ) -> None:
        """Critical control: nothing on the row exposes the plaintext."""

        validator.rotate_secret(
            trigger_id=_TRIGGER_ID,
            org_id=_ORG_ID,
            owner_user_id=_OWNER_USER_ID,
            routine_id=_ROUTINE_ID,
        )
        plaintext = validator.consume_reveal(trigger_id=_TRIGGER_ID)
        assert plaintext is not None
        row = webhook_store.get_for_trigger(trigger_id=_TRIGGER_ID)
        assert row is not None
        assert plaintext not in row.current_secret_ciphertext
        # Mask preserves only the last 4 chars.
        assert row.current_secret_mask.endswith(plaintext[-4:])
        # And `reveal_plaintext` never leaks into the persisted row.
        assert row.reveal_plaintext is None


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_trigger_from_other_tenant_returns_404(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        # Seed a trigger for org_acme.
        secret = _seed_secret(validator)
        del secret

        # Hit an unrelated trigger id — 404, NOT 403, NOT a tenant probe.
        response = client.post(
            "/v1/webhook/routines/trg_unrelated",
            headers={"x-atlas-routine-secret": "any-secret"},
            json={"x": 1},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "trigger_not_found"
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "trigger_not_found"
        assert meta["trigger_id"] == "trg_unrelated"

    def test_rotate_under_other_tenant_returns_trigger_not_found(
        self,
        validator: RoutineWebhookValidator,
    ) -> None:
        # Tenant A seeds the trigger.
        _seed_secret(validator)
        # Tenant B tries to rotate the same trigger_id → must NOT succeed.
        from backend_app.routines.webhook import WebhookValidationError

        with pytest.raises(WebhookValidationError) as exc:
            validator.rotate_secret(
                trigger_id=_TRIGGER_ID,
                org_id="org_other",
                owner_user_id="usr_attacker",
                routine_id="rtn_other",
            )
        assert exc.value.reason == "trigger_not_found"


# ---------------------------------------------------------------------------
# Payload size guard
# ---------------------------------------------------------------------------


class TestPayloadSizeGuard:
    def test_oversize_body_returns_413_and_audits(
        self,
        client: TestClient,
        validator: RoutineWebhookValidator,
        identity_store: InMemoryIdentityStore,
    ) -> None:
        secret = _seed_secret(validator)
        # 257 KB body — over the 256 KB cap.
        body = b"a" * (257 * 1024)
        response = client.post(
            f"/v1/webhook/routines/{_TRIGGER_ID}",
            headers={"x-atlas-routine-secret": secret},
            content=body,
        )
        assert response.status_code == 413
        meta = _last_audit(identity_store, action="routine.fire_webhook_unauthorized")
        assert meta["reason"] == "payload_too_large"


# ---------------------------------------------------------------------------
# Sanity — every test exercises an audit row
# ---------------------------------------------------------------------------


def test_helpers_self_consistency() -> None:
    """Belt-and-braces: ensure ``compute_signature_header`` round-trips
    with ``_verify_signature`` (mirrored via the validator path)."""

    from backend_app.routines.webhook import _verify_signature

    body = json.dumps({"a": 1}).encode("utf-8")
    secret = "secret-bytes-here"
    header = compute_signature_header(body=body, secret=secret)
    assert _verify_signature(body=body, secret=secret, header=header)
    assert not _verify_signature(body=body, secret="other", header=header)
    assert not _verify_signature(body=body, secret=secret, header="bogus")
