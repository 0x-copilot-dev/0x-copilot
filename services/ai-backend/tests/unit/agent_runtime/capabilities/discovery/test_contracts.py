from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityCatalog,
    CapabilityCatalogGeneration,
    CapabilityCatalogIdentityError,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
    CapabilityRefBinding,
    CatalogDescriptorRevision,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_REFERENCE_KEY = b"catalog-test-reference-key-32-bytes!!"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64
_SELECTION_REF = f"task-policy-selection://run_1/public.research/sha256/{'c' * 64}"
_SCOPE_REVISION = "scope_9"


class CatalogIdentityMixin:
    """Deterministic generations, catalogs, and typed-error extraction."""

    @staticmethod
    def generation(
        *,
        subject_fingerprint: str = _SUBJECT,
        connector_scope_revision: str = _SCOPE_REVISION,
        task_policy_selection_ref: str = _SELECTION_REF,
        descriptor_revisions: tuple[CatalogDescriptorRevision, ...] = (),
    ) -> CapabilityCatalogGeneration:
        return CapabilityCatalogGeneration.create(
            subject_fingerprint=subject_fingerprint,
            connector_scope_revision=connector_scope_revision,
            task_policy_selection_ref=task_policy_selection_ref,
            descriptor_revisions=descriptor_revisions,
        )

    @staticmethod
    def descriptor_revisions() -> tuple[CatalogDescriptorRevision, ...]:
        return (
            CatalogDescriptorRevision(source_id="drive", descriptor_revision="rev-a"),
            CatalogDescriptorRevision(
                source_id="calendar", descriptor_revision="rev-b"
            ),
        )

    @staticmethod
    def context(*, run_id: str = "run_1") -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_1",
            org_id="org_1",
            roles={"member"},
            permission_scopes={"docs:read"},
            connector_scopes={"drive": frozenset({"docs:read"})},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=32_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=run_id,
        )

    @classmethod
    def catalog(
        cls,
        *,
        run_id: str = "run_1",
        generation: CapabilityCatalogGeneration | None = None,
    ) -> CapabilityCatalog:
        context = cls.context(run_id=run_id)
        scope = CapabilityCatalogScope.from_context(
            context,
            profile_id="research",
            policy_revision="policy_7",
            connector_scope_revision=_SCOPE_REVISION,
        )
        projected = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=scope,
            task_policy_selection_ref=_SELECTION_REF,
            mcp_server_cards=(
                McpServerCard(
                    name="drive_server",
                    display_name="Drive Server",
                    short_description="Find relevant drive records.",
                    transport=McpTransport.HTTP,
                    auth_mode=McpAuthMode.OAUTH2,
                    required_scopes=frozenset({"docs:read"}),
                    health=McpServerHealth.HEALTHY,
                    load_cost=2,
                    connector_slug="drive",
                ),
            ),
            expires_at=_NOW + timedelta(minutes=15),
        )
        if generation is None:
            return projected
        return CapabilityCatalog(
            scope=projected.scope,
            revision=CapabilityCatalogRevision(
                **projected.revision.model_dump(exclude={"generation"}),
                generation=generation,
            ),
            entries=projected.entries,
        )

    @classmethod
    def ungenerated_catalog(cls) -> CapabilityCatalog:
        """Build the generation-less catalog only a direct construction allows."""

        projected = cls.catalog()
        return CapabilityCatalog(
            scope=projected.scope,
            revision=CapabilityCatalogRevision(
                **projected.revision.model_dump(exclude={"generation"}),
            ),
            entries=projected.entries,
        )

    @staticmethod
    def raised_error(exc_info: pytest.ExceptionInfo[ValidationError]) -> object:
        return exc_info.value.errors()[0].get("ctx", {}).get("error")


