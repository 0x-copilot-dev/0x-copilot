"""Tests for the local-password service + bootstrap admin (A4)."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    IdentityPolicyRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    OrganizationRecord,
    PasswordPolicyRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity import (
    BootstrapAdminService,
    BootstrapRefused,
    InMemoryIdentityStore,
    InMemoryPasswordStore,
    InMemorySessionStore,
    LocalAuthDisabled,
    LoginRejectedError,
    PasswordChangeRejected,
    PasswordHasherConfig,
    PasswordService,
    ResetTokenRejected,
    SessionService,
    WeakPasswordError,
)


_TEST_AUTH_SECRET = "test-auth-secret-passwords"
_TEST_SERVICE_TOKEN = "test-service-token"
# Faster argon2 params so the tests run in < 1s.
_FAST_HASHER = PasswordHasherConfig(memory_cost=512, time_cost=1, parallelism=1)


class PasswordFixtureMixin:
    def build(self) -> tuple[PasswordService, dict]:
        identity_store = InMemoryIdentityStore()
        password_store = InMemoryPasswordStore()
        sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_TEST_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        org = identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="E",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        admin_role = identity_store.create_role(
            RoleRecord(
                name="admin",
                display_name="Administrator",
                is_system=True,
                permission_scopes=("admin:users",),
            )
        )
        user = identity_store.create_user(
            UserRecord(
                org_id=org.org_id,
                primary_email="alice@acme.com",
                display_name="Alice",
            )
        )
        svc = PasswordService(
            identity_store=identity_store,
            password_store=password_store,
            sessions=sessions,
            hasher_config=_FAST_HASHER,
        )
        return svc, {
            "identity_store": identity_store,
            "password_store": password_store,
            "sessions": sessions,
            "org": org,
            "user": user,
            "admin_role": admin_role,
        }


# ---------------------------------------------------------------------------
# Hashing + verify
# ---------------------------------------------------------------------------


class TestHashAndVerify(PasswordFixtureMixin):
    def test_hash_then_verify_round_trip(self) -> None:
        svc, _ = self.build()
        h = svc.hash("MyPass1234!")
        assert svc.verify(h, "MyPass1234!") is True
        assert svc.verify(h, "WrongPass1234!") is False

    def test_verify_with_corrupted_hash_returns_false(self) -> None:
        svc, _ = self.build()
        # Garbage hash must not raise; just return False.
        assert svc.verify("not-an-argon2-hash", "anything") is False


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------


class TestPolicy(PasswordFixtureMixin):
    def test_default_policy_requires_complexity(self) -> None:
        svc, ctx = self.build()
        with pytest.raises(WeakPasswordError) as exc:
            svc.set_password(
                org_id=ctx["org"].org_id,
                user_id=ctx["user"].user_id,
                new_password="short",
            )
        assert any("12 characters" in r for r in exc.value.reasons)

    def test_per_org_policy_overrides(self) -> None:
        svc, ctx = self.build()
        ctx["password_store"].upsert_policy(
            PasswordPolicyRecord(
                org_id=ctx["org"].org_id,
                min_length=4,
                require_upper=False,
                require_lower=False,
                require_digit=False,
                require_symbol=False,
                reuse_window=0,
            )
        )
        # Now even a tiny password is accepted (defense for lab environments).
        svc.set_password(
            org_id=ctx["org"].org_id, user_id=ctx["user"].user_id, new_password="abcd"
        )
        cred = ctx["password_store"].get_credential(
            org_id=ctx["org"].org_id, user_id=ctx["user"].user_id
        )
        assert cred is not None

    def test_reuse_window_rejects_recent_passwords(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="OriginalPass1!",
        )
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="SecondPass2!",
        )
        with pytest.raises(WeakPasswordError):
            svc.set_password(
                org_id=ctx["org"].org_id,
                user_id=ctx["user"].user_id,
                new_password="OriginalPass1!",
            )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin(PasswordFixtureMixin):
    def test_login_with_correct_password_mints_session(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )
        result = svc.login(
            org_id=ctx["org"].org_id,
            email="alice@acme.com",
            password="CorrectPass1!",
        )
        assert result.user_id == ctx["user"].user_id
        assert "." in result.bearer_token

    def test_login_with_wrong_password_rejected(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )
        with pytest.raises(LoginRejectedError):
            svc.login(
                org_id=ctx["org"].org_id,
                email="alice@acme.com",
                password="WrongPass1!",
            )

    def test_login_with_unknown_email_constant_time(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )

        # Time the unknown-email path vs the wrong-password path. They must
        # be within ~30% of each other so a timing oracle can't enumerate
        # emails.
        def _measure(email: str) -> float:
            t0 = time.perf_counter()
            try:
                svc.login(org_id=ctx["org"].org_id, email=email, password="x" * 12)
            except LoginRejectedError:
                pass
            return time.perf_counter() - t0

        unknown = sum(_measure("nobody@nowhere.com") for _ in range(5)) / 5
        wrong = sum(_measure("alice@acme.com") for _ in range(5)) / 5
        ratio = max(unknown, wrong) / max(min(unknown, wrong), 1e-9)
        assert ratio < 3.0, f"unknown={unknown:.4f}s wrong={wrong:.4f}s ratio={ratio}"

    def test_login_attempt_audited_on_failure(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )
        with pytest.raises(LoginRejectedError):
            svc.login(
                org_id=ctx["org"].org_id,
                email="alice@acme.com",
                password="WrongPass1!",
            )
        attempts = ctx["identity_store"].list_login_attempts(org_id=ctx["org"].org_id)
        assert len(attempts) == 1
        assert attempts[0].outcome.value == "bad_password"


# ---------------------------------------------------------------------------
# Identity policy: local_password_enabled=False locks the path
# ---------------------------------------------------------------------------


class TestLocalAuthDisabled(PasswordFixtureMixin):
    def test_login_raises_local_auth_disabled_when_policy_off(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )
        ctx["identity_store"].upsert_identity_policy(
            IdentityPolicyRecord(
                org_id=ctx["org"].org_id,
                local_password_enabled=False,
            )
        )
        with pytest.raises(LocalAuthDisabled):
            svc.login(
                org_id=ctx["org"].org_id,
                email="alice@acme.com",
                password="CorrectPass1!",
            )

    def test_login_disabled_audits_provider_rejected_attempt(self) -> None:
        svc, ctx = self.build()
        ctx["identity_store"].upsert_identity_policy(
            IdentityPolicyRecord(
                org_id=ctx["org"].org_id,
                local_password_enabled=False,
            )
        )
        with pytest.raises(LocalAuthDisabled):
            svc.login(
                org_id=ctx["org"].org_id,
                email="alice@acme.com",
                password="anything",
            )
        attempts = ctx["identity_store"].list_login_attempts(org_id=ctx["org"].org_id)
        assert len(attempts) == 1
        assert attempts[0].auth_kind == LoginAttemptKind.LOCAL
        assert attempts[0].outcome == LoginAttemptOutcome.PROVIDER_REJECTED
        assert attempts[0].failure_reason == "local_password_disabled"

    def test_login_works_when_policy_row_absent(self) -> None:
        # No identity_policy row → default-open. Verifies the SaaS happy path
        # isn't disrupted by the new check.
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="CorrectPass1!",
        )
        result = svc.login(
            org_id=ctx["org"].org_id,
            email="alice@acme.com",
            password="CorrectPass1!",
        )
        assert result.user_id == ctx["user"].user_id


# ---------------------------------------------------------------------------
# Reset flow
# ---------------------------------------------------------------------------


class TestPasswordReset(PasswordFixtureMixin):
    def test_request_reset_for_unknown_email_still_returns_accepted(self) -> None:
        svc, ctx = self.build()
        accepted, plaintext = svc.request_reset(
            org_id=ctx["org"].org_id, email="nobody@nowhere.com"
        )
        assert accepted is True
        assert plaintext is None  # No token minted for unknown email.

    def test_request_reset_for_known_email_returns_token_then_consumed_once(
        self,
    ) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="OriginalPass1!",
        )
        accepted, plaintext = svc.request_reset(
            org_id=ctx["org"].org_id, email="alice@acme.com"
        )
        assert accepted is True
        assert plaintext is not None

        # Confirm with the right token + a strong new password.
        svc.confirm_reset(token=plaintext, new_password="ResetPass2024!")

        # Replay rejected.
        with pytest.raises(ResetTokenRejected):
            svc.confirm_reset(token=plaintext, new_password="ResetPass2025!")

        # Login works with the new password.
        result = svc.login(
            org_id=ctx["org"].org_id,
            email="alice@acme.com",
            password="ResetPass2024!",
        )
        assert result.user_id == ctx["user"].user_id

    def test_expired_reset_token_rejected(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="OriginalPass1!",
        )
        _, plaintext = svc.request_reset(
            org_id=ctx["org"].org_id, email="alice@acme.com"
        )
        assert plaintext is not None
        token_hash = hashlib.sha256(plaintext.encode("ascii")).hexdigest()
        # Force the stored row's expiry into the past.
        for row in ctx["password_store"].reset_tokens.values():
            if row.token_hash == token_hash:
                ctx["password_store"].reset_tokens[row.token_id] = row.model_copy(
                    update={
                        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
                    }
                )

        with pytest.raises(ResetTokenRejected):
            svc.confirm_reset(token=plaintext, new_password="NewPass2024!")


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


class TestPasswordChange(PasswordFixtureMixin):
    def test_change_with_correct_current_password(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="OldPass2024!",
        )
        svc.change_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            current_password="OldPass2024!",
            new_password="NewPass2024!",
        )
        # Old no longer works, new does.
        with pytest.raises(LoginRejectedError):
            svc.login(
                org_id=ctx["org"].org_id,
                email="alice@acme.com",
                password="OldPass2024!",
            )
        result = svc.login(
            org_id=ctx["org"].org_id,
            email="alice@acme.com",
            password="NewPass2024!",
        )
        assert result.user_id == ctx["user"].user_id

    def test_change_with_wrong_current_password_rejected(self) -> None:
        svc, ctx = self.build()
        svc.set_password(
            org_id=ctx["org"].org_id,
            user_id=ctx["user"].user_id,
            new_password="OldPass2024!",
        )
        with pytest.raises(PasswordChangeRejected):
            svc.change_password(
                org_id=ctx["org"].org_id,
                user_id=ctx["user"].user_id,
                current_password="WrongPass1!",
                new_password="NewPass2024!",
            )


# ---------------------------------------------------------------------------
# Bootstrap admin
# ---------------------------------------------------------------------------


class TestBootstrapAdmin(PasswordFixtureMixin):
    def test_bootstrap_creates_admin_with_must_rotate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc, ctx = self.build()
        bootstrap = BootstrapAdminService(
            identity_store=ctx["identity_store"],
            password_service=svc,
        )
        # Fresh org without any users.
        empty_org = ctx["identity_store"].create_organization(
            OrganizationRecord(display_name="Empty", slug="empty")
        )
        monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "operator-secret-1")

        user_id = bootstrap.bootstrap(
            org_id=empty_org.org_id,
            email="admin@empty.com",
            display_name="Admin",
            setup_token="operator-secret-1",
            initial_password="InitialPass2024!",
        )

        result = svc.login(
            org_id=empty_org.org_id,
            email="admin@empty.com",
            password="InitialPass2024!",
        )
        assert result.user_id == user_id
        # Forced rotation flag bubbles up so the UI can prompt.
        assert result.requires_password_change is True

    def test_bootstrap_refused_when_admin_already_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc, ctx = self.build()
        ctx["identity_store"].assign_role(
            _role_assignment(
                org_id=ctx["org"].org_id,
                user_id=ctx["user"].user_id,
                role_id=ctx["admin_role"].role_id,
            )
        )
        bootstrap = BootstrapAdminService(
            identity_store=ctx["identity_store"], password_service=svc
        )
        monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "operator-secret-1")
        with pytest.raises(BootstrapRefused):
            bootstrap.bootstrap(
                org_id=ctx["org"].org_id,
                email="other@acme.com",
                display_name="Other",
                setup_token="operator-secret-1",
                initial_password="StrongPass1!",
            )

    def test_bootstrap_refused_with_wrong_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc, ctx = self.build()
        bootstrap = BootstrapAdminService(
            identity_store=ctx["identity_store"], password_service=svc
        )
        empty_org = ctx["identity_store"].create_organization(
            OrganizationRecord(display_name="X", slug="x")
        )
        monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "operator-secret")
        with pytest.raises(BootstrapRefused):
            bootstrap.bootstrap(
                org_id=empty_org.org_id,
                email="a@x.com",
                display_name="A",
                setup_token="wrong",
                initial_password="StrongPass1!",
            )

    def test_bootstrap_refused_when_token_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc, ctx = self.build()
        bootstrap = BootstrapAdminService(
            identity_store=ctx["identity_store"], password_service=svc
        )
        monkeypatch.delenv("BOOTSTRAP_ADMIN_TOKEN", raising=False)
        empty_org = ctx["identity_store"].create_organization(
            OrganizationRecord(display_name="Y", slug="y")
        )
        with pytest.raises(BootstrapRefused):
            bootstrap.bootstrap(
                org_id=empty_org.org_id,
                email="a@y.com",
                display_name="A",
                setup_token="anything",
                initial_password="StrongPass1!",
            )


def _role_assignment(*, org_id: str, user_id: str, role_id: str):
    from backend_app.contracts import RoleAssignmentRecord

    return RoleAssignmentRecord(org_id=org_id, user_id=user_id, role_id=role_id)


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


class _BackendRouteFixtureMixin:
    def client(self, monkeypatch) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_AUTH_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        return TestClient(create_app())

    def service_headers(self, *, org_id: str = "org_a") -> dict[str, str]:
        return {
            "x-enterprise-service-token": _TEST_SERVICE_TOKEN,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": "anonymous",
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": "runtime:use",
            "x-enterprise-connector-scopes": "{}",
        }

    def seed_user(self, app, *, email: str, password: str) -> tuple[str, str]:
        identity_store = app.state.identity_store
        password_service = app.state.password_service
        org = identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="E",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        user = identity_store.create_user(
            UserRecord(org_id=org.org_id, primary_email=email, display_name="A")
        )
        password_service.set_password(
            org_id=org.org_id, user_id=user.user_id, new_password=password
        )
        return org.org_id, user.user_id


class TestLocalLoginRoute(_BackendRouteFixtureMixin):
    def test_verify_route_returns_session_on_success(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        org_id, user_id = self.seed_user(
            client.app, email="alice@acme.com", password="CorrectPass1!"
        )
        response = client.post(
            "/internal/v1/auth/local/verify",
            headers=self.service_headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "CorrectPass1!",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_id"] == user_id
        assert "." in body["bearer_token"]

    def test_verify_route_returns_401_on_bad_password(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        org_id, _ = self.seed_user(
            client.app, email="alice@acme.com", password="CorrectPass1!"
        )
        response = client.post(
            "/internal/v1/auth/local/verify",
            headers=self.service_headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "WrongPass1!",
            },
        )
        assert response.status_code == 401

    def test_verify_route_returns_404_when_local_disabled(self, monkeypatch) -> None:
        # Spec A4 §1.2: "/v1/auth/login for that org returns 404" — backend
        # verify route mirrors the same status so the facade can pass it
        # through unchanged.
        client = self.client(monkeypatch)
        org_id, _ = self.seed_user(
            client.app, email="alice@acme.com", password="CorrectPass1!"
        )
        client.app.state.identity_store.upsert_identity_policy(
            IdentityPolicyRecord(org_id=org_id, local_password_enabled=False)
        )
        response = client.post(
            "/internal/v1/auth/local/verify",
            headers=self.service_headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "CorrectPass1!",
            },
        )
        assert response.status_code == 404


class TestPasswordResetRoute(_BackendRouteFixtureMixin):
    def test_request_then_confirm_round_trip(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        org_id, _ = self.seed_user(
            client.app, email="alice@acme.com", password="OldPass2024!"
        )
        request_resp = client.post(
            "/internal/v1/auth/password/reset/request?include_token_in_response=true",
            headers=self.service_headers(org_id=org_id),
            json={"org_id": org_id, "email": "alice@acme.com"},
        )
        assert request_resp.status_code == 200
        token = request_resp.json()["token"]
        assert token

        confirm_resp = client.post(
            "/internal/v1/auth/password/reset/confirm",
            headers=self.service_headers(org_id=org_id),
            json={"token": token, "new_password": "NewPass2024!"},
        )
        assert confirm_resp.status_code == 204

        # Second confirm rejected (replay).
        replay = client.post(
            "/internal/v1/auth/password/reset/confirm",
            headers=self.service_headers(org_id=org_id),
            json={"token": token, "new_password": "NewPass2025!"},
        )
        assert replay.status_code == 400

    def test_request_for_unknown_email_returns_accepted_without_token(
        self, monkeypatch
    ) -> None:
        client = self.client(monkeypatch)
        org_id, _ = self.seed_user(
            client.app, email="alice@acme.com", password="OldPass2024!"
        )
        response = client.post(
            "/internal/v1/auth/password/reset/request?include_token_in_response=true",
            headers=self.service_headers(org_id=org_id),
            json={"org_id": org_id, "email": "nobody@nowhere.com"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["token"] is None
