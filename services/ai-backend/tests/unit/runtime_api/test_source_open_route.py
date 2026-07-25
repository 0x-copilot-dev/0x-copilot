"""HTTP contract tests for the safe Sources v2 open route (E1 D4/D5)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agent_runtime.api.source_open_service import (
    SourceOpenDispositionV2,
    SourceOpenNotFoundError,
    SourceOpenResultV2,
)
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind
from agent_runtime.surfaces_v2.sources import SourceFactKindV2
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.routes import RuntimeApiRouter

_ORG = "acme"
_USER = "sarah"
_RUN = "run_source_open_route"
_SOURCE_ID = "source:v2:004:artifact"


def _headers(org: str = _ORG, user: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": org, "x-enterprise-user-id": user}


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


@dataclass
class _SourceOpener:
    result: SourceOpenResultV2 | Exception
    calls: list[dict[str, str]]

    async def open_source(self, **kwargs: str) -> SourceOpenResultV2:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _build(monkeypatch, *, flag_on: bool, opener: _SourceOpener) -> TestClient:
    monkeypatch.setenv("SURFACES_V2", "true" if flag_on else "false")
    store = InMemoryRuntimeApiStore()
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())
    app.state.source_open_service = opener
    return TestClient(app)


def _artifact_result() -> SourceOpenResultV2:
    return SourceOpenResultV2(
        source_id=_SOURCE_ID,
        kind=SourceFactKindV2.ARTIFACT,
        disposition=SourceOpenDispositionV2.ARTIFACT,
        artifact_id="art_safe_target",
        artifact_revision=2,
        artifact_kind=ArtifactKind.DOCUMENT,
    )


class TestSourceOpenRoute:
    def test_route_metadata_matches_the_d8_source_open_fixture(
        self, monkeypatch
    ) -> None:
        """Keep the owned route ready for D8's 29→30 inventory promotion.

        D8 reserves ``source_open`` as a POST member route whose authoritative
        resource boundary is an immutable ``artifact_revision``.  The latter
        is exercised by the service's owner/revision re-authorization tests;
        this test pins the route-table half without importing D8 before it
        merges.
        """

        monkeypatch.setenv("SURFACES_V2", "true")
        route = next(
            route
            for route in RuntimeApiRouter.create_router().routes
            if isinstance(route, APIRoute)
            and route.path == "/v1/agent/runs/{run_id}/sources/{source_id}/open"
        )

        assert route.name == "source_open"
        assert route.methods == {"POST"}

    def test_flag_off_route_is_absent(self, monkeypatch) -> None:
        opener = _SourceOpener(result=_artifact_result(), calls=[])
        client = _build(monkeypatch, flag_on=False, opener=opener)

        response = client.post(
            f"/v1/agent/runs/{_RUN}/sources/{_SOURCE_ID}/open",
            headers=_headers(),
        )

        assert response.status_code == 404
        assert opener.calls == []

    def test_open_passes_only_trusted_scope_and_safe_response(
        self, monkeypatch
    ) -> None:
        opener = _SourceOpener(result=_artifact_result(), calls=[])
        client = _build(monkeypatch, flag_on=True, opener=opener)

        response = client.post(
            f"/v1/agent/runs/{_RUN}/sources/{_SOURCE_ID}/open",
            headers=_headers(),
            # Malicious client input cannot become part of the service request.
            json={
                "physical_path": "/Users/sarah/private.md",
                "cookie": "session=secret",
                "raw_args": {"provider_token": "sk-never-return-this"},
                "body": "full body",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "v": 2,
            "source_id": _SOURCE_ID,
            "kind": "artifact",
            "disposition": "artifact",
            "artifact_id": "art_safe_target",
            "artifact_revision": 2,
            "artifact_kind": "document",
        }
        assert opener.calls == [
            {
                "org_id": _ORG,
                "user_id": _USER,
                "run_id": _RUN,
                "source_id": _SOURCE_ID,
            }
        ]

    def test_not_found_is_generic_and_missing_identity_never_opens(
        self, monkeypatch
    ) -> None:
        opener = _SourceOpener(result=SourceOpenNotFoundError(), calls=[])
        client = _build(monkeypatch, flag_on=True, opener=opener)

        missing_identity = client.post(
            f"/v1/agent/runs/{_RUN}/sources/{_SOURCE_ID}/open"
        )
        assert missing_identity.status_code == 401
        assert opener.calls == []

        response = client.post(
            f"/v1/agent/runs/{_RUN}/sources/{_SOURCE_ID}/open",
            headers=_headers(),
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Source is not available for this scope."}
        assert opener.calls == [
            {
                "org_id": _ORG,
                "user_id": _USER,
                "run_id": _RUN,
                "source_id": _SOURCE_ID,
            }
        ]