class TestCapabilityCatalogGeneration(CatalogIdentityMixin):
    def test_identity_is_reproducible_for_identical_inputs(self) -> None:
        first = self.generation(descriptor_revisions=self.descriptor_revisions())
        second = self.generation(descriptor_revisions=self.descriptor_revisions())

        assert first == second
        assert first.generation_digest == second.generation_digest
        assert first.is_same_generation(second)

    def test_descriptor_revision_folding_is_order_insensitive(self) -> None:
        revisions = self.descriptor_revisions()

        first = self.generation(descriptor_revisions=revisions)
        second = self.generation(descriptor_revisions=tuple(reversed(revisions)))

        assert first.generation_digest == second.generation_digest
        assert first.descriptor_revision_count == 2

    def test_identity_changes_when_the_subject_changes(self) -> None:
        baseline = self.generation()

        changed = self.generation(subject_fingerprint=_OTHER_SUBJECT)

        assert not baseline.is_same_generation(changed)

    def test_identity_changes_when_the_connector_scope_changes(self) -> None:
        baseline = self.generation()

        changed = self.generation(connector_scope_revision="scope_10")

        assert not baseline.is_same_generation(changed)

    def test_identity_changes_when_the_task_policy_selection_changes(self) -> None:
        baseline = self.generation()

        changed = self.generation(
            task_policy_selection_ref=(
                f"task-policy-selection://run_1/effect.proposal/sha256/{'d' * 64}"
            )
        )

        assert not baseline.is_same_generation(changed)

    def test_identity_changes_when_a_descriptor_revision_changes(self) -> None:
        baseline = self.generation(descriptor_revisions=self.descriptor_revisions())

        changed = self.generation(
            descriptor_revisions=(
                CatalogDescriptorRevision(
                    source_id="drive",
                    descriptor_revision="rev-a2",
                ),
                CatalogDescriptorRevision(
                    source_id="calendar",
                    descriptor_revision="rev-b",
                ),
            )
        )

        assert not baseline.is_same_generation(changed)

    def test_identity_changes_when_a_descriptor_source_is_added(self) -> None:
        baseline = self.generation(descriptor_revisions=self.descriptor_revisions())

        changed = self.generation(
            descriptor_revisions=(
                *self.descriptor_revisions(),
                CatalogDescriptorRevision(
                    source_id="mail",
                    descriptor_revision="rev-c",
                ),
            )
        )

        assert not baseline.is_same_generation(changed)
        assert changed.descriptor_revision_count == 3

    def test_conflicting_descriptor_revisions_fail_closed(self) -> None:
        with pytest.raises(CapabilityCatalogIdentityError, match="two descriptor"):
            self.generation(
                descriptor_revisions=(
                    CatalogDescriptorRevision(
                        source_id="drive",
                        descriptor_revision="rev-a",
                    ),
                    CatalogDescriptorRevision(
                        source_id="drive",
                        descriptor_revision="rev-a2",
                    ),
                )
            )

    def test_repeated_identical_descriptor_revisions_collapse(self) -> None:
        generation = self.generation(
            descriptor_revisions=(
                CatalogDescriptorRevision(
                    source_id="drive",
                    descriptor_revision="rev-a",
                ),
                CatalogDescriptorRevision(
                    source_id="drive",
                    descriptor_revision="rev-a",
                ),
            )
        )

        assert generation.descriptor_revision_count == 1

    def test_padded_key_values_are_rejected_rather_than_normalized(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self.generation(connector_scope_revision=" scope_9 ")

        assert isinstance(self.raised_error(exc_info), CapabilityCatalogIdentityError)

    def test_padded_descriptor_revision_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CatalogDescriptorRevision(source_id="drive ", descriptor_revision="rev-a")

        assert isinstance(self.raised_error(exc_info), CapabilityCatalogIdentityError)

    def test_a_tampered_digest_is_rejected_at_construction(self) -> None:
        baseline = self.generation()

        with pytest.raises(ValidationError) as exc_info:
            CapabilityCatalogGeneration(
                **baseline.model_dump(exclude={"generation_digest"}),
                generation_digest="f" * 64,
            )

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityCatalogIdentityError)
        assert str(error) == (
            "catalog generation digest does not match its canonical identity"
        )

    def test_verify_rejects_an_unvalidated_tampered_generation(self) -> None:
        baseline = self.generation()
        forged = CapabilityCatalogGeneration.model_construct(
            **baseline.model_dump(
                exclude={"connector_scope_revision", "generation_digest"}
            ),
            connector_scope_revision="scope_10",
            generation_digest=baseline.generation_digest,
        )

        with pytest.raises(CapabilityCatalogIdentityError, match="does not match"):
            forged.verify()

    def test_generation_ref_discloses_no_keyed_input(self) -> None:
        generation = self.generation(descriptor_revisions=self.descriptor_revisions())

        reference = generation.generation_ref

        assert reference.startswith("capability-catalog-generation://sha256/")
        assert _SUBJECT not in reference
        assert _SCOPE_REVISION not in reference
        assert _SELECTION_REF not in reference

    def test_generation_carries_identifiers_revisions_digests_and_counts_only(
        self,
    ) -> None:
        generation = self.generation(descriptor_revisions=self.descriptor_revisions())

        assert set(generation.model_dump()) == {
            "schema_version",
            "subject_fingerprint",
            "connector_scope_revision",
            "task_policy_selection_ref",
            "descriptor_revision_digest",
            "descriptor_revision_count",
            "generation_digest",
        }
        assert "rev-a" not in generation.model_dump_json()

    def test_generation_is_immutable(self) -> None:
        generation = self.generation()

        with pytest.raises(ValidationError):
            generation.connector_scope_revision = "scope_10"


