"""W0.1 — `Identity` Depends + strict `require_identity`.

Closes Bug 1 from the W0 QA report: workspace/drafts routes now route
through `require_identity` which raises 401 on missing headers. The
lenient `trusted_identity_from_request` keeps its dev "open" mode for
internal routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.identity import Identity


def _app_with_identity_route() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(identity: Identity) -> dict[str, str]:
        return {"org": identity.org_id, "user": identity.user_id}

    return app


class TestRequireIdentity:
    def test_401_when_org_header_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        client = TestClient(_app_with_identity_route())
        resp = client.get("/probe")
        assert resp.status_code == 401

    def test_returns_identity_for_valid_headers_in_dev(self, monkeypatch) -> None:
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        client = TestClient(_app_with_identity_route())
        resp = client.get(
            "/probe",
            headers={
                "x-enterprise-org-id": "org_acme",
                "x-enterprise-user-id": "usr_sarah",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"org": "org_acme", "user": "usr_sarah"}

    def test_returns_identity_with_service_token_in_prod(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "shared-secret")
        client = TestClient(_app_with_identity_route())
        resp = client.get(
            "/probe",
            headers={
                "x-enterprise-service-token": "shared-secret",
                "x-enterprise-org-id": "org_acme",
                "x-enterprise-user-id": "usr_sarah",
            },
        )
        assert resp.status_code == 200

    def test_401_on_service_token_mismatch(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "shared-secret")
        client = TestClient(_app_with_identity_route())
        resp = client.get(
            "/probe",
            headers={
                "x-enterprise-service-token": "WRONG",
                "x-enterprise-org-id": "org_acme",
                "x-enterprise-user-id": "usr_sarah",
            },
        )
        assert resp.status_code == 401


class TestLenientIdentityKeepsDevOpenMode:
    """``trusted_identity_from_request`` returns None in dev when no token
    AND no identity headers are present — that's how internal routes
    (audit cursor, system skills) stay reachable in dev. Bug 1 only
    affected tenant routes that incorrectly relied on this branch."""

    def test_lenient_returns_none_in_dev_without_anything(self, monkeypatch) -> None:
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)

        class _R:
            headers: dict[str, str] = {}

        request = _R()
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)  # type: ignore[arg-type]
        assert identity is None

    def test_lenient_returns_identity_when_headers_present(self, monkeypatch) -> None:
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)

        class _R:
            headers = {
                "x-enterprise-org-id": "org_acme",
                "x-enterprise-user-id": "usr_sarah",
            }

        request = _R()
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)  # type: ignore[arg-type]
        assert identity is not None
        assert identity.org_id == "org_acme"


class TestWorkspaceRoutesUseStrictIdentity:
    """Bug 1 closer: /sources, /subagents, /drafts now use the strict
    Identity dependency, so a request without identity headers gets a
    proper 401 (the missing-header signal) instead of the legacy 400
    that came from the route's own None-handling."""

    def test_workspace_subagents_route_returns_401_without_identity(
        self, monkeypatch
    ) -> None:
        from runtime_api.app import RuntimeApiAppFactory
        from agent_runtime.settings import RuntimeSettings
        from runtime_adapters.factory import RuntimeAdapterFactory
        from runtime_adapters.in_memory import InMemoryRuntimeApiStore

        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        async_store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(async_store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        client = TestClient(app)

        resp = client.get("/v1/agent/conversations/cid/subagents")
        # Was 400 ("org_id and user_id are required") pre-W0.1; now 401.
        assert resp.status_code == 401
