"""The Context Occupancy Ledger at the provider-call seam (PRD-05, design §3.1).

``ModelInvocationMiddleware.awrap_model_call`` is the most load-bearing method in
the runtime: it owns replay reconciliation, attempt admission, the journal, and
prompt-cache identity. Adding measurement to it is therefore two claims, and this
file separates them deliberately.

**The measurement is correct.** Occupancy is captured per *attempt* from the
request that attempt actually dispatched, scoped to the window it was sent to,
reconciled against the same ``NormalizedTokenUsage`` the usage lane reads, and
persisted once per ``(model_call_id, attempt_ordinal)``.

**The seam is unchanged.** ``TestTheSeamIsUnchanged`` is the regression net and
matters more than everything above it. A recorder that raises on every method
must not cost a single provider response, and the journal a run produces must be
byte-identical with occupancy wired and unwired — same records, same order, same
digest. If occupancy can change any of that, it does not belong on this path
(§6.4).

Fakes throughout: no network, no live model, no store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Final, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
import pytest

from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import RunControlSnapshot, RunPolicyRevisions
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.control_plane.model_reliability import (
    ModelReliabilityControlSnapshot,
    ModelReliabilityReleaseResolver,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelCapability,
    ModelCredentialAvailability,
    ModelCredentialMode,
    ModelDeploymentCatalog,
    ModelDeploymentDescriptor,
    ModelFallbackPolicy,
    ModelInvocationAuthority,
    ModelInvocationBudget,
    ModelInvocationRequirements,
    ModelInvocationRequirementsSnapshot,
    ModelRouteEntry,
    ModelRoutePlan,
)
from agent_runtime.execution.model_invocation.journal import (
    ModelInvocationWrite,
    SequencedModelInvocationRecord,
)
from agent_runtime.execution.model_invocation.runtime import (
    ModelInvocationMiddleware,
    ModelInvocationRuntimeBinding,
    canonical_model_request_digest,
)
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderFailureAdapterRegistry,
)
from agent_runtime.observability.context_occupancy_recorder import (
    ContextOccupancyRecorder,
    ThirdPartyPromptIndex,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    declare_context_origin,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    DigestTokenCache,
)
from agent_runtime.persistence.records import RuntimeContextGraphScope
from agent_runtime.prompts import (
    PromptAssembler,
    PromptAssemblyContext,
    PromptCacheEligibility,
    PromptCacheFallbackContext,
    PromptCacheFallbackHandoff,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeObservation,
    PromptRuntimeResult,
    PromptSensitivity,
    PromptTrustLabel,
    ProviderCacheRejectionAdapterRegistry,
)


_SHA: Final[str] = "0" * 64
_NOW: Final[datetime] = datetime(2026, 7, 29, tzinfo=timezone.utc)
_WINDOW: Final[int] = 10_000


class LengthCounter:
    """A deterministic ``len // 4`` tokenizer, so totals are exact literals."""

    def count(self, *, model: str, messages: Any) -> int:
        content = "".join(str(message.get("content", "")) for message in messages)
        return len(content) // 4


class ExplodingRecorder:
    """A recorder whose every method raises.

    The point of §6.4 is that a *measurement* concern can never take a run down,
    and a middleware that trusted an injected collaborator not to raise would
    have moved that contract outside the code that owns it. This double proves
    the guard is in the middleware, not merely in the recorder.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def capture(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls.append("capture")
        raise RuntimeError("occupancy capture exploded")

    def finalize(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls.append("finalize")
        raise RuntimeError("occupancy finalize exploded")

    async def persist(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        self.calls.append("persist")
        raise RuntimeError("occupancy persist exploded")

    def project(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls.append("project")
        raise RuntimeError("occupancy project exploded")


class PostResponseExplodingRecorder:
    """Captures normally, then explodes once the provider has already answered.

    The nastier half of §6.4. A capture that fails costs nothing because the
    response has not been produced yet; a *reconciliation* that fails happens
    after the model has spoken, and the seam's existing rule there is absolute —
    post-response telemetry never discards output.
    """

    def __init__(self, *, inner: ContextOccupancyRecorder) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def capture(self, *args: object, **kwargs: object) -> object:
        self.calls.append("capture")
        return self._inner.capture(*args, **kwargs)  # type: ignore[arg-type]

    def finalize(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls.append("finalize")
        raise RuntimeError("occupancy finalize exploded")

    async def persist(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        self.calls.append("persist")
        raise RuntimeError("occupancy persist exploded")


class OccupancySink:
    """A store double that dedupes on the natural key, like every adapter."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    async def append_context_occupancy(self, record: Any) -> bool:
        if any(item.idempotency_key == record.idempotency_key for item in self.records):
            return False
        self.records.append(record)
        return True


