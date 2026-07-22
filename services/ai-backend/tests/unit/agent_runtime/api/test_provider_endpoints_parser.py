"""Tests for ProviderEndpointsParser + the persistable provider_endpoints field.

Decision D-2. Unlike ``provider_keys`` (secret, serialization-excluded, hydrated
on the worker), ``provider_endpoints`` is NON-secret: it is split out for a
single home, but it PERSISTS through ``model_dump`` and the queue round-trip.
"""

from __future__ import annotations

import json

from agent_runtime.api.user_policies_resolver import (
    ProviderEndpointsParser,
    ProviderKeysParser,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, JsonObject

_BASE_URL = "https://vllm.example/v1"


def _context(**overrides: object) -> AgentRuntimeContext:
    base: dict[str, object] = {
        "user_id": "user_byok",
        "org_id": "org_byok",
        "roles": {"employee"},
        "model_profile": {
            "provider": "openai_compatible",
            "model_name": "llama-3.1-70b",
            "max_input_tokens": 8192,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
    }
    base.update(overrides)
    return AgentRuntimeContext.model_validate(base)


class TestProviderEndpointsParser:
    def test_split_extracts_endpoints_and_sanitizes(self) -> None:
        snapshot: JsonObject = {
            "privacy": {"training_opt_out": True},
            "provider_endpoints": {"openai_compatible": _BASE_URL},
        }
        endpoints, sanitized = ProviderEndpointsParser.split(snapshot)
        assert endpoints == {"openai_compatible": _BASE_URL}
        assert "provider_endpoints" not in sanitized
        assert sanitized["privacy"] == {"training_opt_out": True}
        # Input snapshot never mutated.
        assert "provider_endpoints" in snapshot

    def test_google_slug_normalizes_to_gemini(self) -> None:
        endpoints, _ = ProviderEndpointsParser.split(
            {"provider_endpoints": {"Google": " https://g.example/v1 "}}
        )
        assert endpoints == {"gemini": "https://g.example/v1"}

    def test_absent_field_returns_same_object(self) -> None:
        snapshot: JsonObject = {"privacy": {}}
        endpoints, sanitized = ProviderEndpointsParser.split(snapshot)
        assert endpoints == {}
        assert sanitized is snapshot

    def test_malformed_entries_dropped(self) -> None:
        endpoints, sanitized = ProviderEndpointsParser.split(
            {
                "provider_endpoints": {
                    "openai_compatible": "",
                    "x": 123,
                    "": "orphan",
                }
            }
        )
        assert endpoints == {}
        assert sanitized == {}

    def test_composes_after_keys_parser(self) -> None:
        # Both split out of the same snapshot in the coordinator order.
        snapshot: JsonObject = {
            "privacy": {},
            "provider_keys": {"openai_compatible": "sk-secret"},
            "provider_endpoints": {"openai_compatible": _BASE_URL},
        }
        keys, without_keys = ProviderKeysParser.split(snapshot)
        endpoints, without_both = ProviderEndpointsParser.split(without_keys)
        assert keys == {"openai_compatible": "sk-secret"}
        assert endpoints == {"openai_compatible": _BASE_URL}
        assert without_both == {"privacy": {}}


class TestPersistableField:
    def test_endpoints_survive_model_dump(self) -> None:
        # Non-secret: MUST persist (opposite of provider_keys).
        context = _context(provider_endpoints={"openai_compatible": _BASE_URL})
        dumped = context.model_dump(mode="json")
        assert dumped["provider_endpoints"] == {"openai_compatible": _BASE_URL}
        assert _BASE_URL in context.model_dump_json()

    def test_endpoints_survive_round_trip(self) -> None:
        context = _context(provider_endpoints={"openai_compatible": _BASE_URL})
        restored = AgentRuntimeContext.model_validate(
            json.loads(context.model_dump_json())
        )
        assert restored.provider_endpoints == {"openai_compatible": _BASE_URL}
