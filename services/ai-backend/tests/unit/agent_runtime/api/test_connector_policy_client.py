"""Unit tests for the PRD-C2 :class:`HttpConnectorWritePolicyClient`.

A fake ``httpx.AsyncClient`` records the two hops (resolve-by-slug GET, then the
write-policy PATCH) and lets each be forced to fail so the fail-closed contract
(any non-2xx / missing connector ⇒ ``GatePolicyPersistError``) is pinned.
"""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.api.connector_policy_client import (
    GatePolicyPersistError,
    HttpConnectorWritePolicyClient,
    build_connector_write_policy_client,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeResponse:
    def __init__(self, *, status: int = 200, json_body: object = None) -> None:
        self.status_code = status
        self._json = json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "http://x"),
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> object:
        return self._json


class _FakeClient:
    def __init__(
        self, *, get_response: _FakeResponse, patch_response: _FakeResponse
    ) -> None:
        self.get_response = get_response
        self.patch_response = patch_response
        self.get_calls: list[dict] = []
        self.patch_calls: list[dict] = []

    async def get(self, url, *, params, headers, timeout):  # noqa: ANN001
        self.get_calls.append({"url": url, "params": params})
        return self.get_response

    async def patch(self, url, *, params, json, headers, timeout):  # noqa: ANN001
        self.patch_calls.append({"url": url, "params": params, "json": json})
        return self.patch_response


def _client(fake: _FakeClient) -> HttpConnectorWritePolicyClient:
    return HttpConnectorWritePolicyClient(
        base_url="http://backend:8100",
        http_client=fake,  # type: ignore[arg-type]
        service_token="svc-token",
    )


async def test_resolves_slug_then_patches_by_id() -> None:
    fake = _FakeClient(
        get_response=_FakeResponse(
            json_body={"connectors": [{"id": "conn_abc", "slug": "linear"}]}
        ),
        patch_response=_FakeResponse(status=200),
    )
    await _client(fake).put_override(
        org_id="org_1",
        user_id="user_1",
        connector_slug="linear",
        write_policy="allow_always",
    )
    assert fake.get_calls[0]["params"]["slug"] == "linear"
    assert fake.patch_calls[0]["url"].endswith("/v1/connectors/conn_abc/write-policy")
    assert fake.patch_calls[0]["json"] == {"write_policy": "allow_always"}


async def test_unknown_connector_raises() -> None:
    fake = _FakeClient(
        get_response=_FakeResponse(json_body={"connectors": []}),
        patch_response=_FakeResponse(status=200),
    )
    with pytest.raises(GatePolicyPersistError):
        await _client(fake).put_override(
            org_id="org_1",
            user_id="user_1",
            connector_slug="linear",
            write_policy="ask_first",
        )
    assert fake.patch_calls == []  # never patched


async def test_patch_failure_raises() -> None:
    fake = _FakeClient(
        get_response=_FakeResponse(
            json_body={"connectors": [{"id": "conn_abc", "slug": "linear"}]}
        ),
        patch_response=_FakeResponse(status=502),
    )
    with pytest.raises(GatePolicyPersistError):
        await _client(fake).put_override(
            org_id="org_1",
            user_id="user_1",
            connector_slug="linear",
            write_policy="ask_first",
        )


async def test_missing_base_url_raises() -> None:
    fake = _FakeClient(
        get_response=_FakeResponse(json_body={"connectors": []}),
        patch_response=_FakeResponse(status=200),
    )
    client = HttpConnectorWritePolicyClient(base_url="", http_client=fake)  # type: ignore[arg-type]
    with pytest.raises(GatePolicyPersistError):
        await client.put_override(
            org_id="o", user_id="u", connector_slug="linear", write_policy="ask_first"
        )


def test_builder_returns_none_without_backend_url() -> None:
    assert build_connector_write_policy_client({}) is None