class Journal:
    """In-memory F10 journal enforcing the store's own ordering rules."""

    def __init__(self) -> None:
        self.records: list[SequencedModelInvocationRecord] = []

    async def append(
        self, write: ModelInvocationWrite
    ) -> SequencedModelInvocationRecord:
        existing = next(
            (
                item
                for item in self.records
                if item.record.record_id == write.record.record_id
            ),
            None,
        )
        if existing is not None:
            return existing
        EventJournalModelInvocationStore._validate_next(  # noqa: SLF001
            run_id=write.record.run_id,
            records=tuple(self.records),
            candidate=write.record,
        )
        item = SequencedModelInvocationRecord(
            sequence_no=len(self.records) + 1, record=write.record
        )
        self.records.append(item)
        return item

    async def list_for_run(self, **kwargs: object) -> tuple[Any, ...]:
        del kwargs
        return tuple(self.records)

    async def list_for_invocation(
        self, *, invocation_id: str, **kwargs: object
    ) -> tuple[Any, ...]:
        del kwargs
        return tuple(
            item for item in self.records if item.record.invocation_id == invocation_id
        )

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(item.record.record_kind for item in self.records)


class AuthorityAdapter:
    """Minimal F10 authority double bound to one deployment catalog."""

    def __init__(self, *, budget: ModelInvocationBudget | None = None) -> None:
        descriptor = ModelDeploymentDescriptor(
            deployment_id="primary",
            endpoint_ref="endpoint_" + "1" * 32,
            provider="openai",
            model_name="gpt-5",
            capabilities=frozenset({ModelCapability.STREAMING}),
            max_input_tokens=_WINDOW,
            max_output_tokens=1_000,
            region="global",
            credential_modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
            price_revision="price-v1",
            descriptor_revision="descriptor-v1",
        )
        self.catalog = ModelDeploymentCatalog.create((descriptor,))
        requirements = ModelInvocationRequirements(
            task_family="research",
            provider="openai",
            model_name="gpt-5",
            primary_deployment_id="primary",
            required_capabilities=frozenset({ModelCapability.STREAMING}),
            minimum_context_tokens=1,
            credential_availability=(
                ModelCredentialAvailability(
                    provider="openai",
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
            ),
            fallback_policy=ModelFallbackPolicy.NONE,
            budget=budget or ModelInvocationBudget(),
        )
        self.requirements = ModelInvocationRequirementsSnapshot.create(requirements)
        self.route_plan = ModelRoutePlan.create(
            routes=(
                ModelRouteEntry.from_descriptor(
                    descriptor,
                    credential_mode=ModelCredentialMode.DEPLOYMENT,
                ),
            ),
            exclusions=(),
            fallback_policy=ModelFallbackPolicy.NONE,
            budget=requirements.budget,
        )

    def prepare(self, *, authority_input: object, call_identity: object, control: Any):
        return SimpleNamespace(
            authority=ModelInvocationAuthority.create(
                call_identity=call_identity,
                purpose="main",
                request_digest=cast(str, authority_input),
                run_control_snapshot_digest=control.snapshot.snapshot_digest,
                requirements=self.requirements,
                catalog=self.catalog,
                route_plan=self.route_plan,
            ),
            requirements=self.requirements,
            catalog=self.catalog,
            route_plan=self.route_plan,
        )


class OccupancyMiddlewareMixin:
    """Bindings, requests, and invocation helpers for the seam under test."""

    ORG_ID: Final[str] = "org-1"
    RUN_ID: Final[str] = "run-1"
    CONVERSATION_ID: Final[str] = "conversation-1"
    POLICY_TEXT: Final[str] = "Runtime safety policy the model must follow."

    DECLARED_TOOL_ORIGIN: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities.backends",
        name="publish_artifact",
        segment_class=ContextSegmentClass.TOOLS,
        lifecycle=ContextLifecycle.RESIDENT,
    )

    def recorder(self) -> ContextOccupancyRecorder:
        """A recorder with a private cache and no third-party sweep."""

        return ContextOccupancyRecorder(
            counter=ContextTokenCounter(
                tokenizer=LengthCounter(),
                heuristic=LengthCounter(),
                cache=DigestTokenCache(max_entries=64),
            ),
            third_party=ThirdPartyPromptIndex.disabled(),
        )

    def middleware(self, recorder: object | None = None) -> ModelInvocationMiddleware:
        return ModelInvocationMiddleware(
            occupancy_recorder=cast(Any, recorder or self.recorder())
        )

    def control(self) -> RunControlBinding:
        modes = FeatureModeSet.model_validate(
            {field: FeatureMode.OFF for field in FeatureModeSet.model_fields}
        )
        revisions = {field: "v1" for field in RunPolicyRevisions.model_fields}
        revisions["model_route"] = "model-route-policy.v2"
        snapshot = RunControlSnapshot.create(
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
            subject_fingerprint=_SHA,
            deployment_profile="single_user_desktop",
            harness_variant_ref="harness-v1",
            task_policy_selection_ref="task-v1",
            policy_revisions=RunPolicyRevisions.model_validate(revisions),
            feature_modes=modes,
            budget_envelope_ref=f"budget://v1/sha256/{_SHA}",
            assignment_revision="assignment-v1",
        )
        return RunControlBinding(snapshot=snapshot, effective_modes=modes, decisions=())

    def binding(
        self,
        *,
        journal: Journal,
        authority: AuthorityAdapter,
        sink: OccupancySink | None = None,
        retry: bool = False,
    ) -> ModelInvocationRuntimeBinding:
        release = ModelReliabilityReleaseResolver().resolve(
            run_id=self.RUN_ID,
            snapshot_id="snapshot-1",
            snapshot_digest=_SHA,
            snapshot=ModelReliabilityControlSnapshot(
                same_deployment_retry=(
                    FeatureMode.ENFORCE if retry else FeatureMode.OFF
                ),
            ),
            snapshot_f10_mode=FeatureMode.ENFORCE,
            effective_f10_mode=FeatureMode.ENFORCE,
        )
        return ModelInvocationRuntimeBinding(
            authority_adapter=authority,
            authority_input_factory=lambda digest: digest,
            journal=journal,
            route_model_resolver=None,
            release=release,
            org_id=self.ORG_ID,
            subject_fingerprint=_SHA,
            trace_id="trace-1",
            failure_adapters=ProviderFailureAdapterRegistry.defaults(),
            projected_cost_microusd=0,
            projected_input_tokens=0,
            projected_output_tokens=0,
            context_occupancy_store=sink,
            now=lambda: _NOW,
        )

    def tool(self, *, name: str = "search", declared: bool = True) -> StructuredTool:
        def implementation(query: str) -> str:
            return query

        built = StructuredTool.from_function(
            implementation, name=name, description="Search the authorized corpus."
        )
        if declared:
            declare_context_origin(built, self.DECLARED_TOOL_ORIGIN)
        return built

    def request(
        self,
        *,
        child: str | None = None,
        tools: Any = (),
        system_text: str | None = None,
        turn: int = 1,
    ) -> ModelRequest[Any]:
        metadata = {"supervisor_task_call_id": child} if child else {}
        return ModelRequest(
            model=FakeListChatModel(responses=["done"]),
            messages=[HumanMessage(content="private user body")],
            system_message=SystemMessage(
                content=self.POLICY_TEXT if system_text is None else system_text
            ),
            tools=list(tools),
            state={"runtime_control_model_turn": turn},
            runtime=cast(Any, SimpleNamespace(config={"metadata": metadata})),
            model_settings={},
        )

    async def handler(self, request: ModelRequest[Any]) -> ModelResponse[Any]:
        del request
        return ModelResponse(
            result=[
                AIMessage(
                    content="done",
                    usage_metadata={
                        "input_tokens": 900,
                        "output_tokens": 12,
                        "total_tokens": 912,
                    },
                )
            ]
        )

    async def invoke(
        self,
        *,
        binding: ModelInvocationRuntimeBinding,
        request: ModelRequest[Any],
        handler: Any = None,
        middleware: ModelInvocationMiddleware | None = None,
        handoff: PromptCacheFallbackHandoff | None = None,
    ) -> ModelResponse[Any]:
        token = RunControlContext.bind_for_run(self.control())
        try:
            RunControlContext.install_model_invocation_runtime(binding)
            with PromptCacheFallbackContext.bind(handoff):
                return await (middleware or self.middleware()).awrap_model_call(
                    request, handler or self.handler
                )
        finally:
            RunControlContext.unbind(token)

    def plan_handoff(self) -> tuple[PromptCacheFallbackHandoff, str]:
        """An F2 handoff carrying a real assembly plan, plus its rendered text.

        Built directly rather than through ``RuntimeToolControlMiddleware`` so
        the test asserts one thing: that the capture seam reads the plan the
        outer middleware bound for it, and attributes the system block from it.
        """

        plan = PromptAssembler(
            context=PromptAssemblyContext(
                provider="openai",
                model_family="gpt-5",
                harness_revision="harness-v1",
                capability_bridge_revision="bridge-v1",
                tool_schema_revision="tools-v1",
                policy_revision="policy-v1",
                authorization_revision="authorization-v1",
            )
        ).assemble(
            (
                PromptFragment(
                    fragment_id="00_base_runtime",
                    source_owner="agent_runtime.prompts",
                    source_revision="v1",
                    tier=PromptFragmentTier.SYSTEM_POLICY,
                    source_scope=PromptFragmentScope.INSTALLATION,
                    scope=PromptFragmentScope.INSTALLATION,
                    sensitivity=PromptSensitivity.INTERNAL,
                    trust=PromptTrustLabel.IMMUTABLE_POLICY,
                    content=self.POLICY_TEXT,
                    cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
                ),
            )
        )
        result = PromptRuntimeResult(
            system_message=SystemMessage(content=plan.rendered_prompt),
            tools=(),
            plan=plan,
            decoration=None,
            observation=PromptRuntimeObservation(
                mode=FeatureMode.ENFORCE,
                provider="openai",
                model_family="gpt-5",
                execution_scope="supervisor",
                harness_revision="harness-v1",
                tool_schema_revision="a" * 64,
                cache_reason_code="test",
                sent_assembled_prompt=True,
            ),
        )
        return (
            PromptCacheFallbackHandoff(
                result=result,
                rejection_adapters=ProviderCacheRejectionAdapterRegistry(()),
            ),
            plan.rendered_prompt,
        )


