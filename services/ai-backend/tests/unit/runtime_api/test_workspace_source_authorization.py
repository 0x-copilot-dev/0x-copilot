"""Parent-scope authorization for the existing compatibility Sources feed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from runtime_api.http.workspace import register_workspace_feed_routes
from runtime_api.schemas.workspace import SourceListResponse


_ORG = "org_sources"
_OWNER = "user_owner"
_FOREIGN_USER = "user_foreign"
_RUN = "run_sources"
_CONVERSATION = "conv_sources"


def _headers(*, user_id: str = _OWNER) -> dict[str, str]:
    return {
        "x-enterprise-org-id": _ORG,
        "x-enterprise-user-id": user_id,
        "x-enterprise-permission-scopes": "runtime:use",
    }


class _Persistence:
    def __init__(self) -> None:
        self.runs: dict[tuple[str, str], object] = {
            (_ORG, _RUN): SimpleNamespace(
                run_id=_RUN,
                user_id=_OWNER,
                conversation_id=_CONVERSATION,
            ),
            (_ORG, "run_other_conversation"): SimpleNamespace(
                run_id="run_other_conversation",
                user_id=_OWNER,
                conversation_id="conv_other",
            ),
        }
        self.conversations: dict[tuple[str, str, str], object] = {
            (_ORG, _OWNER, _CONVERSATION): SimpleNamespace(
                conversation_id=_CONVERSATION,
                org_id=_ORG,
                user_id=_OWNER,
            ),
            (_ORG, _OWNER, "conv_other"): SimpleNamespace(
                conversation_id="conv_other",
                org_id=_ORG,
                user_id=_OWNER,
            ),
        }

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        return self.runs.get((org_id, run_id))

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> object | None:
        return self.conversations.get((org_id, user_id, conversation_id))


class _WorkspaceFeed:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def list_sources(self, **kwargs: object) -> SourceListResponse:
        self.calls.append(kwargs)
        return SourceListResponse(
            conversation_id=str(kwargs["conversation_id"]),
            run_id=kwargs["run_id"] if isinstance(kwargs["run_id"], str) else None,
        )


@pytest.fixture(autouse=True)
def _development_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "audit")


def _client() -> tuple[TestClient, _WorkspaceFeed]:
    app = FastAPI()
    router = APIRouter(prefix="/v1/agent")
    register_workspace_feed_routes(router)
    app.include_router(router)
    feed = _WorkspaceFeed()
    app.state.runtime_persistence = _Persistence()
    app.state.workspace_feed_service = feed
    return TestClient(app), feed


def test_sources_requires_verified_parent_membership_and_matching_run() -> None:
    client, feed = _client()
    base = f"/v1/agent/conversations/{_CONVERSATION}/sources"

    owner = client.get(f"{base}?run_id={_RUN}", headers=_headers())
    foreign_user = client.get(base, headers=_headers(user_id=_FOREIGN_USER))
    absent = client.get(
        "/v1/agent/conversations/conv_absent/sources", headers=_headers()
    )
    wrong_parent = client.get(
        f"{base}?run_id=run_other_conversation", headers=_headers()
    )

    assert owner.status_code == 200, owner.text
    assert feed.calls == [
        {
            "org_id": _ORG,
            "conversation_id": _CONVERSATION,
            "run_id": _RUN,
            "limit": 200,
        }
    ]
    assert (
        foreign_user.status_code
        == absent.status_code
        == wrong_parent.status_code
        == 404
    )
    assert foreign_user.json() == absent.json() == wrong_parent.json()
