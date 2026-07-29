"""F6.R contract tests: the three F6 lanes share one vocabulary.

F6.1 (descriptor precedence), F6.4 (scoped permits), and F6.7 (serial kill
switches) were built concurrently in isolation and each defined the same ideas
locally. These tests pin the reconciled shape so the duplication cannot grow
back: one parallelism bound, one scope vocabulary, one authority type — and one
deliberate exception.
"""

from __future__ import annotations

from annotated_types import Ge, Le
import pytest
from pydantic import BaseModel, ValidationError

from agent_runtime.capabilities.concurrency import (
    BatchOperation,
    BatchPlanner,
    BatchSegmentMode,
    BatchSegmentReason,
    CapabilityConcurrencyDeclaration,
    ConcurrencyAllowance,
    ConcurrencyBounds,
    ConcurrencyKillSwitchScope,
    ConcurrencyPolicy,
    ConcurrencyScope,
    OperationBatch,
    PermitAcquisitionRequest,
    PermitCapacity,
    PermitCapacityPolicy,
    PermitScope,
    RunPermitManager,
)
from agent_runtime.control_plane.feature_modes import FeatureMode

PROFILE = "single_user_desktop"
SUBJECT = "a" * 64


class BoundedFieldMixin:
    """Read a declared numeric bound back off a Pydantic field."""

    def bounds_of(self, model: type[BaseModel], field_name: str) -> tuple[int, int]:
        metadata = model.model_fields[field_name].metadata
        lower = next(item.ge for item in metadata if isinstance(item, Ge))
        upper = next(item.le for item in metadata if isinstance(item, Le))
        return lower, upper


class TestOneParallelismBound(BoundedFieldMixin):
    """The ``(1, 16)`` pair is declared once and imported everywhere."""

    BOUNDED_FIELDS: tuple[tuple[type[BaseModel], str], ...] = (
        (ConcurrencyPolicy, "max_parallelism"),
        (CapabilityConcurrencyDeclaration, "max_parallelism"),
        (ConcurrencyAllowance, "max_parallelism"),
        (PermitCapacity, "max_concurrency"),
        (PermitAcquisitionRequest, "max_parallelism"),
    )

    def test_every_bounded_field_uses_the_shared_ceiling(self) -> None:
        expected = (
            ConcurrencyBounds.SERIAL_PARALLELISM,
            ConcurrencyBounds.MAX_PARALLELISM,
        )

        for model, field_name in self.BOUNDED_FIELDS:
            assert self.bounds_of(model, field_name) == expected, (
                f"{model.__name__}.{field_name} restates the parallelism bound"
            )

    def test_the_shared_ceiling_is_the_only_definition(self) -> None:
        from agent_runtime.capabilities.concurrency import kill_switches, permits

        for module in (kill_switches, permits):
            assert not hasattr(module, "SERIAL_PARALLELISM")
            assert not hasattr(module, "MAX_PARALLELISM")

    def test_the_conservative_floor_is_the_serial_bound(self) -> None:
        assert ConcurrencyBounds.SERIAL_PARALLELISM == 1
        assert ConcurrencyAllowance().max_parallelism == (
            ConcurrencyBounds.SERIAL_PARALLELISM
        )
        assert PermitCapacityPolicy.serial().capacity_for(ConcurrencyScope.GLOBAL) == (
            ConcurrencyBounds.SERIAL_PARALLELISM
        )


class TestFoldedScopeVocabulary:
    """One scope enum serves both declared rate limits and enforced permits."""

    def test_rank_order_is_pinned_broadest_first(self) -> None:
        assert tuple(ConcurrencyScope) == (
            ConcurrencyScope.UNKNOWN,
            ConcurrencyScope.GLOBAL,
            ConcurrencyScope.PROFILE,
            ConcurrencyScope.INSTALLATION,
            ConcurrencyScope.USER,
            ConcurrencyScope.CONNECTOR,
            ConcurrencyScope.CAPABILITY,
        )
        assert [scope.rank for scope in ConcurrencyScope] == list(range(7))

    def test_the_fold_preserved_the_pre_existing_rate_limit_ranks(self) -> None:
        """``PROFILE`` was inserted without reordering any original member."""

        original_order = (
            ConcurrencyScope.UNKNOWN,
            ConcurrencyScope.GLOBAL,
            ConcurrencyScope.INSTALLATION,
            ConcurrencyScope.USER,
            ConcurrencyScope.CONNECTOR,
            ConcurrencyScope.CAPABILITY,
        )
        ranks = [scope.rank for scope in original_order]

        assert ranks == sorted(ranks)
        assert ConcurrencyScope.conservative() is ConcurrencyScope.UNKNOWN

    def test_narrowest_still_picks_the_broader_pool(self) -> None:
        assert (
            ConcurrencyScope.narrowest(
                ConcurrencyScope.USER,
                ConcurrencyScope.CAPABILITY,
            )
            is ConcurrencyScope.USER
        )
        assert (
            ConcurrencyScope.narrowest(
                ConcurrencyScope.PROFILE,
                ConcurrencyScope.CONNECTOR,
            )
            is ConcurrencyScope.PROFILE
        )

    def test_profile_is_declarable_as_a_rate_limit_scope(self) -> None:
        policy = ConcurrencyPolicy(rate_limit_scope=ConcurrencyScope.PROFILE)

        assert policy.rate_limit_scope is ConcurrencyScope.PROFILE


