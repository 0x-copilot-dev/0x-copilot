"""Facade must proxy the Context Occupancy Ledger read API to ai-backend.

Passthrough only — the facade owns no occupancy logic, it owns the boundary.
What is actually worth guarding here:

- **Method / path / target.** Both endpoints reach ai-backend at the same path
  the client used, so ai-backend stays the single source of route truth.
- **Identity is the facade's, never the caller's.** A client that supplies its
  own ``org_id`` / ``user_id`` query values must not widen the read.
- **The one client input is constrained at the edge.** ``graph_scope`` is a
  closed vocabulary; a typo is refused here rather than silently becoming an
  all-scopes read, which is the request that invites a cross-scope sum.
- **No shadowing.** ``/context`` still proxies the window summary;
  ``/context/occupancy`` is a distinct sub-resource beside it.

Mirrors the capture-``forward_json`` pattern from ``test_run_surfaces_proxy``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_ORG_ID = "org_occupancy_facade"
_USER_ID = "user_occupancy_facade"
_RUN_ID = "run_occupancy_facade"
_CONVERSATION_ID = "conv_occupancy_facade"
_SECRET = "test-auth-secret"
_RUN_PATH = f"/v1/agent/runs/{_RUN_ID}/context/occupancy"
_CONVERSATION_PATH = f"/v1/agent/conversations/{_CONVERSATION_ID}/context/occupancy"


def _bearer(
    *,
    org_id: str = _ORG_ID,
    user_id: str = _USER_ID,
    secret: str = _SECRET,
) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "roles": ["employee"],
                    "permission_scopes": ["runtime:use"],
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"Bearer {payload}.{signature}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")


def _install_capturing_forwarder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_path: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Capture the single forward this facade route is allowed to make."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001, ARG001
        if target == "ai_backend" and method == "GET" and path == expected_path:
            captured.append(
                {
                    "method": method,
                    "path": path,
                    "params": kwargs.get("params"),
                    "identity": kwargs.get("identity"),
                }
            )
            return body
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def _client() -> TestClient:
    return TestClient(create_app(FacadeSettings()))


class TestRunOccupancyProxy:
    """``GET /v1/agent/runs/{run_id}/context/occupancy``."""

    _BODY = {"run_id": _RUN_ID, "graph_scope": None, "snapshots": []}

    def test_proxies_with_org_and_user_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_RUN_PATH, body=self._BODY
        )

        response = _client().get(_RUN_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 200, response.text
        assert response.json() == self._BODY
        assert len(captured) == 1
        assert captured[0]["method"] == "GET"
        assert captured[0]["path"] == _RUN_PATH
        # ai-backend scopes occupancy on (org, user) — the run must belong to the
        # caller, exactly as /v1/usage/runs/{run_id} requires — so both ride.
        assert captured[0]["params"] == {"org_id": _ORG_ID, "user_id": _USER_ID}

    def test_omits_graph_scope_when_the_client_does_not_ask_for_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An absent filter must not become an explicit ``graph_scope=None``."""

        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_RUN_PATH, body=self._BODY
        )

        _client().get(_RUN_PATH, headers={"authorization": _bearer()})

        assert "graph_scope" not in captured[0]["params"]

    @pytest.mark.parametrize("scope", ["root", "subagent"])
    def test_forwards_a_valid_graph_scope(
        self, monkeypatch: pytest.MonkeyPatch, scope: str
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_RUN_PATH, body=self._BODY
        )

        response = _client().get(
            _RUN_PATH,
            params={"graph_scope": scope},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["params"] == {
            "org_id": _ORG_ID,
            "user_id": _USER_ID,
            "graph_scope": scope,
        }

    def test_rejects_an_unknown_graph_scope_without_forwarding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo must not fall through to an all-scopes read.

        Root and subagent snapshots describe different windows, so an
        unintentionally unfiltered series is the exact input that produces a
        cross-scope sum in a client.
        """

        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_RUN_PATH, body=self._BODY
        )

        response = _client().get(
            _RUN_PATH,
            params={"graph_scope": "orchestrator"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 422
        assert captured == []

    def test_caller_supplied_identity_cannot_widen_the_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_RUN_PATH, body=self._BODY
        )

        response = _client().get(
            _RUN_PATH,
            params={"org_id": "attacker_org", "user_id": "attacker_user"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["params"] == {"org_id": _ORG_ID, "user_id": _USER_ID}
        identity = captured[0]["identity"]
        assert (identity.org_id, identity.user_id) == (_ORG_ID, _USER_ID)

    def test_requires_a_bearer(self) -> None:
        assert _client().get(_RUN_PATH).status_code == 401

    def test_surfaces_upstream_errors_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001, ARG001
            raise HTTPException(status_code=503, detail="ai-backend unavailable")

        monkeypatch.setattr(facade_app, "forward_json", _forward)

        response = _client().get(_RUN_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 503
        assert response.json()["detail"] == "ai-backend unavailable"


class TestConversationOccupancyProxy:
    """``GET /v1/agent/conversations/{conversation_id}/context/occupancy``."""

    _BODY = {
        "conversation_id": _CONVERSATION_ID,
        "run_id": None,
        "snapshot": None,
    }

    def test_proxies_with_org_and_user_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_CONVERSATION_PATH, body=self._BODY
        )

        response = _client().get(
            _CONVERSATION_PATH, headers={"authorization": _bearer()}
        )

        assert response.status_code == 200, response.text
        assert response.json() == self._BODY
        assert captured[0]["path"] == _CONVERSATION_PATH
        assert captured[0]["params"] == {"org_id": _ORG_ID, "user_id": _USER_ID}

    def test_caller_supplied_identity_cannot_widen_the_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch, expected_path=_CONVERSATION_PATH, body=self._BODY
        )

        response = _client().get(
            _CONVERSATION_PATH,
            params={"org_id": "attacker_org", "user_id": "attacker_user"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["params"] == {"org_id": _ORG_ID, "user_id": _USER_ID}

    def test_requires_a_bearer(self) -> None:
        assert _client().get(_CONVERSATION_PATH).status_code == 401

    def test_does_not_shadow_the_existing_context_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/context`` keeps forwarding the window summary, unchanged.

        The occupancy endpoint is a sub-resource beside it precisely because
        taking ``/context`` would have made one of the two routes dead code.
        """

        summary_path = f"/v1/agent/conversations/{_CONVERSATION_ID}/context"
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_path=summary_path,
            body={"model": {}, "current": {}, "breakdown": {}},
        )

        response = _client().get(summary_path, headers={"authorization": _bearer()})

        assert response.status_code == 200, response.text
        assert captured[0]["path"] == summary_path


class TestOccupancyRoutesAreRegistered:
    """Static registration guard, mirroring ``test_public_route_contract``."""

    def test_both_paths_are_on_the_public_surface(self) -> None:
        paths = create_app(FacadeSettings()).openapi()["paths"]

        assert "/v1/agent/runs/{run_id}/context/occupancy" in paths
        assert "/v1/agent/conversations/{conversation_id}/context/occupancy" in paths
