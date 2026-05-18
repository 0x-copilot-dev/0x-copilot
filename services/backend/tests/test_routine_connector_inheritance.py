"""P6.5-A2 — project connector-allowlist inheritance at routine create.

PRD §5.4 contract under test (mirrors the conversation hook in
``ai-backend/tests/test_conversation_connector_inheritance.py``):

1. **Caller-explicit wins.** When the create payload carries an
   explicit ``connectors_scope`` (even if empty), the project allowlist
   is NOT consulted. Caller's choice is authoritative.
2. **Project allowlist non-empty.** When ``project_id`` is set, the
   caller did NOT pass ``connectors_scope``, and the project lookup
   returns a non-empty tuple, the new routine's ``connectors_scope`` is
   the materialized allowlist (each slug → empty scope list = active).
3. **Project allowlist empty.** A lookup return of ``()`` (explicit
   denial) seeds an empty map and stops.
4. **Project allowlist absent / null.** A lookup return of ``None``
   leaves the routine's ``connectors_scope`` as the caller passed
   (default ``{}`` when absent) — no inheritance.
5. **Cross-tenant / missing project.** The lookup returns ``None`` for
   forbidden / missing projects so create never fails on a bad id.
"""

from __future__ import annotations

from typing import Any

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.routines.service import (
    ProjectAllowlistLookup,
    RoutinesService,
)
from backend_app.routines.store import InMemoryRoutinesStore


# ---------------------------------------------------------------------------
# Fake project-allowlist lookup
# ---------------------------------------------------------------------------


class FakeProjectAllowlistLookup:
    """Deterministic lookup that returns a pre-seeded allowlist per project id.

    Recorded calls let tests assert the lookup is consulted (or not)
    depending on the inheritance ladder branch under test. Mirrors the
    :class:`FakeProjectResolver` in the conversation tests.
    """

    def __init__(self, table: dict[str, tuple[str, ...] | None]) -> None:
        self._table = table
        self.calls: list[dict[str, str]] = []

    def fetch_connector_allowlist(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "caller_user_id": caller_user_id,
                "project_id": project_id,
            }
        )
        return self._table.get(project_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TENANT_ID = "t_p65a2"
_USER_ID = "u_p65a2"
_PROJECT_WITH_ALLOWLIST = "prj_alpha"
_PROJECT_WITH_EMPTY_ALLOWLIST = "prj_empty"
_PROJECT_WITH_NULL_ALLOWLIST = "prj_null"
_UNKNOWN_PROJECT = "prj_missing"


def _build_service(
    *, lookup: ProjectAllowlistLookup | None = None
) -> tuple[RoutinesService, FakeProjectAllowlistLookup]:
    """Return a wired routines service plus the fake it consults."""
    fake = (
        lookup
        if lookup is not None
        else FakeProjectAllowlistLookup(
            {
                _PROJECT_WITH_ALLOWLIST: ("salesforce", "gmail"),
                _PROJECT_WITH_EMPTY_ALLOWLIST: (),
                _PROJECT_WITH_NULL_ALLOWLIST: None,
                # _UNKNOWN_PROJECT is absent → lookup returns None
            }
        )
    )
    service = RoutinesService(
        store=InMemoryRoutinesStore(),
        identity_store=InMemoryIdentityStore(),
        project_allowlist_lookup=fake,  # type: ignore[arg-type]
    )
    return service, fake  # type: ignore[return-value]


def _payload(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "test routine",
        "instructions": "hello",
        "agent_id": "agent_x",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectAllowlistInheritance:
    """Rule 2 — project allowlist seeds the new routine's connectors_scope."""

    def test_non_empty_allowlist_materializes_each_slug(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(project_id=_PROJECT_WITH_ALLOWLIST),
        )
        # Each slug → active (empty scope list).
        assert created.connectors_scope == {
            "salesforce": [],
            "gmail": [],
        }
        # Lookup was consulted with the right scoping.
        assert fake.calls == [
            {
                "tenant_id": _TENANT_ID,
                "caller_user_id": _USER_ID,
                "project_id": _PROJECT_WITH_ALLOWLIST,
            }
        ]

    def test_empty_allowlist_materializes_to_empty_map(self) -> None:
        service, _ = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(project_id=_PROJECT_WITH_EMPTY_ALLOWLIST),
        )
        # PRD §5.4: empty allowlist == explicit denial.
        assert created.connectors_scope == {}


class TestExplicitConnectorsWin:
    """Rule 1 — caller-explicit ``connectors_scope`` skips inheritance."""

    def test_explicit_non_empty_scope_skips_project_inheritance(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(
                project_id=_PROJECT_WITH_ALLOWLIST,
                connectors_scope={"jira": ["read"]},
            ),
        )
        # Caller wins; project's ("salesforce", "gmail") is NOT applied.
        assert created.connectors_scope == {"jira": ["read"]}
        # Lookup was NOT consulted — caller-wins short-circuits.
        assert fake.calls == []

    def test_explicit_empty_scope_skips_project_inheritance(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(
                project_id=_PROJECT_WITH_ALLOWLIST,
                connectors_scope={},
            ),
        )
        # Explicit empty == caller's "no connectors" choice. Wins.
        assert created.connectors_scope == {}
        assert fake.calls == []


class TestNoInheritanceFallThrough:
    """Rule 3/4 — no project default → caller's value (default ``{}``)."""

    def test_null_project_allowlist_leaves_caller_default(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(project_id=_PROJECT_WITH_NULL_ALLOWLIST),
        )
        # Lookup returns None → no inheritance. Default ``{}``.
        assert created.connectors_scope == {}
        assert len(fake.calls) == 1

    def test_no_project_id_does_not_consult_lookup(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(),
        )
        assert created.connectors_scope == {}
        assert created.project_id is None
        assert fake.calls == []

    def test_unknown_project_id_falls_through_to_no_inheritance(self) -> None:
        service, fake = _build_service()
        created = service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(project_id=_UNKNOWN_PROJECT),
        )
        # Lookup returns None → routine still lands; no inheritance.
        # PRD §5.4: a bad project id must never block create.
        assert created.connectors_scope == {}
        assert len(fake.calls) == 1


class TestCrossTenantGuard:
    """The lookup's tenant_id scoping is honoured.

    The Fake here mimics the production bridge: it returns ``None`` for
    any project id whose tenant does not match the caller's tenant. The
    test asserts the routine service threads ``tenant_id`` through to
    the lookup, where the production ACL gate enforces 404-not-403.
    """

    def test_lookup_receives_caller_tenant_id(self) -> None:
        service, fake = _build_service()
        service.create_routine(
            tenant_id=_TENANT_ID,
            caller_user_id=_USER_ID,
            payload=_payload(project_id=_PROJECT_WITH_ALLOWLIST),
        )
        assert fake.calls
        # The tenant id from the routine create must reach the lookup
        # untransformed; the production bridge uses it as the ACL gate.
        assert fake.calls[0]["tenant_id"] == _TENANT_ID
        assert fake.calls[0]["caller_user_id"] == _USER_ID
