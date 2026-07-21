"""Integration tests for the SurfaceSpec registry HTTP routes (PRD-08).

Internal-only routes under ``/internal/v1/surfaces/specs``: PUT/GET round-trip,
org isolation, invalid-spec 422, override precedence, delete, and the internal
service-token + org/user header discipline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.surface_specs import (
    InMemorySurfaceSpecStore,
    SurfaceSpecService,
)


def _identity_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    for org in ("org_acme", "org_globex"):
        store.create_organization(
            OrganizationRecord(org_id=org, display_name=org, slug=org.replace("_", "-"))
        )
    store.create_user(
        UserRecord(
            user_id="usr_alice",
            org_id="org_acme",
            primary_email="alice@acme.com",
            display_name="Alice",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_bob",
            org_id="org_globex",
            primary_email="bob@globex.com",
            display_name="Bob",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    return store


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, SurfaceSpecService]:
    # Dev posture: no service token expected, identity comes from query params
    # (the same shape the other /internal routes use in tests).
    monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
    service = SurfaceSpecService(store=InMemorySurfaceSpecStore())
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_identity_store(),
        surface_specs_service=service,
    )
    return TestClient(app), service


_SPECS = "/internal/v1/surfaces/specs"


def _params(*, org_id: str = "org_acme", user_id: str = "usr_alice") -> dict[str, str]:
    return {"org_id": org_id, "user_id": user_id}


def _spec() -> dict[str, object]:
    return {
        "spec_version": 1,
        "archetype": "record",
        "source": {"server": "linear", "tool": "get_issue"},
        "title_path": "issue.title",
        "fields": [{"label": "State", "path": "issue.state.name"}],
    }


def _upsert(
    *,
    shape: str = "h1",
    origin: str = "generated",
    spec: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "server": "linear",
        "tool": "get_issue",
        "output_shape_hash": shape,
        "spec_schema_version": 1,
        "skill_version": 1,
        "origin": origin,
        "generator_model": "haiku-test",
        "spec": spec or _spec(),
    }


class TestRoundTrip:
    def test_put_then_get(self, client: tuple[TestClient, SurfaceSpecService]) -> None:
        c, _ = client
        put = c.put(_SPECS, params=_params(), json=_upsert())
        assert put.status_code == 201, put.text
        body = put.json()
        assert body["origin"] == "generated"
        assert body["spec"]["archetype"] == "record"

        got = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
        )
        assert got.status_code == 200, got.text
        assert got.json()["spec"]["spec_id"] == body["spec_id"]

    def test_get_with_full_key(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        c.put(_SPECS, params=_params(), json=_upsert())
        got = c.get(
            _SPECS,
            params={
                **_params(),
                "server": "linear",
                "tool": "get_issue",
                "shape_hash": "h1",
                "schema_version": 1,
                "skill_version": 1,
            },
        )
        assert got.status_code == 200
        assert got.json()["spec"]["output_shape_hash"] == "h1"

    def test_get_miss_returns_null_spec_200(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        got = c.get(
            _SPECS,
            params={**_params(), "server": "nope", "tool": "missing"},
        )
        assert got.status_code == 200
        assert got.json()["spec"] is None


class TestOrgIsolation:
    def test_org_b_cannot_read_org_a(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        c.put(
            _SPECS,
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json=_upsert(),
        )
        got = c.get(
            _SPECS,
            params={
                **_params(org_id="org_globex", user_id="usr_bob"),
                "server": "linear",
                "tool": "get_issue",
            },
        )
        assert got.status_code == 200
        assert got.json()["spec"] is None


class TestValidation:
    def test_invalid_spec_returns_422(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        bad_spec = _spec()
        bad_spec["archetype"] = "carousel"
        resp = c.put(_SPECS, params=_params(), json=_upsert(spec=bad_spec))
        assert resp.status_code == 422, resp.text

    def test_wrong_spec_version_returns_422(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        bad_spec = _spec()
        bad_spec["spec_version"] = 2
        resp = c.put(_SPECS, params=_params(), json=_upsert(spec=bad_spec))
        assert resp.status_code == 422, resp.text


class TestOverridePrecedence:
    def test_override_wins_on_get(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        c.put(_SPECS, params=_params(), json=_upsert(origin="generated"))
        c.put(_SPECS, params=_params(), json=_upsert(origin="curated-override"))
        got = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
        )
        assert got.status_code == 200
        assert got.json()["spec"]["origin"] == "curated-override"


class TestDelete:
    def test_delete_then_get_null(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        put = c.put(_SPECS, params=_params(), json=_upsert())
        spec_id = put.json()["spec_id"]
        deleted = c.delete(f"{_SPECS}/{spec_id}", params=_params())
        assert deleted.status_code == 204, deleted.text
        got = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
        )
        assert got.json()["spec"] is None

    def test_delete_missing_returns_404(
        self, client: tuple[TestClient, SurfaceSpecService]
    ) -> None:
        c, _ = client
        resp = c.delete(f"{_SPECS}/sspec_missing", params=_params())
        assert resp.status_code == 404


class TestInternalAuth:
    """Service-token + org/user header discipline (token configured)."""

    def test_missing_service_token_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_identity_store(),
            surface_specs_service=SurfaceSpecService(store=InMemorySurfaceSpecStore()),
        )
        c = TestClient(app)
        resp = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
        )
        assert resp.status_code == 401, resp.text

    def test_missing_org_header_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_identity_store(),
            surface_specs_service=SurfaceSpecService(store=InMemorySurfaceSpecStore()),
        )
        c = TestClient(app)
        resp = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
            headers={SERVICE_TOKEN_HEADER: "tok-test", USER_HEADER: "usr_alice"},
        )
        assert resp.status_code == 401, resp.text

    def test_missing_user_header_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_identity_store(),
            surface_specs_service=SurfaceSpecService(store=InMemorySurfaceSpecStore()),
        )
        c = TestClient(app)
        resp = c.get(
            _SPECS,
            params={**_params(), "server": "linear", "tool": "get_issue"},
            headers={SERVICE_TOKEN_HEADER: "tok-test", ORG_HEADER: "org_acme"},
        )
        assert resp.status_code == 401, resp.text

    def test_identity_from_headers_not_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With a token configured, the trusted org comes from the header, not
        # the query param — a caller cannot write into another org by spoofing
        # ?org_id=. Write as org from header; the query names a different org.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        service = SurfaceSpecService(store=InMemorySurfaceSpecStore())
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=_identity_store(),
            surface_specs_service=service,
        )
        c = TestClient(app)
        headers = {
            SERVICE_TOKEN_HEADER: "tok-test",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_alice",
        }
        put = c.put(
            _SPECS,
            params={"org_id": "org_globex", "user_id": "usr_bob"},
            json=_upsert(),
            headers=headers,
        )
        assert put.status_code == 201, put.text
        # The spec landed in org_acme (the header identity), not org_globex.
        assert (
            service.get_spec(org_id="org_acme", server="linear", tool="get_issue")
            is not None
        )
        assert (
            service.get_spec(org_id="org_globex", server="linear", tool="get_issue")
            is None
        )
