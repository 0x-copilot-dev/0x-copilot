"""Route tests for the custom OpenAI-compatible endpoint add-flow (D-2).

Fully hermetic: a fake DNS resolver drives the SSRF guard and an httpx
``MockTransport`` answers the ``{base_url}/models`` probe, so no network is
touched. Verifies the base_url is required + SSRF-guarded on BOTH validate and
PUT, that a public endpoint round-trips (store + projection + live probe), and
that plaintext never leaks.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.provider_keys.live_validator import ProviderKeyLiveValidator
from backend_app.provider_keys.ssrf_guard import SsrfGuard
from backend_app.provider_keys.store import InMemoryProviderApiKeyStore
from backend_app.token_vault import LocalTokenVault


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_CUSTOM_KEY = "sk-custom-gateway-000000000000000000abcd"
_PUBLIC_BASE = "https://vllm.public.example/v1"
_PRIVATE_BASE = "https://vllm.internal.example/v1"

# Fake DNS: one public host, one that resolves to a private IP (rebinding).
_RESOLVER_MAP = {
    "vllm.public.example": ("93.184.216.34",),
    "vllm.internal.example": ("10.0.0.7",),
}


def _resolver(host: str) -> tuple[str, ...]:
    try:
        return _RESOLVER_MAP[host]
    except KeyError as exc:  # NXDOMAIN → fail closed
        raise OSError("nxdomain") from exc


def _guard() -> SsrfGuard:
    return SsrfGuard(allow_private_networks=False, resolver=_resolver)


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, 9, 1, 24, tzinfo=timezone.utc),
        )
    )
    return store


def _models_handler(request: httpx.Request) -> httpx.Response:
    # Only the custom probe target is expected; assert the shape rather than
    # silently 200 anything.
    assert request.url.path.endswith("/models")
    assert request.headers.get("Authorization") == f"Bearer {_CUSTOM_KEY}"
    return httpx.Response(200, json={"data": [{"id": "llama-3.1-70b"}]})


def _client(
    *,
    handler=_models_handler,
    guard: SsrfGuard | None = None,
) -> tuple[TestClient, InMemoryProviderApiKeyStore]:
    resolved_guard = guard or _guard()
    validator = ProviderKeyLiveValidator(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
        ssrf_guard=resolved_guard,
    )
    provider_keys = InMemoryProviderApiKeyStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        provider_api_keys_store=provider_keys,
        token_vault=LocalTokenVault(secret=_VAULT_SECRET),
        provider_key_live_validator=validator,
        provider_key_ssrf_guard=resolved_guard,
    )
    return TestClient(app), provider_keys


_PARAMS = {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestPutCustomEndpoint:
    def test_happy_path_stores_and_projects_endpoint(self) -> None:
        client, store = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai_compatible",
            params=_PARAMS,
            json={
                "api_key": _CUSTOM_KEY,
                "base_url": _PUBLIC_BASE,
                "label": "My vLLM",
                "default_model": "llama-3.1-70b",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"] == "openai_compatible"
        assert body["base_url"] == _PUBLIC_BASE
        assert body["label"] == "My vLLM"
        assert body["default_model"] == "llama-3.1-70b"
        assert body["live_check"] == "passed"
        assert _CUSTOM_KEY not in response.text
        # Persisted for real.
        from backend_app.provider_keys.store import ProviderName

        stored = store.get(
            org_id="org_acme",
            user_id="usr_sarah",
            provider=ProviderName.OPENAI_COMPATIBLE,
        )
        assert stored is not None
        assert stored.base_url == _PUBLIC_BASE
        assert stored.label == "My vLLM"

    def test_missing_base_url_is_400(self) -> None:
        client, _store = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai_compatible",
            params=_PARAMS,
            json={"api_key": _CUSTOM_KEY, "label": "My vLLM"},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "base_url_required"

    def test_ssrf_blocked_base_url_is_400_and_not_stored(self) -> None:
        client, store = _client()
        response = client.put(
            "/v1/settings/provider-keys/openai_compatible",
            params=_PARAMS,
            json={
                "api_key": _CUSTOM_KEY,
                "base_url": _PRIVATE_BASE,
                "label": "sneaky",
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "base_url_rejected:blocked_address"
        # Nothing was persisted — the guard runs before any store write.
        from backend_app.provider_keys.store import ProviderName

        assert (
            store.get(
                org_id="org_acme",
                user_id="usr_sarah",
                provider=ProviderName.OPENAI_COMPATIBLE,
            )
            is None
        )

    def test_list_projects_base_url_and_label(self) -> None:
        client, _store = _client()
        client.put(
            "/v1/settings/provider-keys/openai_compatible",
            params=_PARAMS,
            json={
                "api_key": _CUSTOM_KEY,
                "base_url": _PUBLIC_BASE,
                "label": "My vLLM",
            },
        )
        listing = client.get("/v1/settings/provider-keys", params=_PARAMS)
        assert listing.status_code == 200, listing.text
        entry = listing.json()["keys"][0]
        assert entry["provider"] == "openai_compatible"
        assert entry["base_url"] == _PUBLIC_BASE
        assert entry["label"] == "My vLLM"
        assert _CUSTOM_KEY not in listing.text


class TestValidateCustomEndpoint:
    def test_validate_probes_models_endpoint(self) -> None:
        client, _store = _client()
        response = client.post(
            "/v1/settings/provider-keys/openai_compatible/validate",
            params=_PARAMS,
            json={"api_key": _CUSTOM_KEY, "base_url": _PUBLIC_BASE},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["valid"] is True
        assert body["models"] == ["llama-3.1-70b"]
        assert _CUSTOM_KEY not in response.text

    def test_validate_blocked_base_url_is_400(self) -> None:
        client, _store = _client()
        response = client.post(
            "/v1/settings/provider-keys/openai_compatible/validate",
            params=_PARAMS,
            json={"api_key": _CUSTOM_KEY, "base_url": _PRIVATE_BASE},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "base_url_rejected:blocked_address"

    def test_validate_missing_base_url_is_400(self) -> None:
        client, _store = _client()
        response = client.post(
            "/v1/settings/provider-keys/openai_compatible/validate",
            params=_PARAMS,
            json={"api_key": _CUSTOM_KEY},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "base_url_required"
