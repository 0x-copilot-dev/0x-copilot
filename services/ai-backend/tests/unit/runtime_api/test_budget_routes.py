"""HTTP route tests for the B7 ``/v1/budgets/*`` endpoints.

D4 adds the scope-aware write authorization: a caller may manage their OWN
``scope=user`` budget with no admin scope (the self-service spend cap), while
every ``scope=org`` budget — or a user budget owned by someone else — requires
``admin:budgets`` and is rejected with an explicit 403 that fires regardless of
``RBAC_MODE``. Identity + scopes ride the dev trusted-header path (org/user +
``x-enterprise-permission-scopes``); no service token is needed in dev.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from copilot_service_contracts.scopes import ADMIN_BUDGETS, RUNTIME_USE
from fastapi.testclient import TestClient

from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetScope,
    BudgetStatus,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory

_ORG = "org_a"
_USER = "user_1"
_OTHER_USER = "user_2"


class BudgetClientMixin:
    def _client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        return TestClient(
            RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        ), store

    @staticmethod
    def _headers(
        *,
        user_id: str = _USER,
        scopes: tuple[str, ...] = (RUNTIME_USE,),
        org_id: str = _ORG,
    ) -> dict[str, str]:
        # Dev trusted-header path: with the org header present and no service
        # token configured, the authenticator promotes these header values into
        # a TrustedRequestIdentity (roles default to "employee").
        return {
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": user_id,
            "x-enterprise-permission-scopes": ",".join(scopes),
            "x-enterprise-connector-scopes": "{}",
        }

    @staticmethod
    async def _seed_org_budget(store: InMemoryRuntimeApiStore) -> BudgetRecord:
        return await store.create_budget(
            BudgetRecord(
                org_id=_ORG,
                user_id=None,
                scope=BudgetScope.ORG,
                period=BudgetPeriod.MONTH,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=50_000_000,
                status=BudgetStatus.ACTIVE,
                created_by_user_id="seed_admin",
            )
        )

    @staticmethod
    async def _seed_user_budget(
        store: InMemoryRuntimeApiStore, *, user_id: str = _USER
    ) -> BudgetRecord:
        return await store.create_budget(
            BudgetRecord(
                org_id=_ORG,
                user_id=user_id,
                scope=BudgetScope.USER,
                period=BudgetPeriod.MONTH,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=1_000_000,
                status=BudgetStatus.ACTIVE,
                created_by_user_id=user_id,
            )
        )

    @staticmethod
    def _user_month_payload(*, user_id: str = _USER) -> dict[str, object]:
        return {
            "scope": "user",
            "period": "month",
            "enforcement": "hard",
            "limit_micro_usd": 1_000_000,
            "user_id": user_id,
        }

    @staticmethod
    def _org_month_payload() -> dict[str, object]:
        return {
            "scope": "org",
            "period": "month",
            "enforcement": "soft",
            "limit_micro_usd": 50_000_000,
        }


class TestSelfServiceWrites(BudgetClientMixin):
    """A non-admin caller manages ONLY their own user-scoped budget."""

    def test_self_can_create_own_user_budget_without_admin(self) -> None:
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json=self._user_month_payload(),
        )
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["scope"] == "user"
        assert created["user_id"] == _USER

    def test_user_scope_defaults_user_id_to_caller(self) -> None:
        # The self-service spend-cap flow POSTs without user_id; the server
        # defaults it to the authenticated caller (no client-supplied id).
        client, _ = self._client()
        payload = {
            "scope": "user",
            "period": "month",
            "enforcement": "hard",
            "limit_micro_usd": 1_000_000,
        }
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json=payload,
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == _USER

    async def test_self_can_patch_and_delete_own_user_budget(self) -> None:
        client, store = self._client()
        record = await self._seed_user_budget(store)
        patched = client.patch(
            f"/v1/budgets/{record.id}",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json={"limit_micro_usd": 2_000_000},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["limit_micro_usd"] == 2_000_000
        deleted = client.delete(
            f"/v1/budgets/{record.id}",
            headers=self._headers(scopes=(RUNTIME_USE,)),
        )
        assert deleted.status_code == 200, deleted.text

    def test_non_admin_cannot_create_org_budget(self) -> None:
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json=self._org_month_payload(),
        )
        assert response.status_code == 403, response.text

    async def test_non_admin_cannot_write_other_users_budget(self) -> None:
        client, store = self._client()
        record = await self._seed_user_budget(store, user_id=_OTHER_USER)
        # Caller is _USER; the budget belongs to _OTHER_USER → 403.
        patched = client.patch(
            f"/v1/budgets/{record.id}",
            headers=self._headers(user_id=_USER, scopes=(RUNTIME_USE,)),
            json={"limit_micro_usd": 5},
        )
        assert patched.status_code == 403, patched.text
        deleted = client.delete(
            f"/v1/budgets/{record.id}",
            headers=self._headers(user_id=_USER, scopes=(RUNTIME_USE,)),
        )
        assert deleted.status_code == 403, deleted.text

    async def test_non_admin_cannot_patch_or_delete_org_budget(self) -> None:
        client, store = self._client()
        record = await self._seed_org_budget(store)
        patched = client.patch(
            f"/v1/budgets/{record.id}",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json={"enforcement": "soft"},
        )
        assert patched.status_code == 403, patched.text
        deleted = client.delete(
            f"/v1/budgets/{record.id}",
            headers=self._headers(scopes=(RUNTIME_USE,)),
        )
        assert deleted.status_code == 403, deleted.text


class TestAdminWrites(BudgetClientMixin):
    """A caller holding ``admin:budgets`` manages org-scoped budgets."""

    def test_admin_can_create_org_budget(self) -> None:
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE, ADMIN_BUDGETS)),
            json=self._org_month_payload(),
        )
        assert response.status_code == 200, response.text

    async def test_admin_can_patch_and_delete_org_budget(self) -> None:
        client, store = self._client()
        record = await self._seed_org_budget(store)
        admin = self._headers(scopes=(RUNTIME_USE, ADMIN_BUDGETS))
        patched = client.patch(
            f"/v1/budgets/{record.id}",
            headers=admin,
            json={"status": "disabled"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "disabled"
        deleted = client.delete(f"/v1/budgets/{record.id}", headers=admin)
        assert deleted.status_code == 200, deleted.text

    def test_create_duplicate_returns_409(self) -> None:
        client, _ = self._client()
        admin = self._headers(scopes=(RUNTIME_USE, ADMIN_BUDGETS))
        first = client.post(
            "/v1/budgets", headers=admin, json=self._org_month_payload()
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/v1/budgets", headers=admin, json=self._org_month_payload()
        )
        assert second.status_code == 409, second.text

    def test_list_requires_admin_under_enforce(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        client, _ = self._client()
        denied = client.get("/v1/budgets", headers=self._headers(scopes=(RUNTIME_USE,)))
        assert denied.status_code == 403, denied.text
        allowed = client.get(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE, ADMIN_BUDGETS)),
        )
        assert allowed.status_code == 200, allowed.text


class TestAuthorizationIsModeIndependent(BudgetClientMixin):
    """The org-write 403 must fire under BOTH audit and enforce modes."""

    @pytest.fixture
    def _enforce(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        yield

    def test_org_write_denied_under_audit(self) -> None:
        # RBAC_MODE defaults to audit; the route-level RequireScopes only logs,
        # so the in-handler 403 is the load-bearing control.
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json=self._org_month_payload(),
        )
        assert response.status_code == 403, response.text

    def test_self_write_allowed_under_enforce(self, _enforce: None) -> None:
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE,)),
            json=self._user_month_payload(),
        )
        assert response.status_code == 200, response.text

    def test_org_write_allowed_with_admin_under_enforce(self, _enforce: None) -> None:
        client, _ = self._client()
        response = client.post(
            "/v1/budgets",
            headers=self._headers(scopes=(RUNTIME_USE, ADMIN_BUDGETS)),
            json=self._org_month_payload(),
        )
        # With admin:budgets the org write succeeds even under enforce.
        assert response.status_code == 200, response.text


class TestBudgetMeIsCallerScoped(BudgetClientMixin):
    async def test_me_returns_only_callers_budgets(self) -> None:
        client, store = self._client()
        await self._seed_user_budget(store, user_id=_USER)
        await self._seed_user_budget(store, user_id=_OTHER_USER)

        response = client.get(
            "/v1/budgets/me",
            headers=self._headers(user_id=_USER, scopes=(RUNTIME_USE,)),
        )
        assert response.status_code == 200, response.text
        rows = response.json()["budgets"]
        # Only the caller's own user-scoped budget is returned (plus any
        # org-scoped budgets, of which there are none here).
        assert len(rows) == 1
        assert rows[0]["scope"] == "user"
        assert rows[0]["limit_micro_usd"] == 1_000_000
        assert rows[0]["remaining_micro_usd"] == 1_000_000

    async def test_me_includes_remaining_headroom(self) -> None:
        client, store = self._client()
        await self._seed_user_budget(store, user_id=_USER)
        response = client.get(
            "/v1/budgets/me", headers=self._headers(scopes=(RUNTIME_USE,))
        )
        assert response.status_code == 200
        row = response.json()["budgets"][0]
        assert row["remaining_micro_usd"] == 1_000_000
        assert row["current_micro_usd"] == 0