class TestOccupancyCapture(OccupancyMiddlewareMixin):
    async def test_one_successful_call_writes_one_reconciled_row(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(tools=[self.tool()]),
        )

        assert len(sink.records) == 1
        row = sink.records[0]
        assert row.org_id == self.ORG_ID
        assert row.run_id == self.RUN_ID
        assert row.conversation_id == self.CONVERSATION_ID
        assert row.attempt_ordinal == 1
        assert row.provider == "openai"
        assert row.model_family == "gpt-5"

    async def test_the_provider_total_is_copied_from_the_observed_usage(self) -> None:
        # §6.1: read-side denormalization for reconciliation, never a second
        # meter. The number must be the one the usage lane already saw.
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(),
        )

        row = sink.records[0]
        assert row.provider_input_tokens == 900
        assert row.unattributed_delta == 900 - row.estimated_input_tokens

    async def test_the_window_comes_from_the_dispatched_route(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(),
        )

        row = sink.records[0]
        assert row.context_window_tokens == _WINDOW
        assert row.free_tokens == _WINDOW - 900

    async def test_a_call_that_reports_no_usage_leaves_the_total_absent(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        async def silent_handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
            del request
            return ModelResponse(result=[AIMessage(content="done")])

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(),
            handler=silent_handler,
        )

        row = sink.records[0]
        assert row.provider_input_tokens is None
        assert row.unattributed_delta == 0

    async def test_an_undeclared_tool_lights_the_alarm_at_runtime(self) -> None:
        # §4.4: the AST gate is the CI half; this is the runtime half, and
        # neither substitutes for the other.
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(tools=[self.tool(name="rogue", declared=False)]),
        )

        row = sink.records[0]
        assert row.undeclared_tokens > 0
        assert UNDECLARED_CONTEXT_LABEL in {
            segment["label"] for segment in row.segments
        }

    async def test_a_declared_tool_reports_its_owner(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(tools=[self.tool()]),
        )

        labels = {segment["label"] for segment in sink.records[0].segments}
        assert "agent_runtime.capabilities.backends:publish_artifact" in labels

    async def test_the_f2_plan_bound_by_the_outer_middleware_is_used(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        handoff, rendered = self.plan_handoff()

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(system_text=rendered),
            handoff=handoff,
        )

        labels = {segment["label"] for segment in sink.records[0].segments}
        assert "agent_runtime.prompts:00_base_runtime" in labels

    async def test_no_store_wired_is_a_silent_skip(self) -> None:
        journal, authority = Journal(), AuthorityAdapter()

        response = await self.invoke(
            binding=self.binding(journal=journal, authority=authority),
            request=self.request(),
        )

        assert response.result[0].content == "done"