class TestUnknownScopeIsBoundedNotUnbounded:
    """An undeclared rate-limit scope may never widen the permit path."""

    def test_unknown_is_the_conservative_policy_default(self) -> None:
        assert ConcurrencyPolicy().rate_limit_scope is ConcurrencyScope.UNKNOWN

    def test_unknown_resolves_to_the_broadest_pool(self) -> None:
        assert ConcurrencyScope.UNKNOWN.permit_pool() is ConcurrencyScope.GLOBAL

        for scope in ConcurrencyScope.permit_pool_kinds():
            assert scope.permit_pool() is scope

    def test_unknown_can_never_identify_a_permit_pool(self) -> None:
        with pytest.raises(ValidationError):
            PermitScope(kind=ConcurrencyScope.UNKNOWN)

        assert ConcurrencyScope.UNKNOWN not in ConcurrencyScope.permit_pool_kinds()

    def test_unknown_can_never_be_configured_with_capacity(self) -> None:
        with pytest.raises(ValidationError):
            PermitCapacity(kind=ConcurrencyScope.UNKNOWN, max_concurrency=8)

        with pytest.raises(ValidationError):
            PermitCapacityPolicy.from_limits({ConcurrencyScope.UNKNOWN: 8})

    def test_unknown_capacity_lookup_is_serial(self) -> None:
        policy = PermitCapacityPolicy.from_limits(
            {
                ConcurrencyScope.GLOBAL: ConcurrencyBounds.MAX_PARALLELISM,
                ConcurrencyScope.CAPABILITY: 4,
            }
        )

        assert policy.capacity_for(ConcurrencyScope.UNKNOWN) == (
            ConcurrencyBounds.SERIAL_PARALLELISM
        )

    @pytest.mark.asyncio
    async def test_an_unknown_declaration_is_still_bounded_by_the_global_pool(
        self,
    ) -> None:
        """The ladder always acquires the pool ``UNKNOWN`` resolves to."""

        policy = ConcurrencyPolicy()
        assert policy.rate_limit_scope is ConcurrencyScope.UNKNOWN

        request = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
        )
        acquired_pools = {scope.kind for scope in request.scopes}
        assert policy.rate_limit_scope.permit_pool() in acquired_pools

        manager = RunPermitManager(policy=PermitCapacityPolicy.serial())
        first = await manager.acquire_lease(request)
        second = await manager.acquire_lease(request)

        assert first.admitted
        assert not second.admitted
        assert first.effective_capacity == ConcurrencyBounds.SERIAL_PARALLELISM


