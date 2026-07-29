"""F3.4 — an over-bound capability schema becomes a protected artifact ref.

The property under test is not "describe can return a reference".  It is that
describe can never return a *partial* schema: a capability whose parameters do
not fit the inline bound is either published whole and referenced, or reported
unavailable, and the reference it is published under is worthless to any other
run, subject, or catalog generation.

These tests deliberately compose the *real* offload writer over the *real*
content-addressed object store for the round-trip, and the *real* Step RB
revalidator for every scoping check.  A fake store would prove nothing about
reuse, and a hand-rolled staleness check would prove nothing about RB.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityCatalog,
    CapabilityCatalogAccess,
    CapabilityCatalogRevisionAuthority,
    CapabilityCatalogScope,
    CapabilityDescribeTool,
    CapabilityIndexEntry,
    CapabilityInvokeRequest,
    CapabilityParameterHint,
    CapabilityRefRevalidation,
    CapabilityRefRevisionBinding,
    CapabilitySchemaArtifactRef,
    CapabilitySchemaArtifactResolver,
    CapabilitySchemaAvailability,
    CapabilitySchemaBounds,
    CapabilitySchemaDocument,
    CapabilityDescription,
    CapabilitySearchRequest,
    CapabilityDescribeRequest,
    HmacCapabilityReferenceMinter,
    RunScopedSchemaArtifactPublisher,
)
from agent_runtime.capabilities.discovery.executor import (
    CapabilityArgumentSchemaCheck,
)
from agent_runtime.capabilities.discovery.tool_bridge import (
    CapabilityExecutionRefused,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.revision_binding import RevisionBindingRevalidator
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_adapters.file.offload import FileOffloadWriter
from tests.unit.agent_runtime.capabilities.discovery.test_revision_authority import (
    InMemoryCatalogGenerationSource,
)

_NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
_REFERENCE_KEY = b"f3-schema-artifact-reference-key-32-bytes!!"
_SELECTION_REF = f"task-policy-selection://run_1/default/sha256/{'a' * 64}"
_OVER_BOUND = CapabilitySchemaBounds.MAX_PARAMETERS + 1


class RecordingWriter:
    """An offload writer that remembers what it was asked to park."""

    def __init__(self, *, fail: bool = False, locator: str | None = None) -> None:
        self.fail = fail
        self.locator = locator
        self.contents: list[str] = []

    def __call__(self, content: str) -> str:
        if self.fail:
            raise RuntimeError("object store unavailable")
        self.contents.append(content)
        if self.locator is not None:
            return self.locator
        return f"/large_tool_results/{len(self.contents):064d}"


class DiscoveryFixtureMixin:
    """Build real catalogs, real bindings, and a real RB revalidation path."""

    @staticmethod
    def context(
        *,
        run_id: str = "run_1",
        user_id: str = "user_1",
        org_id: str = "org_1",
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=user_id,
            org_id=org_id,
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

    @staticmethod
    def card() -> McpServerCard:
        return McpServerCard(
            name="document_search",
            display_name="Document Search",
            short_description="Search authorized documents and return records.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            required_scopes={"docs:read"},
            health=McpServerHealth.HEALTHY,
            load_cost=2,
            connector_slug="drive",
        )

    @classmethod
    def catalog(
        cls,
        context: AgentRuntimeContext,
        *,
        selection_ref: str = _SELECTION_REF,
        parameters: int = _OVER_BOUND,
        name_chars: int = 20,
    ) -> CapabilityCatalog:
        built = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id="default",
                policy_revision="policy_1",
                connector_scope_revision="scope_1",
            ),
            task_policy_selection_ref=selection_ref,
            mcp_server_cards=(cls.card(),),
            expires_at=_NOW + timedelta(minutes=15),
        )
        base = built.entries[0]
        entry = CapabilityIndexEntry(
            **base.model_dump(exclude={"parameter_names", "parameter_types"}),
            parameter_names=tuple(
                f"p{index:03d}".ljust(name_chars, "n") for index in range(parameters)
            ),
            parameter_types=tuple(f"t{index:03d}" for index in range(parameters)),
        )
        return CapabilityCatalog(
            scope=built.scope,
            revision=built.revision,
            entries=(entry,),
        )

    @staticmethod
    def access(
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
    ) -> CapabilityCatalogAccess:
        return CapabilityCatalogAccess(
            catalog=catalog,
            runtime_context=context,
            clock=lambda: _NOW,
        )

    @staticmethod
    def minter() -> HmacCapabilityReferenceMinter:
        return HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY)

    @classmethod
    def publisher(
        cls,
        writer: object | None = None,
    ) -> RunScopedSchemaArtifactPublisher:
        return RunScopedSchemaArtifactPublisher(
            writer=writer or RecordingWriter(),  # type: ignore[arg-type]
            minter=cls.minter(),
        )

    @classmethod
    def revalidation(
        cls,
        context: AgentRuntimeContext,
        *,
        held: CapabilityCatalog,
        live: CapabilityCatalog | None = None,
        source: InMemoryCatalogGenerationSource | None = None,
    ) -> tuple[CapabilityRefRevalidation, InMemoryCatalogGenerationSource]:
        """Wire the real Step RB revalidator over an injectable live authority."""

        published = live or held
        held_generation = held.generation
        live_generation = published.generation
        assert held_generation is not None
        assert live_generation is not None
        generation_source = source or InMemoryCatalogGenerationSource()
        generation_source.publish(
            CapabilityRefRevisionBinding.scope_for(
                held_generation,
                run_id=context.run_id,
            ),
            live_generation,
        )
        return (
            CapabilityRefRevalidation(
                revalidator=RevisionBindingRevalidator(
                    CapabilityCatalogRevisionAuthority(generation_source)
                ),
                subject_fingerprint=AuthorizedCatalogBuilder(
                    reference_key=_REFERENCE_KEY
                ).subject_fingerprint(context),
            ),
            generation_source,
        )

    @classmethod
    def describe(
        cls,
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
        publisher: RunScopedSchemaArtifactPublisher | None,
    ) -> dict[str, object]:
        tool = CapabilityDescribeTool(
            access=cls.access(catalog, context),
            schema_artifacts=publisher,
        )
        return tool.invoke(catalog.entries[0].capability_ref)


class TestOverBoundSchemaBecomesAnArtifact(DiscoveryFixtureMixin):
    def test_an_over_bound_schema_yields_a_ref_and_never_a_partial_schema(
        self,
    ) -> None:
        context = self.context()
        catalog = self.catalog(context)
        entry = catalog.entries[0]
        publisher = self.publisher()

        result = self.describe(catalog, context, publisher)

        description = result["description"]["capability"]
        assert description["schema_availability"] == "artifact"
        assert description["parameters"] == []
        artifact = description["schema_artifact"]
        assert artifact["artifact_ref"].startswith("sch_")
        assert artifact["parameter_count"] == _OVER_BOUND
        # The decisive assertion: not one parameter name leaked into the answer,
        # so there is no prefix the model could mistake for the whole schema.
        encoded = json.dumps(result, sort_keys=True)
        assert not any(name in encoded for name in entry.parameter_names)

    def test_the_published_document_carries_every_parameter(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        entry = catalog.entries[0]
        writer = RecordingWriter()

        self.describe(catalog, context, self.publisher(writer))

        assert len(writer.contents) == 1
        document = json.loads(writer.contents[0])
        assert tuple(document["parameter_names"]) == entry.parameter_names
        assert tuple(document["parameter_types"]) == entry.parameter_types
        assert len(document["parameter_names"]) == _OVER_BOUND

    def test_a_schema_within_the_bound_never_publishes_anything(self) -> None:
        context = self.context()
        catalog = self.catalog(
            context, parameters=CapabilitySchemaBounds.MAX_PARAMETERS
        )
        writer = RecordingWriter()

        result = self.describe(catalog, context, self.publisher(writer))

        description = result["description"]["capability"]
        assert description["schema_availability"] == "inline"
        assert len(description["parameters"]) == CapabilitySchemaBounds.MAX_PARAMETERS
        assert writer.contents == []

    def test_an_over_wide_parameter_name_defers_even_within_the_count(self) -> None:
        context = self.context()
        catalog = self.catalog(
            context,
            parameters=2,
            name_chars=CapabilitySchemaBounds.MAX_PARAMETER_CHARS + 1,
        )

        result = self.describe(catalog, context, self.publisher())

        description = result["description"]["capability"]
        # The count fits but a value does not, so the schema still cannot be
        # inlined whole -- and is therefore not inlined at all.
        assert description["schema_availability"] == "artifact"
        assert description["parameters"] == []

    def test_a_failed_publish_reports_unavailable_rather_than_truncating(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        result = self.describe(
            catalog,
            context,
            self.publisher(RecordingWriter(fail=True)),
        )

        description = result["description"]["capability"]
        assert description["schema_availability"] == "unavailable"
        assert description["parameters"] == []
        assert "schema_artifact" not in description

    def test_no_publisher_reports_unavailable_rather_than_truncating(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        result = self.describe(catalog, context, None)

        description = result["description"]["capability"]
        assert description["schema_availability"] == "unavailable"
        assert description["parameters"] == []

    def test_describing_twice_mints_one_stable_reference(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()

        first = self.describe(catalog, context, publisher)
        second = self.describe(catalog, context, publisher)

        assert first == second


class TestTheInlineBoundIsStructural(DiscoveryFixtureMixin):
    def test_a_description_cannot_hold_more_parameters_than_the_bound(self) -> None:
        hints = tuple(
            CapabilityParameterHint(name=f"p{index:03d}")
            for index in range(CapabilitySchemaBounds.MAX_PARAMETERS + 1)
        )

        with pytest.raises(ValueError):
            CapabilityDescription(
                capability_ref=f"cap_{'0' * 32}",
                stable_name="doc_search",
                display_name="Doc Search",
                concise_description="Search documents.",
                source="mcp_server",
                parameters=hints,
                effect_class="unknown",
                approval_cue="unknown",
                connector_label="drive",
            )

    @pytest.mark.parametrize(
        "availability",
        [
            CapabilitySchemaAvailability.ARTIFACT,
            CapabilitySchemaAvailability.UNAVAILABLE,
        ],
    )
    def test_a_deferred_schema_can_never_also_carry_hints(
        self,
        availability: CapabilitySchemaAvailability,
    ) -> None:
        with pytest.raises(ValueError, match="inlined schema may carry parameter"):
            CapabilityDescription(
                capability_ref=f"cap_{'0' * 32}",
                stable_name="doc_search",
                display_name="Doc Search",
                concise_description="Search documents.",
                source="mcp_server",
                parameters=(CapabilityParameterHint(name="query"),),
                effect_class="unknown",
                approval_cue="unknown",
                connector_label="drive",
                schema_availability=availability,
                schema_artifact=(
                    CapabilitySchemaArtifactRef(artifact_ref=f"sch_{'0' * 32}")
                    if availability is CapabilitySchemaAvailability.ARTIFACT
                    else None
                ),
            )

    def test_an_artifact_answer_without_a_reference_is_unrepresentable(self) -> None:
        with pytest.raises(ValueError, match="deferred schema must carry"):
            CapabilityDescription(
                capability_ref=f"cap_{'0' * 32}",
                stable_name="doc_search",
                display_name="Doc Search",
                concise_description="Search documents.",
                source="mcp_server",
                effect_class="unknown",
                approval_cue="unknown",
                connector_label="drive",
                schema_availability=CapabilitySchemaAvailability.ARTIFACT,
            )

    def test_an_inline_answer_can_never_smuggle_a_reference(self) -> None:
        with pytest.raises(ValueError, match="only a deferred schema"):
            CapabilityDescription(
                capability_ref=f"cap_{'0' * 32}",
                stable_name="doc_search",
                display_name="Doc Search",
                concise_description="Search documents.",
                source="mcp_server",
                effect_class="unknown",
                approval_cue="unknown",
                connector_label="drive",
                schema_artifact=CapabilitySchemaArtifactRef(
                    artifact_ref=f"sch_{'0' * 32}"
                ),
            )

    def test_a_published_document_may_never_drop_a_parameter(self) -> None:
        context = self.context()
        entry = self.catalog(context).entries[0]

        document = CapabilitySchemaDocument.for_entry(entry)

        assert document.parameter_count == len(entry.parameter_names)
        assert document.parameter_names == entry.parameter_names


class TestTheReferenceIsProtected(DiscoveryFixtureMixin):
    def _resolver(
        self,
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
        publisher: RunScopedSchemaArtifactPublisher,
        *,
        live: CapabilityCatalog | None = None,
        revalidation: CapabilityRefRevalidation | None = None,
    ) -> CapabilitySchemaArtifactResolver:
        if revalidation is None:
            revalidation, _ = self.revalidation(context, held=catalog, live=live)
        return CapabilitySchemaArtifactResolver(
            scope=self.access(catalog, context),
            publisher=publisher,
            revalidation=revalidation,
        )

    @pytest.mark.asyncio
    async def test_the_owning_run_resolves_to_the_stored_locator(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        writer = RecordingWriter(locator="/large_tool_results/" + "b" * 64)
        publisher = self.publisher(writer)
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        release = await self._resolver(catalog, context, publisher).resolve(
            artifact_ref
        )

        assert release is not None
        assert release.locator == "/large_tool_results/" + "b" * 64
        assert release.capability_ref == catalog.entries[0].capability_ref
        assert release.parameter_count == _OVER_BOUND

    @pytest.mark.asyncio
    async def test_another_run_cannot_resolve_the_same_reference(self) -> None:
        owner = self.context(run_id="run_owner")
        other = self.context(run_id="run_other")
        owner_catalog = self.catalog(owner)
        other_catalog = self.catalog(other)
        # Worst case on purpose: both runs are handed the *same* publisher, so
        # the ledger cannot be what refuses. Only the scoping can.
        publisher = self.publisher()
        result = self.describe(owner_catalog, owner, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        assert (
            await self._resolver(owner_catalog, owner, publisher).resolve(artifact_ref)
        ) is not None
        assert (
            await self._resolver(other_catalog, other, publisher).resolve(artifact_ref)
        ) is None

    @pytest.mark.asyncio
    async def test_another_subject_cannot_resolve_the_same_reference(self) -> None:
        owner = self.context(user_id="user_owner")
        other = self.context(user_id="user_other")
        owner_catalog = self.catalog(owner)
        other_catalog = self.catalog(other)
        publisher = self.publisher()
        result = self.describe(owner_catalog, owner, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        # Positive control: the owning subject still resolves, so the refusal
        # below is scoping rather than a resolver that never releases anything.
        assert (
            await self._resolver(owner_catalog, owner, publisher).resolve(artifact_ref)
        ) is not None
        assert (
            await self._resolver(other_catalog, other, publisher).resolve(artifact_ref)
        ) is None

    @pytest.mark.asyncio
    async def test_another_org_cannot_resolve_the_same_reference(self) -> None:
        owner = self.context(org_id="org_owner")
        other = self.context(org_id="org_other")
        owner_catalog = self.catalog(owner)
        other_catalog = self.catalog(other)
        publisher = self.publisher()
        result = self.describe(owner_catalog, owner, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        assert (
            await self._resolver(owner_catalog, owner, publisher).resolve(artifact_ref)
        ) is not None
        assert (
            await self._resolver(other_catalog, other, publisher).resolve(artifact_ref)
        ) is None

    @pytest.mark.asyncio
    async def test_a_reference_minted_under_another_generation_is_refused(self) -> None:
        """Same run, same subject, same catalog id -- only the generation moved."""

        context = self.context()
        issued = self.catalog(context, selection_ref=_SELECTION_REF)
        moved = self.catalog(
            context,
            selection_ref=f"task-policy-selection://run_1/default/sha256/{'b' * 64}",
        )
        assert issued.revision.catalog_id == moved.revision.catalog_id
        assert issued.entries[0].capability_ref == moved.entries[0].capability_ref
        publisher = self.publisher()
        result = self.describe(issued, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        # The run now holds the moved catalog; the reference was minted under the
        # issued one, so the binding it re-derives is a different binding.
        assert (
            await self._resolver(issued, context, publisher).resolve(artifact_ref)
        ) is not None
        assert (
            await self._resolver(moved, context, publisher).resolve(artifact_ref)
        ) is None

    @pytest.mark.asyncio
    async def test_a_superseded_live_generation_is_refused_by_the_shared_primitive(
        self,
    ) -> None:
        """The binding matches; only the *authority* disagrees."""

        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]
        superseded = self.catalog(
            context,
            selection_ref=f"task-policy-selection://run_1/default/sha256/{'c' * 64}",
        )
        revalidation, _ = self.revalidation(
            context,
            held=catalog,
            live=superseded,
        )

        release = await self._resolver(
            catalog,
            context,
            publisher,
            revalidation=revalidation,
        ).resolve(artifact_ref)

        assert release is None

    @pytest.mark.asyncio
    async def test_a_revoked_scope_is_refused(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]
        revalidation, source = self.revalidation(context, held=catalog)
        generation = catalog.generation
        assert generation is not None
        source.revoke(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=context.run_id)
        )

        assert (
            await self._resolver(
                catalog,
                context,
                publisher,
                revalidation=revalidation,
            ).resolve(artifact_ref)
        ) is None

    @pytest.mark.asyncio
    async def test_a_missing_revalidation_seam_refuses_rather_than_releasing(
        self,
    ) -> None:
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]

        resolver = CapabilitySchemaArtifactResolver(
            scope=self.access(catalog, context),
            publisher=publisher,
            revalidation=None,
        )

        assert await resolver.resolve(artifact_ref) is None

    @pytest.mark.asyncio
    async def test_an_expired_catalog_releases_nothing(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]
        revalidation, _ = self.revalidation(context, held=catalog)
        expired = CapabilityCatalogAccess(
            catalog=catalog,
            runtime_context=context,
            clock=lambda: _NOW + timedelta(hours=2),
        )

        resolver = CapabilitySchemaArtifactResolver(
            scope=expired,
            publisher=publisher,
            revalidation=revalidation,
        )

        assert await resolver.resolve(artifact_ref) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("forged", [f"sch_{'0' * 32}", "cap_" + "0" * 32, ""])
    async def test_a_reference_that_was_never_published_releases_nothing(
        self,
        forged: str,
    ) -> None:
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher()
        self.describe(catalog, context, publisher)

        assert await self._resolver(catalog, context, publisher).resolve(forged) is None

    def test_the_model_visible_reference_never_carries_the_locator(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        writer = RecordingWriter(locator="/large_tool_results/" + "d" * 64)

        result = self.describe(catalog, context, self.publisher(writer))

        encoded = json.dumps(result, sort_keys=True)
        assert "large_tool_results" not in encoded
        assert "d" * 64 not in encoded
        assert set(result["description"]["capability"]["schema_artifact"]) == {
            "artifact_ref",
            "parameter_count",
        }


class TestTheExistingContentAddressedStoreIsReused(DiscoveryFixtureMixin):
    def test_the_document_round_trips_through_the_real_object_store(
        self,
        tmp_path: Path,
    ) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        store = FileObjectStore(layout)
        context = self.context()
        catalog = self.catalog(context)
        entry = catalog.entries[0]
        publisher = self.publisher(FileOffloadWriter(store))

        self.describe(catalog, context, publisher)

        # Exactly one blob, addressed by the existing CAS layout, readable back
        # through the existing read path -- no second store was introduced.
        digests = store.iter_digests()
        assert len(digests) == 1
        stored = json.loads(store.get(digests[0]).decode("utf-8"))
        assert tuple(stored["parameter_names"]) == entry.parameter_names
        assert stored["capability_ref"] == entry.capability_ref

    @pytest.mark.asyncio
    async def test_the_released_locator_reads_back_through_the_existing_path(
        self,
        tmp_path: Path,
    ) -> None:
        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        store = FileObjectStore(layout)
        context = self.context()
        catalog = self.catalog(context)
        publisher = self.publisher(FileOffloadWriter(store))
        result = self.describe(catalog, context, publisher)
        artifact_ref = result["description"]["capability"]["schema_artifact"][
            "artifact_ref"
        ]
        revalidation, _ = self.revalidation(context, held=catalog)
        resolver = CapabilitySchemaArtifactResolver(
            scope=self.access(catalog, context),
            publisher=publisher,
            revalidation=revalidation,
        )

        release = await resolver.resolve(artifact_ref)

        assert release is not None
        assert release.locator.startswith("/large_tool_results/")
        sha = release.locator.removeprefix("/large_tool_results/")
        document = json.loads(store.get(sha).decode("utf-8"))
        assert len(document["parameter_names"]) == _OVER_BOUND
        assert (
            CapabilitySchemaDocument.model_validate(document).content_digest
            == release.content_digest
        )


class TestInvokeStillEnforcesTheRealSchema(DiscoveryFixtureMixin):
    """A deferred schema changes what describe *shows*, never what invoke checks."""

    @staticmethod
    def _real_schema() -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["p000nnnnnnnnnnnnnnnn"],
            "properties": {
                f"p{index:03d}".ljust(20, "n"): {"type": "string"}
                for index in range(_OVER_BOUND)
            },
        }

    def test_the_over_bound_capability_is_described_by_reference(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        result = self.describe(catalog, context, self.publisher())

        assert result["description"]["capability"]["schema_availability"] == "artifact"

    def test_arguments_missing_a_required_property_are_still_refused(self) -> None:
        with pytest.raises(CapabilityExecutionRefused) as refusal:
            CapabilityArgumentSchemaCheck.enforce(
                arguments={},
                schema=self._real_schema(),
            )

        assert refusal.value.code == "invalid_request"

    def test_arguments_the_real_schema_does_not_declare_are_still_refused(self) -> None:
        with pytest.raises(CapabilityExecutionRefused) as refusal:
            CapabilityArgumentSchemaCheck.enforce(
                arguments={"p000nnnnnnnnnnnnnnnn": "x", "undeclared": "y"},
                schema=self._real_schema(),
            )

        assert refusal.value.code == "invalid_request"

    def test_arguments_the_real_schema_declares_are_admitted(self) -> None:
        CapabilityArgumentSchemaCheck.enforce(
            arguments={"p000nnnnnnnnnnnnnnnn": "x"},
            schema=self._real_schema(),
        )

    def test_the_executor_validates_unconditionally_and_knows_no_artifact(self) -> None:
        """Structural: no branch in the executor can skip the schema check."""

        source = Path("src/agent_runtime/capabilities/discovery/executor.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        execute = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
        )
        enforcements = [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "enforce"
        ]
        assert len(enforcements) == 1
        # The one call sits directly in the method body, not inside any branch.
        assert any(
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "enforce"
            for statement in execute.body
        )
        # The executor cannot consult the schema-representation vocabulary,
        # because it never mentions it.
        text = source.read_text(encoding="utf-8")
        assert "schema_availability" not in text
        assert "schema_artifact" not in text


class TestTheModelFacingToolSchemasDidNotGrow(DiscoveryFixtureMixin):
    """Token cost lives on the request side; this lane only touched responses."""

    @pytest.mark.parametrize(
        "args_schema",
        [CapabilitySearchRequest, CapabilityDescribeRequest, CapabilityInvokeRequest],
    )
    def test_no_bridge_argument_schema_mentions_the_artifact_vocabulary(
        self,
        args_schema: type,
    ) -> None:
        encoded = json.dumps(args_schema.model_json_schema(), sort_keys=True)

        assert "schema_artifact" not in encoded
        assert "schema_availability" not in encoded
        assert "artifact_ref" not in encoded

    def test_the_describe_tool_description_is_unchanged_by_this_lane(self) -> None:
        assert CapabilityDescribeTool.description == (
            "Describe one opaque capability reference returned by "
            "search_capabilities. Returns only bounded compact metadata and "
            "parameter hints; it never returns a full schema or invokes a "
            "capability."
        )
