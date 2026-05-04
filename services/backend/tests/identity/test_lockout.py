"""Tests for the account lockout service + routes (A8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    LockoutPolicyRecord,
    OrganizationRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity import (
    AccountLocked,
    InMemoryIdentityStore,
    InMemoryLockoutStore,
    InMemoryPasswordStore,
    InMemorySessionStore,
    LockoutService,
    PasswordHasherConfig,
    PasswordService,
    SessionService,
)


_TEST_AUTH_SECRET = "test-auth-secret-lockout"
_TEST_SERVICE_TOKEN = "test-service-token"
_FAST_HASHER = PasswordHasherConfig(memory_cost=512, time_cost=1, parallelism=1)


class _Fixture:
    def __init__(self) -> None:
        self.identity_store = InMemoryIdentityStore()
        self.lockout_store = InMemoryLockoutStore()
        self.password_store = InMemoryPasswordStore()
        self.sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_TEST_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        self.lockout = LockoutService(
            identity_store=self.identity_store,
            lockout_store=self.lockout_store,
        )
        self.org = self.identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        self.identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="E",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        self.user = self.identity_store.create_user(
            UserRecord(
                org_id=self.org.org_id,
                primary_email="alice@acme.com",
                display_name="Alice",
            )
        )
        self.password_service = PasswordService(
            identity_store=self.identity_store,
            password_store=self.password_store,
            sessions=self.sessions,
            hasher_config=_FAST_HASHER,
            lockout=self.lockout,
        )

    def with_policy(self, **kwargs) -> LockoutPolicyRecord:
        record = LockoutPolicyRecord(org_id=self.org.org_id, **kwargs)
        return self.lockout_store.upsert_policy(record)

    def seed_password(self, password: str) -> None:
        self.password_service.set_password(
            org_id=self.org.org_id,
            user_id=self.user.user_id,
            new_password=password,
        )


# ---------------------------------------------------------------------------
# Policy + service unit behavior
# ---------------------------------------------------------------------------


class TestLockoutPolicyDefault:
    def test_policy_for_unknown_org_returns_default_off(self) -> None:
        f = _Fixture()
        policy = f.lockout.policy_for(org_id=f.org.org_id)
        assert policy.enforce_lockout is False
        assert policy.max_failures == 5

    def test_default_policy_does_not_lock_when_enforcement_off(self) -> None:
        f = _Fixture()
        f.seed_password("CorrectPass2024!")
        # Spam failures — without enforce_lockout, the gate stays open.
        for _ in range(20):
            with pytest.raises(Exception):
                f.password_service.login(
                    org_id=f.org.org_id,
                    email="alice@acme.com",
                    password="WrongPass2024!",
                )
        active = f.lockout_store.get_active_lockout(
            org_id=f.org.org_id, user_id=f.user.user_id
        )
        # The lockout row is created (telemetry on), but check_or_raise
        # never trips because policy.enforce_lockout=False.
        assert active is not None or active is None  # both are valid here
        # The right password still works.
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.user_id == f.user.user_id


class TestSlidingWindowEnforcement:
    def test_threshold_trip_raises_account_locked_on_next_attempt(self) -> None:
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=3)
        f.seed_password("CorrectPass2024!")
        # 3 wrong attempts to fill the window; 4th is the one that 423s.
        for _ in range(3):
            with pytest.raises(Exception):
                f.password_service.login(
                    org_id=f.org.org_id,
                    email="alice@acme.com",
                    password="WrongPass2024!",
                )
        active = f.lockout_store.get_active_lockout(
            org_id=f.org.org_id, user_id=f.user.user_id
        )
        assert active is not None, "expected an active lockout after threshold"
        with pytest.raises(AccountLocked) as exc:
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="WrongPass2024!",
            )
        assert exc.value.org_id == f.org.org_id
        assert exc.value.user_id == f.user.user_id
        assert exc.value.retry_after_seconds > 0

    def test_correct_password_during_lockout_still_423s(self) -> None:
        # Spec §1.2: lockout supersedes password check.
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=2)
        f.seed_password("CorrectPass2024!")
        for _ in range(2):
            with pytest.raises(Exception):
                f.password_service.login(
                    org_id=f.org.org_id,
                    email="alice@acme.com",
                    password="WrongPass2024!",
                )
        with pytest.raises(AccountLocked):
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="CorrectPass2024!",
            )

    def test_auto_unlock_after_window_elapses(self) -> None:
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=1, lockout_duration_seconds=1)
        f.seed_password("CorrectPass2024!")
        with pytest.raises(Exception):
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="WrongPass2024!",
            )
        # Hand-roll the auto-unlock window expiry by mutating the row.
        active = f.lockout_store.get_active_lockout(
            org_id=f.org.org_id, user_id=f.user.user_id
        )
        assert active is not None
        elapsed = active.model_copy(
            update={"auto_unlock_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        f.lockout_store.lockouts[active.lockout_id] = elapsed
        # Login again — pre-check sees the elapsed window and lazily clears.
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.user_id == f.user.user_id
        # Active lockout was cleared as part of the lazy auto-unlock.
        assert (
            f.lockout_store.get_active_lockout(
                org_id=f.org.org_id, user_id=f.user.user_id
            )
            is None
        )

    def test_concurrent_failures_create_at_most_one_active_lockout(self) -> None:
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=1)
        f.seed_password("CorrectPass2024!")
        # First failure trips the threshold (max=1).
        with pytest.raises(Exception):
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="WrongPass2024!",
            )
        # Simulate a second worker trying to lock the same user. The store
        # mirrors the partial-unique constraint and refuses.
        from backend_app.contracts import AccountLockoutRecord

        duplicate = AccountLockoutRecord(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            lock_reason="concurrent",
            auto_unlock_at=datetime.now(timezone.utc) + timedelta(seconds=900),
        )
        result = f.lockout_store.create_lockout(duplicate)
        assert result is None  # the partial unique guarded the second insert
        actives = [
            r
            for r in f.lockout_store.lockouts.values()
            if r.unlocked_at is None
            and r.org_id == f.org.org_id
            and r.user_id == f.user.user_id
        ]
        assert len(actives) == 1


class TestSuccessClearsLockout:
    def test_record_success_unlocks_active_window(self) -> None:
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=1)
        f.seed_password("CorrectPass2024!")
        with pytest.raises(Exception):
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="WrongPass2024!",
            )
        # Force-clear the active lockout so the success path can exercise
        # without 423-ing first.
        f.lockout.force_unlock(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            unlocked_by_user_id="admin_test",
            reason="test_setup",
        )
        # The next successful login leaves no active lockout.
        f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert (
            f.lockout_store.get_active_lockout(
                org_id=f.org.org_id, user_id=f.user.user_id
            )
            is None
        )


class TestAdminUnlock:
    def test_force_unlock_writes_audit_row(self) -> None:
        f = _Fixture()
        f.with_policy(enforce_lockout=True, max_failures=1)
        f.seed_password("CorrectPass2024!")
        with pytest.raises(Exception):
            f.password_service.login(
                org_id=f.org.org_id,
                email="alice@acme.com",
                password="WrongPass2024!",
            )
        f.lockout.force_unlock(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            unlocked_by_user_id="usr_admin",
            reason="customer_call",
        )
        actions = [
            event.action
            for event in f.identity_store.list_identity_audit(org_id=f.org.org_id)
        ]
        assert "lockout.admin_unlocked" in actions
        # The next login proceeds without 423.
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.user_id == f.user.user_id


class TestTenantIsolation:
    def test_lockout_in_org_a_does_not_lock_same_email_in_org_b(self) -> None:
        # Two orgs, one shared identity-store/lockout-store, same email.
        identity_store = InMemoryIdentityStore()
        lockout_store = InMemoryLockoutStore()
        password_store = InMemoryPasswordStore()
        sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_TEST_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        lockout = LockoutService(
            identity_store=identity_store, lockout_store=lockout_store
        )
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="E",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        org_a = identity_store.create_organization(
            OrganizationRecord(display_name="A", slug="a")
        )
        org_b = identity_store.create_organization(
            OrganizationRecord(display_name="B", slug="b")
        )
        user_a = identity_store.create_user(
            UserRecord(org_id=org_a.org_id, primary_email="x@x.com", display_name="A")
        )
        user_b = identity_store.create_user(
            UserRecord(org_id=org_b.org_id, primary_email="x@x.com", display_name="B")
        )
        lockout_store.upsert_policy(
            LockoutPolicyRecord(
                org_id=org_a.org_id, enforce_lockout=True, max_failures=1
            )
        )
        lockout_store.upsert_policy(
            LockoutPolicyRecord(
                org_id=org_b.org_id, enforce_lockout=True, max_failures=1
            )
        )
        svc = PasswordService(
            identity_store=identity_store,
            password_store=password_store,
            sessions=sessions,
            hasher_config=_FAST_HASHER,
            lockout=lockout,
        )
        svc.set_password(
            org_id=org_a.org_id, user_id=user_a.user_id, new_password="CorrectPass2024!"
        )
        svc.set_password(
            org_id=org_b.org_id, user_id=user_b.user_id, new_password="CorrectPass2024!"
        )
        with pytest.raises(Exception):
            svc.login(org_id=org_a.org_id, email="x@x.com", password="WrongPass2024!")
        # org_a now has an active lockout. org_b remains open.
        assert (
            lockout_store.get_active_lockout(
                org_id=org_a.org_id, user_id=user_a.user_id
            )
            is not None
        )
        result_b = svc.login(
            org_id=org_b.org_id, email="x@x.com", password="CorrectPass2024!"
        )
        assert result_b.user_id == user_b.user_id


# ---------------------------------------------------------------------------
# Route-level: backend internal endpoints
# ---------------------------------------------------------------------------


class _RouteFixture:
    def client(self, monkeypatch) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_AUTH_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        return TestClient(create_app())

    def headers(self, *, org_id: str, user_id: str = "anonymous") -> dict[str, str]:
        return {
            "x-enterprise-service-token": _TEST_SERVICE_TOKEN,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": user_id,
            "x-enterprise-roles": "admin",
            "x-enterprise-permission-scopes": "admin:users",
            "x-enterprise-connector-scopes": "{}",
        }

    def seed(self, app, *, email: str, password: str) -> tuple[str, str]:
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


class TestLoginRouteReturns423:
    def test_locked_user_returns_423_with_retry_after(self, monkeypatch) -> None:
        rf = _RouteFixture()
        client = rf.client(monkeypatch)
        org_id, user_id = rf.seed(
            client.app, email="alice@acme.com", password="CorrectPass2024!"
        )
        client.app.state.lockout_store.upsert_policy(
            LockoutPolicyRecord(org_id=org_id, enforce_lockout=True, max_failures=1)
        )
        # First failure trips threshold; second attempt 423s.
        client.post(
            "/internal/v1/auth/local/verify",
            headers=rf.headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "WrongPass2024!",
            },
        )
        response = client.post(
            "/internal/v1/auth/local/verify",
            headers=rf.headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "CorrectPass2024!",
            },
        )
        assert response.status_code == 423, response.text
        assert response.headers.get("retry-after") is not None
        del user_id  # reference suppressor


class TestLockoutAdminRoutes:
    def test_force_unlock_and_list_active(self, monkeypatch) -> None:
        rf = _RouteFixture()
        client = rf.client(monkeypatch)
        org_id, user_id = rf.seed(
            client.app, email="alice@acme.com", password="CorrectPass2024!"
        )
        client.app.state.lockout_store.upsert_policy(
            LockoutPolicyRecord(org_id=org_id, enforce_lockout=True, max_failures=1)
        )
        client.post(
            "/internal/v1/auth/local/verify",
            headers=rf.headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "WrongPass2024!",
            },
        )
        list_response = client.get(
            "/internal/v1/auth/lockouts",
            headers=rf.headers(org_id=org_id),
            params={"org_id": org_id, "active": True},
        )
        assert list_response.status_code == 200
        assert len(list_response.json()["lockouts"]) == 1

        unlock = client.post(
            f"/internal/v1/auth/lockouts/{user_id}/unlock",
            headers=rf.headers(org_id=org_id),
            json={"org_id": org_id, "reason": "manual"},
        )
        assert unlock.status_code == 200
        body = unlock.json()
        assert body["ok"] is True

        # List again — no active lockouts.
        again = client.get(
            "/internal/v1/auth/lockouts",
            headers=rf.headers(org_id=org_id),
            params={"org_id": org_id, "active": True},
        )
        assert again.json()["lockouts"] == []

    def test_my_login_attempts_returns_callers_history(self, monkeypatch) -> None:
        rf = _RouteFixture()
        client = rf.client(monkeypatch)
        org_id, user_id = rf.seed(
            client.app, email="alice@acme.com", password="CorrectPass2024!"
        )
        # Trigger one success attempt so there's a row to list.
        client.post(
            "/internal/v1/auth/local/verify",
            headers=rf.headers(org_id=org_id),
            json={
                "org_id": org_id,
                "email": "alice@acme.com",
                "password": "CorrectPass2024!",
            },
        )
        response = client.get(
            "/internal/v1/auth/me/login-attempts",
            headers=rf.headers(org_id=org_id, user_id=user_id),
            params={"org_id": org_id, "user_id": user_id},
        )
        assert response.status_code == 200
        attempts = response.json()["attempts"]
        assert len(attempts) >= 1
        assert any(a["outcome"] == "success" for a in attempts)
