"""Cross-language parity: the pydantic SurfaceSpec vs the JSON Schema SSOT.

AC3 (the load-bearing test): the pydantic model's field set / enums / required
lists must match ``surface_spec.schema.json`` in ``copilot_service_contracts``.
Drift on either side fails CI. This is what keeps the ai-backend model, the
TypeScript types, and the shared schema from disagreeing silently.
"""

from __future__ import annotations

from copilot_service_contracts.surface_spec import (
    SURFACE_ARCHETYPES,
    SURFACE_SPEC_VERSION,
    load_surface_spec_schema,
)

from agent_runtime.capabilities.surfaces.spec_models import (
    ColumnAlign,
    SurfaceArchetype,
    SurfaceFieldFormat,
    SurfaceSpec,
)


class SchemaParityMixin:
    """Resolves the pydantic model_json_schema() against the file schema."""

    @staticmethod
    def file_schema() -> dict[str, object]:
        return load_surface_spec_schema()

    @staticmethod
    def model_schema() -> dict[str, object]:
        return SurfaceSpec.model_json_schema()

    @staticmethod
    def enum_from_defs(schema: dict[str, object], contains: str) -> set[str]:
        """Find the ``$defs`` enum that contains ``contains`` and return its set."""
        defs = schema.get("$defs")
        assert isinstance(defs, dict), "schema has no $defs"
        for entry in defs.values():
            enum = entry.get("enum") if isinstance(entry, dict) else None
            if isinstance(enum, list) and contains in enum:
                return {str(item) for item in enum}
        raise AssertionError(f"no enum in $defs contains {contains!r}")


class TestSchemaParity(SchemaParityMixin):
    def test_top_level_property_names_match(self) -> None:
        file_props = set((self.file_schema().get("properties") or {}).keys())
        model_props = set((self.model_schema().get("properties") or {}).keys())

        assert file_props == model_props

    def test_required_lists_match(self) -> None:
        file_required = set(self.file_schema().get("required") or [])
        model_required = set(self.model_schema().get("required") or [])

        assert file_required == model_required
        # Sanity: the contract's four required members are present.
        assert file_required == {
            "spec_version",
            "archetype",
            "source",
            "title_path",
        }

    def test_archetype_enum_matches(self) -> None:
        file_enum = self.enum_from_defs(self.file_schema(), "record")
        model_enum = {member.value for member in SurfaceArchetype}
        constant_enum = set(SURFACE_ARCHETYPES)

        assert file_enum == model_enum == constant_enum

    def test_archetype_order_is_stable(self) -> None:
        # Ordering is part of the contract (the schema enum + the constant tuple
        # + the StrEnum declaration all list archetypes in the same order).
        file_defs = self.file_schema()["$defs"]
        assert isinstance(file_defs, dict)
        file_order = file_defs["archetype"]["enum"]

        assert list(SURFACE_ARCHETYPES) == file_order
        assert [member.value for member in SurfaceArchetype] == file_order

    def test_format_enum_matches(self) -> None:
        file_enum = self.enum_from_defs(self.file_schema(), "currency")
        model_enum = {member.value for member in SurfaceFieldFormat}

        assert file_enum == model_enum

    def test_align_enum_matches(self) -> None:
        file_enum = self.enum_from_defs(self.file_schema(), "start")
        model_enum = {member.value for member in ColumnAlign}

        assert file_enum == model_enum

    def test_spec_version_const_matches(self) -> None:
        file_const = (self.file_schema()["properties"])["spec_version"]["const"]
        model_const = (self.model_schema()["properties"])["spec_version"]["const"]

        assert file_const == model_const == SURFACE_SPEC_VERSION == 1

    def test_source_required_members_match(self) -> None:
        file_defs = self.file_schema()["$defs"]
        assert isinstance(file_defs, dict)
        file_source_required = set(file_defs["source"]["required"])

        model_source = self.model_schema()["$defs"]["SurfaceSource"]
        model_source_required = set(model_source["required"])

        assert file_source_required == model_source_required == {"server", "tool"}
