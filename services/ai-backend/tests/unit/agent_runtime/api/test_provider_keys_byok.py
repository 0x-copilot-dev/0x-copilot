"""BYOK Phase-2 — provider-key snapshot parsing, context exclusion, hydration.

Security contract under test:

* ``ProviderKeysParser.split`` removes ``provider_keys`` from the policy
  snapshot before the remainder is stored on the (persisted)
  ``user_policies_json`` context field.
* ``AgentRuntimeContext.provider_keys`` is in-memory only: excluded from
  every ``model_dump`` / ``model_dump_json`` and absent from ``repr``.
* ``ProviderKeysHydrator`` re-attaches keys after the queue's JSON
  round-trip dropped them, without ever raising into the run path.
* ``HttpUserPoliciesResolver`` forwards the backend snapshot verbatim so
  the optional ``provider_keys`` field reaches the parser.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_runtime.api.user_policies_resolver import (
    HttpUserPoliciesResolver,
    NullUserPoliciesResolver,
    ProviderKeysHydrator,
    ProviderKeysParser,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, JsonObject


class ByokFixturesMixin:
    """Shared fake snapshot + context builders. Key values are obviously fake."""

    OPENAI_KEY = "sk-unit-test-openai-key-000000000000"
    GOOGLE_KEY = "AIzaUnitTestGeminiKey0000000000000"

    @classmethod
    def snapshot_with_keys(cls) -> JsonObject:
        return {
            "tool_use": {"mode": "auto"},
            "privacy": {"training_opt_out": True},
            "provider_keys": {"openai": cls.OPENAI_KEY, "google": cls.GOOGLE_KEY},
        }

    @staticmethod
    def build_context(**overrides: object) -> AgentRuntimeContext:
        base: dict[str, object] = {
            "user_id": "user_byok",
            "org_id": "org_byok",
            "roles": {"employee"},
            "model_profile": {
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        }
        base.update(overrides)
        return AgentRuntimeContext.model_validate(base)


class FakeSnapshotResolver:
    """Resolver stub returning a canned snapshot and counting calls."""

    def __init__(self, snapshot: JsonObject) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, *, org_id: str, user_id: str) -> JsonObject:
        self.calls.append((org_id, user_id))
        return self.snapshot


class TestProviderKeysParser(ByokFixturesMixin):
    def test_split_extracts_keys_and_sanitizes_snapshot(self) -> None:
        snapshot = self.snapshot_with_keys()
        keys, sanitized = ProviderKeysParser.split(snapshot)

        assert keys == {"openai": self.OPENAI_KEY, "gemini": self.GOOGLE_KEY}
        assert "provider_keys" not in sanitized
        assert sanitized["privacy"] == {"training_opt_out": True}
        # Input snapshot is never mutated.
        assert "provider_keys" in snapshot

    def test_google_slug_normalizes_to_gemini(self) -> None:
        keys, _ = ProviderKeysParser.split({"provider_keys": {"Google": " k1-value "}})
        assert keys == {"gemini": "k1-value"}

    def test_snapshot_without_keys_returns_same_object(self) -> None:
        snapshot: JsonObject = {"privacy": {}}
        keys, sanitized = ProviderKeysParser.split(snapshot)
        assert keys == {}
        assert sanitized is snapshot

    def test_malformed_entries_are_dropped(self) -> None:
        keys, sanitized = ProviderKeysParser.split(
            {
                "provider_keys": {
                    "openai": "",
                    "anthropic": 123,
                    "": "orphan-value",
                    "gemini": "  ",
                }
            }
        )
        assert keys == {}
        assert sanitized == {}

    def test_non_mapping_provider_keys_field_is_stripped(self) -> None:
        keys, sanitized = ProviderKeysParser.split({"provider_keys": "not-a-dict"})
        assert keys == {}
        assert "provider_keys" not in sanitized


class TestContextProviderKeysExclusion(ByokFixturesMixin):
    """The in-memory field must never reach a serialized surface."""

    def test_excluded_from_model_dump_and_json(self) -> None:
        context = self.build_context(provider_keys={"openai": self.OPENAI_KEY})

        dumped = context.model_dump(mode="json")
        assert "provider_keys" not in dumped
        assert self.OPENAI_KEY not in json.dumps(dumped)
        assert self.OPENAI_KEY not in context.model_dump_json()

    def test_absent_from_repr(self) -> None:
        context = self.build_context(provider_keys={"openai": self.OPENAI_KEY})
        assert self.OPENAI_KEY not in repr(context)

    def test_round_trip_through_dump_drops_keys(self) -> None:
        # Mirrors the queue path: model_dump → model_validate loses the keys.
        context = self.build_context(provider_keys={"openai": self.OPENAI_KEY})
        rebuilt = AgentRuntimeContext.model_validate(context.model_dump(mode="json"))
        assert rebuilt.provider_keys == {}

    def test_validator_normalizes_slugs(self) -> None:
        context = self.build_context(provider_keys={" OpenAI ": f" {self.OPENAI_KEY} "})
        assert context.provider_keys == {"openai": self.OPENAI_KEY}

    def test_validator_rejects_non_mapping_without_leaking_values(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            self.build_context(provider_keys=["not", "a", "mapping"])
        assert "provider_keys must be a mapping" in str(exc_info.value)

    def test_validator_rejects_empty_key_value(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            self.build_context(provider_keys={"openai": "   "})
        assert "non-empty" in str(exc_info.value)


class TestProviderKeysHydrator(ByokFixturesMixin):
    def test_hydrates_keys_from_resolver(self) -> None:
        resolver = FakeSnapshotResolver(self.snapshot_with_keys())
        hydrator = ProviderKeysHydrator(resolver=resolver)
        context = self.build_context()

        hydrated = asyncio.run(hydrator.hydrate(context))

        assert hydrated.provider_keys == {
            "openai": self.OPENAI_KEY,
            "gemini": self.GOOGLE_KEY,
        }
        assert resolver.calls == [("org_byok", "user_byok")]
        # Hydration must not leak into serialized surfaces either.
        assert self.OPENAI_KEY not in hydrated.model_dump_json()

    def test_noop_when_keys_already_present(self) -> None:
        resolver = FakeSnapshotResolver(self.snapshot_with_keys())
        hydrator = ProviderKeysHydrator(resolver=resolver)
        context = self.build_context(provider_keys={"openai": "already-set-key"})

        hydrated = asyncio.run(hydrator.hydrate(context))

        assert hydrated is context
        assert resolver.calls == []

    def test_noop_when_resolver_has_no_keys(self) -> None:
        hydrator = ProviderKeysHydrator(resolver=NullUserPoliciesResolver())
        context = self.build_context()

        hydrated = asyncio.run(hydrator.hydrate(context))

        assert hydrated is context
        assert hydrated.provider_keys == {}


class TestHttpResolverForwardsProviderKeys(ByokFixturesMixin):
    def test_snapshot_provider_keys_survive_the_http_hop(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self.snapshot_with_keys())

        resolver = HttpUserPoliciesResolver(
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://backend"
            ),
            backend_url="http://backend",
            service_token="svc-test-token",
        )

        snapshot = asyncio.run(resolver.resolve(org_id="org_byok", user_id="user_byok"))

        keys, sanitized = ProviderKeysParser.split(snapshot)
        assert keys == {"openai": self.OPENAI_KEY, "gemini": self.GOOGLE_KEY}
        assert "provider_keys" not in sanitized
