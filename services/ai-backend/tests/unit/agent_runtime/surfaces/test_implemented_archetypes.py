"""AC16 — generation is licensed by what a renderer draws, validation is not.

The SurfaceSpec contract carries ten archetypes; ``packages/surface-renderers``
implements five. The other five collapse to the generic view, and the generator
was licensed for all ten, so it could be asked to author a shape nobody could
draw. ``SurfaceArchetype.implemented()`` is the narrower licence, sourced from
``copilot_service_contracts.implemented_archetypes`` — the same on-disk file the
TypeScript side pins to ``ARCHETYPE_ADAPTERS``.

The two halves are asserted separately on purpose. Widening the licence must
require an edit to the shared file (so it cannot happen by accident), while
*validation* must keep accepting every archetype the contract ever licensed, or
replaying an older run stops rendering.
"""

from __future__ import annotations

import pytest
from copilot_service_contracts.implemented_archetypes import (
    IMPLEMENTED_SURFACE_ARCHETYPES,
)
from copilot_service_contracts.surface_spec import SURFACE_ARCHETYPES

from agent_runtime.capabilities.surfaces import spec_models
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    validate_surface_spec,
)


class LicensedArchetypeMixin:
    """Shared vocabulary + a minimal valid spec for the licence tests."""

    SHARED_CONSTANT = "IMPLEMENTED_SURFACE_ARCHETYPES"

    @staticmethod
    def licensed_values() -> list[str]:
        return [member.value for member in SurfaceArchetype.implemented()]

    @staticmethod
    def unlicensed_values() -> list[str]:
        licensed = set(IMPLEMENTED_SURFACE_ARCHETYPES)
        return [name for name in SURFACE_ARCHETYPES if name not in licensed]

    @staticmethod
    def spec_with(archetype: str) -> dict[str, object]:
        """The smallest spec the schema accepts, parameterised by archetype."""

        return {
            "spec_version": 1,
            "archetype": archetype,
            "source": {"server": "seed:linear", "tool": "list_issues"},
            "title_path": "title",
        }


class TestLicensedSet(LicensedArchetypeMixin):
    def test_matches_the_shared_constant_in_order(self) -> None:
        # Order is part of the shared file: it keeps the generator's prompt
        # byte-stable between runs.
        assert self.licensed_values() == list(IMPLEMENTED_SURFACE_ARCHETYPES)

    def test_is_a_non_empty_strict_subset_of_the_contract_vocabulary(self) -> None:
        licensed = set(IMPLEMENTED_SURFACE_ARCHETYPES)
        vocabulary = set(SURFACE_ARCHETYPES)

        assert licensed
        assert licensed < vocabulary

    def test_every_licensed_name_is_a_contract_enum_member(self) -> None:
        assert {member.value for member in SurfaceArchetype.implemented()} <= {
            member.value for member in SurfaceArchetype
        }

    def test_never_licenses_form(self) -> None:
        # Model-authored write forms are a deliberate non-goal — SurfaceSpec is
        # read-only by design — but the contract must still accept a replayed
        # spec that names one.
        assert "form" in SURFACE_ARCHETYPES
        assert "form" not in IMPLEMENTED_SURFACE_ARCHETYPES


class TestValidationStaysWider(LicensedArchetypeMixin):
    def test_unlicensed_archetypes_still_validate(self) -> None:
        # Replay safety: the five renderer-less members stay in the enum and a
        # spec carrying one is accepted, not an error. The renderer degrades.
        unlicensed = self.unlicensed_values()
        assert unlicensed, "expected the contract to outrun the renderers"

        for name in unlicensed:
            spec = validate_surface_spec(self.spec_with(name))

            assert spec.archetype.value == name

    def test_licensed_archetypes_validate_too(self) -> None:
        for name in IMPLEMENTED_SURFACE_ARCHETYPES:
            assert validate_surface_spec(self.spec_with(name)).archetype.value == name


class TestDriftFailsLoudly(LicensedArchetypeMixin):
    def test_a_licence_outside_the_vocabulary_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the shared file ever names an archetype the schema would reject,
        # the licence must fail here rather than send the model after a value
        # its own output could never validate as.
        monkeypatch.setattr(
            spec_models, self.SHARED_CONSTANT, ("record", "hologram"), raising=True
        )

        with pytest.raises(ValueError, match="hologram"):
            SurfaceArchetype.implemented()

    def test_an_empty_licence_is_reported_as_empty_not_as_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A renderer-less client licenses nothing. The failure mode to avoid is
        # an empty set quietly meaning "no restriction".
        monkeypatch.setattr(spec_models, self.SHARED_CONSTANT, (), raising=True)

        assert SurfaceArchetype.implemented() == ()
