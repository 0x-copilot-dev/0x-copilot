"""Facade must forward the three run routes that no app could previously reach.

``host-writes``, ``host-writes/revert`` and ``steer`` were backend-complete and
called by nothing: apps may talk only to the facade, and the facade named none
of these paths. They were carried in ``tools/route_reachability_baseline.txt``
until this forward landed.

Passthrough only — the facade owns no undo and no steering logic, it owns the
boundary. What is worth guarding:

- **Method / path / target.** Each reaches ai-backend at the path the client
  used, so ai-backend stays the single source of route truth.
- **Identity is the facade's, never the caller's.** A client that supplies its
  own ``org_id`` / ``user_id`` query values must not widen the read, and a
  client that names another user in a *steer body* must not put words into that
  user's run — ``requested_by_user_id`` is overwritten from the verified
  session exactly as ``cancel`` overwrites it.
- **No bearer, no forward.** The upstream is never dialled for an
  unauthenticated caller.
- **Upstream errors survive.** The ``503`` for a deployment with no capture
  store and the ``409`` for a run no longer in flight are ai-backend's calls;
  the facade must not flatten either into a 200 or a 500.

Mirrors the capture-``forward_json`` pattern from ``test_context_occupancy_proxy``.
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

_ORG_ID = "org_host_writes_facade"
_USER_ID = "user_host_writes_facade"
_RUN_ID = "run_host_writes_facade"
_SECRET = "test-auth-secret"
_LIST_PATH = f"/v1/agent/runs/{_RUN_ID}/host-writes"
_REVERT_PATH = f"/v1/agent/runs/{_RUN_ID}/host-writes/revert"
_STEER_PATH = f"/v1/agent/runs/{_RUN_ID}/steer"
_SCOPED = {"org_id": _ORG_ID, "user_id": _USER_ID}


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
    expected_method: str,
    expected_path: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Capture the single forward this facade route is allowed to make."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001, ARG001
        if (
            target == "ai_backend"
            and method == expected_method
            and path == expected_path
        ):
            captured.append(
                {
                    "method": method,
                    "path": path,
                    "params": kwargs.get("params"),
                    "json": kwargs.get("json"),
                    "identity": kwargs.get("identity"),
                }
            )
            return body
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def _install_raising_forwarder(
    monkeypatch: pytest.MonkeyPatch, *, status_code: int, detail: str
) -> None:
    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001, ARG001
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(facade_app, "forward_json", _forward)


def _client() -> TestClient:
    return TestClient(create_app(FacadeSettings()))


class TestListHostWritesProxy:
    """``GET /v1/agent/runs/{run_id}/host-writes``."""

    _BODY: dict[str, Any] = {"run_id": _RUN_ID, "entries": []}

    def test_proxies_with_org_and_user_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="GET",
            expected_path=_LIST_PATH,
            body=self._BODY,
        )

        response = _client().get(_LIST_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 200, response.text
        assert response.json() == self._BODY
        assert len(captured) == 1
        assert captured[0]["params"] == _SCOPED
        identity = captured[0]["identity"]
        assert (identity.org_id, identity.user_id) == (_ORG_ID, _USER_ID)

    def test_caller_supplied_identity_cannot_widen_the_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run's disk journal is the one thing a cross-tenant probe wants."""

        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="GET",
            expected_path=_LIST_PATH,
            body=self._BODY,
        )

        response = _client().get(
            _LIST_PATH,
            params={"org_id": "attacker_org", "user_id": "attacker_user"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["params"] == _SCOPED

    def test_requires_a_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="GET",
            expected_path=_LIST_PATH,
            body=self._BODY,
        )

        assert _client().get(_LIST_PATH).status_code == 401
        assert captured == []

    def test_surfaces_the_capability_absent_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No capture store on this deployment is a 503, not an empty 200.

        An empty listing would read as "this run changed nothing on disk",
        which is the opposite of "we cannot tell you what it changed".
        """

        _install_raising_forwarder(
            monkeypatch,
            status_code=503,
            detail="Agent-write undo is not available on this deployment.",
        )

        response = _client().get(_LIST_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 503
        assert (
            response.json()["detail"]
            == "Agent-write undo is not available on this deployment."
        )


class TestRevertHostWritesProxy:
    """``POST /v1/agent/runs/{run_id}/host-writes/revert``."""

    _BODY: dict[str, Any] = {"run_id": _RUN_ID, "reverted": 0, "entries": []}

    def test_a_bare_post_reverts_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The coarse action a user reaches for after a run goes wrong.

        The body is optional, so a bare POST must reach ai-backend as ``{}``
        rather than being refused at the edge with a 422.
        """

        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_REVERT_PATH,
            body=self._BODY,
        )

        response = _client().post(_REVERT_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 200, response.text
        assert captured[0]["json"] == {}
        assert captured[0]["params"] == _SCOPED

    def test_forwards_a_tool_call_narrowing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_REVERT_PATH,
            body=self._BODY,
        )

        response = _client().post(
            _REVERT_PATH,
            json={"tool_call_id": "call_7"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["json"] == {"tool_call_id": "call_7"}

    def test_requires_a_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_REVERT_PATH,
            body=self._BODY,
        )

        assert _client().post(_REVERT_PATH).status_code == 401
        assert captured == []

    def test_surfaces_the_unknown_run_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing run and someone else's run are the same 404 upstream.

        The facade must pass that through rather than translating it, or the
        distinction ai-backend deliberately collapses reappears here.
        """

        _install_raising_forwarder(
            monkeypatch, status_code=404, detail="Run not found."
        )

        response = _client().post(_REVERT_PATH, headers={"authorization": _bearer()})

        assert response.status_code == 404
        assert response.json()["detail"] == "Run not found."


class TestSteerRunProxy:
    """``POST /v1/agent/runs/{run_id}/steer``."""

    _BODY: dict[str, Any] = {"steer_id": "steer_1", "sequence_no": 12}

    def test_proxies_with_org_and_user_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_STEER_PATH,
            body=self._BODY,
        )

        response = _client().post(
            _STEER_PATH,
            json={"text": "use the staging table instead"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert response.json() == self._BODY
        assert captured[0]["params"] == _SCOPED
        assert captured[0]["json"] == {
            "text": "use the staging table instead",
            "requested_by_user_id": _USER_ID,
        }

    def test_body_supplied_identity_is_overwritten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Honouring a body-supplied id would let one user speak as another.

        ai-backend stamps the verified session over this field too, but the
        facade is the boundary that faces the client, so the caller's value
        must not survive the hop.
        """

        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_STEER_PATH,
            body=self._BODY,
        )

        response = _client().post(
            _STEER_PATH,
            json={"text": "stop", "requested_by_user_id": "victim_user"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 200, response.text
        assert captured[0]["json"]["requested_by_user_id"] == _USER_ID

    def test_requires_a_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _install_capturing_forwarder(
            monkeypatch,
            expected_method="POST",
            expected_path=_STEER_PATH,
            body=self._BODY,
        )

        response = _client().post(_STEER_PATH, json={"text": "stop"})

        assert response.status_code == 401
        assert captured == []

    def test_surfaces_the_run_not_in_flight_409(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A steer that cannot be read is refused, not silently accepted."""

        _install_raising_forwarder(
            monkeypatch, status_code=409, detail="Run is no longer in flight."
        )

        response = _client().post(
            _STEER_PATH,
            json={"text": "stop"},
            headers={"authorization": _bearer()},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Run is no longer in flight."