class TestOneAuthorityType:
    """A batch, a segment, and a kill switch all narrow the same value."""

    def _reads(self, count: int) -> tuple[BatchOperation, ...]:
        return tuple(
            BatchOperation(
                operation_id=f"op_{index}",
                authorization_epoch="auth_1",
                dependency_ids=(),
                resource_fingerprints=(),
            )
            for index in range(count)
        )

    def _parallel_safe(self) -> ConcurrencyPolicy:
        from agent_runtime.capabilities.concurrency import (
            ConcurrencyMode,
            PolicySource,
            SideEffectKind,
        )

        return ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    def test_batch_and_segment_carry_an_allowance(self) -> None:
        batch = OperationBatch(
            batch_id="batch_1",
            operations=self._reads(2),
            allowance=ConcurrencyAllowance.enforcing(4),
        )
        policies = {
            operation.operation_id: self._parallel_safe()
            for operation in batch.operations
        }

        plan = BatchPlanner().plan(batch, policies)
        segment = plan.segments[0]

        assert isinstance(batch.allowance, ConcurrencyAllowance)
        assert isinstance(segment.allowance, ConcurrencyAllowance)
        assert segment.mode is BatchSegmentMode.PARALLEL
        assert segment.reason is BatchSegmentReason.INDEPENDENT_READS
        assert segment.allowance.mode is FeatureMode.ENFORCE
        assert segment.effective_max_parallelism == 4
        assert len(segment.operation_ids) <= segment.effective_max_parallelism

    def test_a_bare_ceiling_still_means_what_it_always_meant(self) -> None:
        coerced = OperationBatch(
            batch_id="batch_1",
            operations=self._reads(2),
            allowance=4,
        )
        explicit = OperationBatch(
            batch_id="batch_1",
            operations=self._reads(2),
            allowance=ConcurrencyAllowance.enforcing(4),
        )

        assert coerced == explicit
        assert coerced.effective_max_parallelism == 4

    def test_an_unconfigured_batch_is_serial(self) -> None:
        batch = OperationBatch(batch_id="batch_1", operations=self._reads(2))

        assert batch.allowance == ConcurrencyAllowance.serial()
        assert batch.effective_max_parallelism == (ConcurrencyBounds.SERIAL_PARALLELISM)

        plan = BatchPlanner().plan(
            batch,
            {
                operation.operation_id: self._parallel_safe()
                for operation in batch.operations
            },
        )

        assert [segment.mode for segment in plan.segments] == [
            BatchSegmentMode.SERIAL,
            BatchSegmentMode.SERIAL,
        ]
        assert {segment.reason for segment in plan.segments} == {
            BatchSegmentReason.BATCH_SERIAL_DEFAULT
        }

    def test_a_kill_switch_narrows_a_batch_through_that_one_type(self) -> None:
        """The value the switch narrows is the value the planner reads."""

        admitted = OperationBatch(
            batch_id="batch_1",
            operations=self._reads(2),
            allowance=ConcurrencyAllowance.enforcing(4),
        )
        policies = {
            operation.operation_id: self._parallel_safe()
            for operation in admitted.operations
        }
        assert BatchPlanner().plan(admitted, policies).segments[0].mode is (
            BatchSegmentMode.PARALLEL
        )

        disabled = admitted.model_copy(
            update={"allowance": admitted.allowance.narrowed_to_serial()}
        )
        plan = BatchPlanner().plan(disabled, policies)

        assert disabled.allowance.is_serial
        assert [segment.mode for segment in plan.segments] == [
            BatchSegmentMode.SERIAL,
            BatchSegmentMode.SERIAL,
        ]

    def test_a_shadow_mode_ceiling_never_authorizes_overlap(self) -> None:
        shadowed = ConcurrencyAllowance(mode=FeatureMode.SHADOW, max_parallelism=8)

        assert shadowed.is_serial
        assert shadowed.effective_max_parallelism == (
            ConcurrencyBounds.SERIAL_PARALLELISM
        )

        batch = OperationBatch(
            batch_id="batch_1",
            operations=self._reads(2),
            allowance=shadowed,
        )
        plan = BatchPlanner().plan(
            batch,
            {
                operation.operation_id: self._parallel_safe()
                for operation in batch.operations
            },
        )

        assert {segment.mode for segment in plan.segments} == {BatchSegmentMode.SERIAL}


class TestKillSwitchScopeStaysSeparate:
    """The third scope enum is deliberately *not* folded; smallness is safety."""

    def test_it_names_only_what_an_operator_may_disable(self) -> None:
        assert tuple(ConcurrencyKillSwitchScope) == (
            ConcurrencyKillSwitchScope.GLOBAL,
            ConcurrencyKillSwitchScope.CONNECTOR,
            ConcurrencyKillSwitchScope.CAPABILITY,
        )

    def test_it_did_not_inherit_the_rate_limit_scopes(self) -> None:
        kill_switch_values = {scope.value for scope in ConcurrencyKillSwitchScope}
        concurrency_values = {scope.value for scope in ConcurrencyScope}

        assert kill_switch_values < concurrency_values
        for widened in ("user", "installation", "profile", "unknown"):
            assert widened in concurrency_values
            assert widened not in kill_switch_values

    def test_it_is_not_the_shared_scope_type(self) -> None:
        assert ConcurrencyKillSwitchScope is not ConcurrencyScope
        assert not issubclass(ConcurrencyKillSwitchScope, ConcurrencyScope)
