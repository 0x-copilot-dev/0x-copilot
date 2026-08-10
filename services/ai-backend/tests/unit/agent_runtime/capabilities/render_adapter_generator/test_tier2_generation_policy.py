"""PRD-10 AC5 — the tier-2 generation policy is OFF by default.

Guards the exact dark-capability shape the repo's ``check_dark_capabilities``
gate exists to prevent: a privileged, off-by-default capability that no test
drives ON. These tests reference ``RUNTIME_TIER2_GENERATION`` on both branches.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.render_adapter_generator import (
    LayoutTemplate,
    RenderAdapterGenerator,
    Tier2GenerationFlag,
    should_invoke_tier2_generator,
)
from agent_runtime.capabilities.surfaces.spec_models import SurfaceArchetype

_FLAG = "RUNTIME_TIER2_GENERATION"


class TestTier2GenerationFlag:
    def test_defaults_off_when_unset(self) -> None:
        assert Tier2GenerationFlag.enabled({}) is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_enabled_for_truthy_values(self, value: str) -> None:
        assert Tier2GenerationFlag.enabled({_FLAG: value}) is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_disabled_for_falsey_values(self, value: str) -> None:
        assert Tier2GenerationFlag.enabled({_FLAG: value}) is False


class TestShouldInvokeTier2Generator:
    def test_off_by_default_even_for_unexpressible_surface(self) -> None:
        # AC5: a normal run (flag unset) never unlocks the generator.
        assert should_invoke_tier2_generator(archetype=None, environ={}) is False

    def test_enabled_only_for_unexpressible_surface(self) -> None:
        assert (
            should_invoke_tier2_generator(archetype=None, environ={_FLAG: "true"})
            is True
        )

    def test_expressible_archetype_never_routes_to_tier2(self) -> None:
        # Even with the flag ON, an archetype the SurfaceSpec vocabulary covers
        # must never route through tier-2 (D1 — the projector prefers specs).
        assert (
            should_invoke_tier2_generator(
                archetype=SurfaceArchetype.RECORD, environ={_FLAG: "true"}
            )
            is False
        )


class TestGeneratorNotInvokedByDefault:
    """AC5 end-to-end: a caller gated by the policy does not run the generator
    in a normal (flag-unset) run."""

    @pytest.mark.asyncio
    async def test_generator_generate_is_not_called_when_flag_off(self) -> None:
        generator = RenderAdapterGenerator()
        calls: list[str] = []
        original = generator.generate

        async def _spy(**kwargs: object):  # type: ignore[no-untyped-def]
            calls.append("generate")
            return await original(**kwargs)

        generator.generate = _spy  # type: ignore[method-assign]

        # A representative caller: only invoke the generator when policy allows.
        if should_invoke_tier2_generator(archetype=None, environ={}):
            await generator.generate(
                scheme="x",
                sample_state={"id": "1"},
                layout_template=LayoutTemplate.FORM,
            )

        assert calls == []

    @pytest.mark.asyncio
    async def test_generator_runs_when_flag_on_and_unexpressible(self) -> None:
        generator = RenderAdapterGenerator()

        result = None
        if should_invoke_tier2_generator(archetype=None, environ={_FLAG: "1"}):
            result = await generator.generate(
                scheme="record",
                sample_state={"id": "1", "title": "Hello"},
                layout_template=LayoutTemplate.FORM,
            )

        assert result is not None
        assert result.scheme == "record"