class TestScopeSeparation(OccupancyMiddlewareMixin):
    async def test_the_supervisor_and_a_child_are_measured_in_their_own_windows(
        self,
    ) -> None:
        # §6.2: a subagent has its OWN window. Summing child occupancy into the
        # parent would report >100% utilization on any run that delegates.
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        binding = self.binding(journal=journal, authority=authority, sink=sink)

        await self.invoke(binding=binding, request=self.request())
        await self.invoke(binding=binding, request=self.request(child="task-7"))

        scopes = {row.graph_scope for row in sink.records}
        assert scopes == {
            RuntimeContextGraphScope.ROOT,
            RuntimeContextGraphScope.SUBAGENT,
        }

    async def test_each_scope_keeps_its_own_call_identity(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        binding = self.binding(journal=journal, authority=authority, sink=sink)

        await self.invoke(binding=binding, request=self.request())
        await self.invoke(binding=binding, request=self.request(child="task-7"))

        assert len({row.model_call_id for row in sink.records}) == 2

    async def test_free_space_is_computed_inside_one_scope_only(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        binding = self.binding(journal=journal, authority=authority, sink=sink)

        await self.invoke(binding=binding, request=self.request())
        await self.invoke(binding=binding, request=self.request(child="task-7"))

        # Neither row's free space is reduced by the other's occupancy: both
        # subtract only their own provider total from their own window.
        assert [row.free_tokens for row in sink.records] == [
            _WINDOW - 900,
            _WINDOW - 900,
        ]


class TestRetryProducesASecondSnapshot(OccupancyMiddlewareMixin):
    def retryable_error(self) -> type[Exception]:
        error = type("APIConnectionError", (Exception,), {})
        error.__module__ = "openai"
        return error

    async def test_two_attempts_write_two_rows_under_two_ordinals(self) -> None:
        # §6.3: a retry re-materializes the request against a different window
        # state, so it earns its own row rather than overwriting the first.
        journal = Journal()
        authority = AuthorityAdapter(
            budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
        )
        sink = OccupancySink()
        failure = self.retryable_error()
        calls = 0

        async def flaky(request: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise failure("message text is ignored")
            return await self.handler(request)

        await self.invoke(
            binding=self.binding(
                journal=journal, authority=authority, sink=sink, retry=True
            ),
            request=self.request(),
            handler=flaky,
        )

        assert calls == 2
        assert [row.attempt_ordinal for row in sink.records] == [1, 2]
        assert len({row.model_call_id for row in sink.records}) == 1

    async def test_the_failed_attempt_reports_no_provider_total(self) -> None:
        journal = Journal()
        authority = AuthorityAdapter(
            budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
        )
        sink = OccupancySink()
        failure = self.retryable_error()
        calls = 0

        async def flaky(request: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise failure("message text is ignored")
            return await self.handler(request)

        await self.invoke(
            binding=self.binding(
                journal=journal, authority=authority, sink=sink, retry=True
            ),
            request=self.request(),
            handler=flaky,
        )

        assert sink.records[0].provider_input_tokens is None
        assert sink.records[1].provider_input_tokens == 900

    async def test_a_terminal_failure_still_records_what_was_sent(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()

        async def broken(request: ModelRequest[Any]) -> ModelResponse[Any]:
            del request
            raise RuntimeError("provider refused")

        with pytest.raises(RuntimeError):
            await self.invoke(
                binding=self.binding(journal=journal, authority=authority, sink=sink),
                request=self.request(),
                handler=broken,
            )

        assert len(sink.records) == 1
        assert sink.records[0].attempt_ordinal == 1


class TestTheSeamIsUnchanged(OccupancyMiddlewareMixin):
    """The regression net. Occupancy is additive or it does not ship."""

    async def test_a_recorder_that_raises_on_every_method_costs_nothing(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        recorder = ExplodingRecorder()

        response = await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(tools=[self.tool()]),
            middleware=self.middleware(recorder),
        )

        assert response.result[0].content == "done"
        assert recorder.calls == ["capture"]
        assert sink.records == []
        assert journal.kinds[-1] == "invocation_completed"

    async def test_a_reconciliation_that_explodes_never_discards_the_response(
        self,
    ) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        recorder = PostResponseExplodingRecorder(inner=self.recorder())

        response = await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=self.request(),
            middleware=self.middleware(recorder),
        )

        assert response.result[0].content == "done"
        assert recorder.calls == ["capture", "finalize"]
        assert sink.records == []
        assert journal.kinds[-1] == "invocation_completed"

    async def test_a_reconciliation_that_explodes_on_a_failed_attempt_is_absorbed(
        self,
    ) -> None:
        # The failure path is the delicate one: its persistence block converts a
        # BaseException into ``raise error from persistence_error``, so the
        # occupancy write must not be able to change which error surfaces.
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        recorder = PostResponseExplodingRecorder(inner=self.recorder())

        async def broken(request: ModelRequest[Any]) -> ModelResponse[Any]:
            del request
            raise RuntimeError("provider refused")

        with pytest.raises(RuntimeError, match="provider refused"):
            await self.invoke(
                binding=self.binding(journal=journal, authority=authority, sink=sink),
                request=self.request(),
                handler=broken,
                middleware=self.middleware(recorder),
            )

        assert sink.records == []
        assert journal.kinds[-1] == "invocation_failed"

    async def test_a_raising_recorder_does_not_change_the_journal(self) -> None:
        clean, exploding = Journal(), Journal()

        await self.invoke(
            binding=self.binding(journal=clean, authority=AuthorityAdapter()),
            request=self.request(tools=[self.tool()]),
        )
        await self.invoke(
            binding=self.binding(journal=exploding, authority=AuthorityAdapter()),
            request=self.request(tools=[self.tool()]),
            middleware=self.middleware(ExplodingRecorder()),
        )

        assert clean.kinds == exploding.kinds

    async def test_a_store_that_raises_never_reaches_the_provider_response(
        self,
    ) -> None:
        class BrokenSink:
            async def append_context_occupancy(self, record: Any) -> bool:
                del record
                raise RuntimeError("occupancy store unavailable")

        journal, authority = Journal(), AuthorityAdapter()

        response = await self.invoke(
            binding=self.binding(
                journal=journal, authority=authority, sink=cast(Any, BrokenSink())
            ),
            request=self.request(),
        )

        assert response.result[0].content == "done"
        assert journal.kinds[-1] == "invocation_completed"

    async def test_the_journal_is_identical_with_and_without_occupancy(self) -> None:
        # Same records, same order, same digests: occupancy writes to its own
        # lane and touches nothing the replay path reads.
        without, with_occupancy = Journal(), Journal()

        await self.invoke(
            binding=self.binding(journal=without, authority=AuthorityAdapter()),
            request=self.request(tools=[self.tool()]),
        )
        await self.invoke(
            binding=self.binding(
                journal=with_occupancy,
                authority=AuthorityAdapter(),
                sink=OccupancySink(),
            ),
            request=self.request(tools=[self.tool()]),
        )

        assert without.kinds == with_occupancy.kinds
        assert [
            item.record.request_digest
            for item in without.records
            if hasattr(item.record, "request_digest")
        ] == [
            item.record.request_digest
            for item in with_occupancy.records
            if hasattr(item.record, "request_digest")
        ]

    async def test_the_request_the_provider_receives_is_never_mutated(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        request = self.request(tools=[self.tool()])
        before = canonical_model_request_digest(request)
        seen: list[ModelRequest[Any]] = []

        async def capturing(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            seen.append(inner)
            return await self.handler(inner)

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=request,
            handler=capturing,
        )

        assert canonical_model_request_digest(request) == before
        assert seen[0].messages == request.messages
        assert seen[0].system_message == request.system_message
        assert seen[0].tools == request.tools
        assert seen[0].response_format == request.response_format

    async def test_the_authority_still_sees_the_unchanged_request_digest(self) -> None:
        journal, authority, sink = Journal(), AuthorityAdapter(), OccupancySink()
        request = self.request(tools=[self.tool()])

        recorded: list[str] = []
        original_prepare = authority.prepare

        def observing_prepare(**kwargs: Any):
            recorded.append(cast(str, kwargs["authority_input"]))
            return original_prepare(**kwargs)

        authority.prepare = observing_prepare  # type: ignore[method-assign]

        await self.invoke(
            binding=self.binding(journal=journal, authority=authority, sink=sink),
            request=request,
        )

        assert recorded == [canonical_model_request_digest(request)]

    async def test_the_middleware_still_constructs_with_no_arguments(self) -> None:
        # The graph funnel passes the class itself as a child-graph factory and
        # the AST topology gate pins that spelling, so a required constructor
        # argument would break every subagent graph, not just one call site.
        journal, authority = Journal(), AuthorityAdapter()

        response = await self.invoke(
            binding=self.binding(journal=journal, authority=authority),
            request=self.request(),
            middleware=ModelInvocationMiddleware(),
        )

        assert response.result[0].content == "done"

    async def test_feature_off_paths_are_untouched(self) -> None:
        # No F10 binding installed: the middleware must hand the exact request
        # to the handler and measure nothing at all.
        request = self.request()
        seen: list[ModelRequest[Any]] = []

        async def capturing(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            seen.append(inner)
            return ModelResponse(result=[AIMessage(content="done")])

        recorder = ExplodingRecorder()
        await self.middleware(recorder).awrap_model_call(request, capturing)

        assert seen == [request]
        assert recorder.calls == []

    def test_the_default_recorder_is_shared_across_middleware_instances(self) -> None:
        # One sweep and one digest cache per process; a fresh recorder per graph
        # would pay the memoization cost without ever collecting the benefit.
        first = ModelInvocationMiddleware()
        second = ModelInvocationMiddleware()

        assert first._occupancy is second._occupancy  # noqa: SLF001
