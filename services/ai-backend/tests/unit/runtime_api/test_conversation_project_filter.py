"""PRD-07 — project-scoped conversation list + per-project counts route.

Exercises the full HTTP path (route → coordinator → query service → in-memory
store) for the ``project_id`` filter and the ``/conversations/counts`` endpoint.
The identity is supplied as query params (no trusted service token in the test
harness), so ``project_ids`` provably narrows the caller's OWN rows and is never
an authorization input.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from runtime_api.app import RuntimeApiAppFactory
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.settings import RuntimeSettings

_ORG = "org_p7"
_USER = "user_p7"


def _client() -> TestClient:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
    app.state.runtime_api_store = store
    return TestClient(app)


def _create(
    client: TestClient, *, project_id: str | None, user_id: str = _USER, title: str
) -> str:
    body: dict[str, Any] = {
        "org_id": _ORG,
        "user_id": user_id,
        "assistant_id": "assistant",
        "title": title,
    }
    if project_id is not None:
        body["project_id"] = project_id
    resp = client.post("/v1/agent/conversations", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["conversation_id"]


def test_list_conversations_filters_by_project_and_round_trips() -> None:
    client = _client()
    on_p1 = _create(client, project_id="p1", title="a")
    on_p2 = _create(client, project_id="p2", title="b")

    scope = {"org_id": _ORG, "user_id": _USER}

    p1 = client.get(
        "/v1/agent/conversations", params={**scope, "project_id": "p1"}
    ).json()["conversations"]
    p1_ids = {c["conversation_id"] for c in p1}
    assert on_p1 in p1_ids
    assert on_p2 not in p1_ids

    p2 = client.get(
        "/v1/agent/conversations", params={**scope, "project_id": "p2"}
    ).json()["conversations"]
    assert {c["conversation_id"] for c in p2} == {on_p2}

    # project_id round-trips through GET /conversations/{id}.
    got = client.get(f"/v1/agent/conversations/{on_p1}", params=scope).json()
    assert got["project_id"] == "p1"


def test_conversation_counts_returns_zeros_for_unknown_and_is_identity_scoped() -> None:
    client = _client()
    for i in range(3):
        _create(client, project_id="p1", title=f"mine-{i}")
    # Another user files one chat under p1 in the same org.
    _create(client, project_id="p1", user_id="other_user", title="theirs")

    counts = client.get(
        "/v1/agent/conversations/counts",
        params={"org_id": _ORG, "user_id": _USER, "project_ids": "p1,p2"},
    )
    assert counts.status_code == 200, counts.text
    assert counts.json()["counts"] == {"p1": 3, "p2": 0}

    # The count is identity-scoped, not project_ids-scoped: a DIFFERENT user
    # sees only their own single chat under p1 — proof project_ids filters and
    # never authorizes.
    other = client.get(
        "/v1/agent/conversations/counts",
        params={"org_id": _ORG, "user_id": "other_user", "project_ids": "p1"},
    ).json()
    assert other["counts"] == {"p1": 1}


def test_conversation_counts_route_is_not_shadowed_by_get_conversation() -> None:
    # ``/conversations/counts`` must register before ``/conversations/{id}`` so
    # the literal path is not swallowed as a conversation id (404).
    client = _client()
    resp = client.get(
        "/v1/agent/conversations/counts",
        params={"org_id": _ORG, "user_id": _USER, "project_ids": "p1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"] == {"p1": 0}