class TestCapabilityCatalogRevisionGeneration(CatalogIdentityMixin):
    def test_the_shipped_builder_always_stamps_a_generation(self) -> None:
        catalog = self.catalog()

        assert catalog.generation is not None

    def test_the_contract_still_permits_a_generationless_catalog(self) -> None:
        """The field stays optional so an unstamped catalog fails closed."""

        catalog = self.ungenerated_catalog()

        assert catalog.generation is None

    def test_a_generation_binds_onto_the_catalog_revision(self) -> None:
        generation = self.generation()

        catalog = self.catalog(generation=generation)

        assert catalog.generation is not None
        assert catalog.generation.is_same_generation(generation)

    def test_generation_connector_scope_drift_fails_closed(self) -> None:
        drifted = self.generation(connector_scope_revision="scope_10")

        with pytest.raises(ValidationError) as exc_info:
            self.catalog(generation=drifted)

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityCatalogIdentityError)
        assert str(error) == (
            "catalog generation connector scope does not match the catalog revision"
        )


class TestCapabilityRefBinding(CatalogIdentityMixin):
    def test_a_member_ref_binds_to_the_issuing_generation(self) -> None:
        generation = self.generation()
        catalog = self.catalog(generation=generation)

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert binding.capability_ref == catalog.entries[0].capability_ref
        assert binding.catalog_id == catalog.revision.catalog_id
        assert binding.catalog_revision == catalog.revision.revision
        assert binding.issued_generation == generation

    def test_a_binding_answers_no_currency_question_of_its_own(self) -> None:
        """Use-time currency belongs to the shared Step RB primitive alone."""

        catalog = self.catalog(generation=self.generation())

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert not hasattr(binding, "is_bound_to")
        assert not any(
            "stale" in name or "current" in name for name in dir(type(binding))
        )

    def test_refs_from_different_generations_are_distinguishable(self) -> None:
        first_generation = self.generation()
        second_generation = self.generation(
            descriptor_revisions=self.descriptor_revisions()
        )
        catalog = self.catalog(generation=first_generation)
        other = self.catalog(generation=second_generation)

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        other_binding = other.bind_ref(other.entries[0].capability_ref)

        assert binding.capability_ref == other_binding.capability_ref
        assert not binding.issued_generation.is_same_generation(second_generation)
        assert not other_binding.issued_generation.is_same_generation(first_generation)
        assert binding.binding_digest != other_binding.binding_digest

    def test_binding_is_reproducible_for_identical_inputs(self) -> None:
        generation = self.generation()
        catalog = self.catalog(generation=generation)

        first = catalog.bind_ref(catalog.entries[0].capability_ref)
        second = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert first == second
        assert first.binding_ref == second.binding_ref

    def test_a_catalog_without_a_generation_cannot_bind(self) -> None:
        catalog = self.ungenerated_catalog()

        with pytest.raises(CapabilityCatalogIdentityError, match="no generation"):
            catalog.bind_ref(catalog.entries[0].capability_ref)

    def test_a_non_member_ref_cannot_be_bound(self) -> None:
        catalog = self.catalog(generation=self.generation())

        with pytest.raises(CapabilityCatalogIdentityError, match="not a member"):
            catalog.bind_ref(f"cap_{'0' * 32}")

    def test_a_tampered_binding_digest_is_rejected(self) -> None:
        generation = self.generation()
        catalog = self.catalog(generation=generation)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        with pytest.raises(ValidationError) as exc_info:
            CapabilityRefBinding(
                **binding.model_dump(exclude={"binding_digest"}),
                binding_digest="f" * 64,
            )

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityCatalogIdentityError)
        assert str(error) == (
            "capability ref binding digest does not match its identity"
        )

    def test_a_replayed_binding_cannot_swap_its_generation(self) -> None:
        catalog = self.catalog(generation=self.generation())
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        replacement = self.generation(subject_fingerprint=_OTHER_SUBJECT)

        with pytest.raises(ValidationError) as exc_info:
            CapabilityRefBinding(
                **binding.model_dump(
                    exclude={"issued_generation", "binding_digest"},
                ),
                issued_generation=replacement,
                binding_digest=binding.binding_digest,
            )

        assert isinstance(self.raised_error(exc_info), CapabilityCatalogIdentityError)

    def test_binding_ref_discloses_no_capability_identity(self) -> None:
        catalog = self.catalog(generation=self.generation())
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert binding.binding_ref.startswith("capability-ref-binding://sha256/")
        assert binding.capability_ref not in binding.binding_ref
        assert "drive_search" not in binding.binding_ref

    def test_binding_carries_identifiers_revisions_and_digests_only(self) -> None:
        catalog = self.catalog(generation=self.generation())

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)

        assert set(binding.model_dump()) == {
            "schema_version",
            "capability_ref",
            "catalog_id",
            "catalog_revision",
            "issued_generation",
            "binding_digest",
        }
        assert "drive_search" not in binding.model_dump_json()
