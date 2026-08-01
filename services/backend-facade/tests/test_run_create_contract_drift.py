"""Every run-start field the apps can send must survive the facade.

WHY THIS FILE EXISTS RATHER THAN A FIFTH BESPOKE TEST.

``FacadeRunRequest`` is a typed Pydantic model, the route forwards
``payload.model_dump(exclude_none=True)``, and Pydantic's default is
``extra="ignore"``. So a field the model does not DECLARE is not rejected — it
is accepted, silently dropped, and forwarded as if the client never sent it.
The client sees ``200 OK``.

That trap has now bitten four times, each time earning its own one-field test:

* ``conversation_idempotency_key`` — new-chat sends 422'd before proxying
  (``test_new_chat_run_forwarding.py``)
* ``reasoning_depth`` — Fast/Balanced/Deep never reached the runtime
  (``test_reasoning_depth_forwarding.py``)
* ``web_search_enabled`` — the Tools toggle did nothing
* ``filesystem_bypass`` — the execution-mode pill was inoperable on BOTH hosts.
  The desktop sent it, the run sealed ``source: "master"`` ("no selection
  arrived"), and the pill could be moved to Bypass while every write still
  paused. Found by driving the packaged app; ~9,900 unit tests were green,
  because ai-backend tests start AT the coordinator and the app tests stop AT
  the composer. Nothing tested the hop between them.

Four instances of one bug is a missing invariant, not four mistakes. The
invariant — "the facade's run-create contract must not be narrower than the
runtime's" — is checked repo-side in ``tools/test_run_create_contract_drift.py``,
because comparing the two contracts means reading BOTH services and no
deployable component may import another's ``src``. That gate parses each model
with ``ast``, so it needs no import and no shared venv.

What lives HERE is the half a facade test can own honestly: that the field this
model now declares actually reaches ai-backend, and that omitting it stays
omitted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


def _bearer(
    *,
    org_id: str = "org_drift",
    user_id: str = "user_drift",
    secret: str = "test-auth-secret",
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
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")


def _install_capturing_forwarder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Intercept ``forward_json`` and capture the outbound JSON body."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "POST" and path == "/v1/agent/runs":
            captured.append(kwargs.get("json", {}))
            return {
                "run_id": "stub",
                "conversation_id": "conv_stub",
                "status": "queued",
            }
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


class TestFilesystemBypassSurvivesTheProxy:
    """The instance that motivated the invariant, pinned end to end."""

    @pytest.mark.parametrize(
        "selection",
        [
            {"message": "bypass"},
            {"run": "bypass"},
            {"run": "manual"},
        ],
    )
    def test_the_selection_reaches_ai_backend_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, selection: dict[str, str]
    ) -> None:
        captured = _install_capturing_forwarder(monkeypatch)
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/runs",
            headers={"authorization": _bearer()},
            json={
                "conversation_id": "conv_1",
                "user_input": "go",
                "filesystem_bypass": selection,
            },
        )

        assert response.status_code == 200, response.text
        assert captured[0].get("filesystem_bypass") == selection

    def test_an_ordinary_send_carries_no_bypass_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent must stay absent.

        ``exclude_none=True`` keeps the key out entirely, so a send with no
        selection is byte-identical to what it posted before bypass existed and
        the runtime applies its own default. An invented ``null`` would be a
        selection the user never made.
        """

        captured = _install_capturing_forwarder(monkeypatch)
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/runs",
            headers={"authorization": _bearer()},
            json={"conversation_id": "conv_1", "user_input": "go"},
        )

        assert response.status_code == 200, response.text
        assert "filesystem_bypass" not in captured[0]
