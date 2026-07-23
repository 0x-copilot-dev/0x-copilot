"""``ShapingModelResolver`` — the desktop shaping-on default (PRD-B3, SDR §13 #1).

Injected ``environ`` only (no process state). Pins the resolution ladder: an
explicit ``SURFACE_SPEC_MODEL`` always wins verbatim; with the flag off and no
explicit env the resolver is ``None`` (byte-identical to today); with the flag on
it returns the cheapest catalog model for the run's provider, and ``None`` when no
provider key is configured (BYOK posture).
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.shaping_policy import ShapingModelResolver


class TestShapingModelResolver:
    def test_explicit_model_env_wins(self) -> None:
        # An operator-set SURFACE_SPEC_MODEL is returned verbatim even with the
        # flag off and regardless of provider.
        env = {"SURFACE_SPEC_MODEL": "openai:gpt-5", "SURFACES_V2": "false"}
        assert (
            ShapingModelResolver.resolve(environ=env, run_provider="anthropic")
            == "openai:gpt-5"
        )

    def test_flag_off_returns_none_when_env_empty(self) -> None:
        # No explicit model + flag off ⇒ shaping stays opt-in on the env var alone.
        env: dict[str, str] = {"SURFACE_SPEC_MODEL": "", "SURFACES_V2": ""}
        assert ShapingModelResolver.resolve(environ=env, run_provider="openai") is None

    def test_flag_on_resolves_cheapest_for_provider(self) -> None:
        env = {"SURFACES_V2": "true"}
        assert (
            ShapingModelResolver.resolve(environ=env, run_provider="openai")
            == "gpt-5.4-mini"
        )
        assert (
            ShapingModelResolver.resolve(environ=env, run_provider="anthropic")
            == "claude-haiku-4-5"
        )
        assert (
            ShapingModelResolver.resolve(environ=env, run_provider="gemini")
            == "gemini-2.5-flash"
        )

    def test_no_provider_key_disables_shaping(self) -> None:
        env = {"SURFACES_V2": "true"}
        # No provider configured ⇒ nothing to shape with (honest off).
        assert ShapingModelResolver.resolve(environ=env, run_provider=None) is None
        assert ShapingModelResolver.resolve(environ=env, run_provider="") is None

    def test_unknown_provider_has_no_default_model(self) -> None:
        # OpenRouter / Ollama / custom compat have no cheap native tier here.
        env = {"SURFACES_V2": "true"}
        assert (
            ShapingModelResolver.resolve(environ=env, run_provider="openrouter") is None
        )
