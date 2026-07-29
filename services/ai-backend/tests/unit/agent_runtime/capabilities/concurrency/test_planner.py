from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent_runtime.capabilities.concurrency import (
    BatchOperation,
    BatchPlanner,
    BatchSegmentMode,
    BatchSegmentReason,
    ConcurrencyMode,
    ConcurrencyPolicy,
    OperationBatch,
    PolicySource,
    SideEffectKind,
)


def _operation(
    operation_id: str,
    *,
    authorization_epoch: str = "auth_1",
    dependency_ids: tuple[str, ...] | None = (),
    resource_fingerprints: tuple[str, ...] | None = (),
) -> BatchOperation:
    return BatchOperation(
        operation_id=operation_id,
        authorization_epoch=authorization_epoch,
        dependency_ids=dependency_ids,
        resource_fingerprints=resource_fingerprints,
    )


def _resource(seed: str) -> str:
    return f"hmac-sha256:{seed * 64}"


def _parallel_read(
    *,
    mode: ConcurrencyMode = ConcurrencyMode.PARALLEL_SAFE,
    max_parallelism: int | None = None,
) -> ConcurrencyPolicy:
    return ConcurrencyPolicy(
        mode=mode,
        side_effect=SideEffectKind.READ,
        max_parallelism=max_parallelism,
        policy_source=PolicySource.PRODUCT_CATALOG,
    )


def _plan(
    operations: tuple[BatchOperation, ...],
    policies: Mapping[str, ConcurrencyPolicy] | None = None,
    *,
    max_parallelism: int = 4,
):
    return BatchPlanner().plan(
        OperationBatch(
            batch_id="batch_1",
            operations=operations,
            allowance=max_parallelism,
        ),
        policies,
    )


def test_default_batch_and_missing_policies_are_serial() -> None:
    batch = OperationBatch(
        batch_id="batch_default",
        operations=(_operation("op_1"), _operation("op_2")),
    )

    plan = BatchPlanner().plan(batch)

    assert [segment.mode for segment in plan.segments] == [
        BatchSegmentMode.SERIAL,
        BatchSegmentMode.SERIAL,
    ]
    assert {segment.reason for segment in plan.segments} == {
        BatchSegmentReason.BATCH_SERIAL_DEFAULT
    }


def test_explicit_independent_reads_share_a_parallel_segment() -> None:
    operations = (
        _operation("op_1", resource_fingerprints=(_resource("a"),)),
        _operation("op_2", resource_fingerprints=(_resource("b"),)),
        _operation("op_3", resource_fingerprints=(_resource("c"),)),
    )
    policies = {operation.operation_id: _parallel_read() for operation in operations}

    plan = _plan(operations, policies)

    assert len(plan.segments) == 1
    assert plan.segments[0].mode is BatchSegmentMode.PARALLEL
    assert plan.segments[0].operation_ids == ("op_1", "op_2", "op_3")
    assert plan.segments[0].reason is BatchSegmentReason.INDEPENDENT_READS


@pytest.mark.parametrize(
    ("policy", "operation", "reason"),
    [
        (
            ConcurrencyPolicy(
                mode=ConcurrencyMode.PARALLEL_SAFE,
                side_effect=SideEffectKind.REVERSIBLE_WRITE,
                policy_source=PolicySource.PRODUCT_CATALOG,
            ),
            _operation("op"),
            BatchSegmentReason.EFFECTFUL_OPERATION,
        ),
        (
            ConcurrencyPolicy(
                mode=ConcurrencyMode.PARALLEL_SAFE,
                side_effect=SideEffectKind.UNKNOWN,
                policy_source=PolicySource.PRODUCT_CATALOG,
            ),
            _operation("op"),
            BatchSegmentReason.UNKNOWN_SIDE_EFFECT,
        ),
        (
            _parallel_read(),
            _operation("op", dependency_ids=("prior",)),
            BatchSegmentReason.EXPLICIT_DEPENDENCIES,
        ),
        (
            _parallel_read(),
            _operation("op", dependency_ids=None),
            BatchSegmentReason.UNKNOWN_DEPENDENCIES,
        ),
        (
            _parallel_read(),
            _operation("op", resource_fingerprints=None),
            BatchSegmentReason.UNKNOWN_RESOURCES,
        ),
    ],
)
def test_unsafe_or_unknown_facts_form_serial_barriers(
    policy: ConcurrencyPolicy,
    operation: BatchOperation,
    reason: BatchSegmentReason,
) -> None:
    dependencies = operation.dependency_ids or ()
    prerequisites = tuple(_operation(dependency_id) for dependency_id in dependencies)
    operations = prerequisites + (operation,)
    policies = {
        prerequisite.operation_id: _parallel_read() for prerequisite in prerequisites
    }
    policies[operation.operation_id] = policy

    plan = _plan(operations, policies)

    assert plan.segments[-1].mode is BatchSegmentMode.SERIAL
    assert plan.segments[-1].reason is reason


def test_resource_conflict_and_authorization_change_form_serial_barriers() -> None:
    operations = (
        _operation("op_1", resource_fingerprints=(_resource("a"),)),
        _operation("op_2", resource_fingerprints=(_resource("a"),)),
        _operation(
            "op_3",
            authorization_epoch="auth_2",
            resource_fingerprints=(_resource("c"),),
        ),
    )
    policies = {operation.operation_id: _parallel_read() for operation in operations}

    plan = _plan(operations, policies)

    assert [segment.reason for segment in plan.segments] == [
        BatchSegmentReason.INSUFFICIENT_PARALLEL_MEMBERS,
        BatchSegmentReason.RESOURCE_CONFLICT,
        BatchSegmentReason.INSUFFICIENT_PARALLEL_MEMBERS,
    ]


def test_authorization_change_cannot_join_an_open_parallel_segment() -> None:
    operations = (
        _operation("op_1", resource_fingerprints=(_resource("a"),)),
        _operation(
            "op_2",
            authorization_epoch="auth_2",
            resource_fingerprints=(_resource("b"),),
        ),
        _operation(
            "op_3",
            authorization_epoch="auth_2",
            resource_fingerprints=(_resource("c"),),
        ),
    )
    policies = {operation.operation_id: _parallel_read() for operation in operations}

    plan = _plan(operations, policies)

    assert [segment.reason for segment in plan.segments] == [
        BatchSegmentReason.INSUFFICIENT_PARALLEL_MEMBERS,
        BatchSegmentReason.AUTHORIZATION_EPOCH_BARRIER,
        BatchSegmentReason.INSUFFICIENT_PARALLEL_MEMBERS,
    ]


def test_effective_parallelism_chunks_output_stably() -> None:
    operations = tuple(_operation(f"op_{index}") for index in range(5))
    policies = {
        operation.operation_id: _parallel_read(max_parallelism=2)
        for operation in operations
    }

    first = _plan(operations, policies, max_parallelism=4)
    second = _plan(
        operations,
        dict(reversed(tuple(policies.items()))),
        max_parallelism=4,
    )

    assert first == second
    assert [segment.operation_ids for segment in first.segments] == [
        ("op_0", "op_1"),
        ("op_2", "op_3"),
        ("op_4",),
    ]
    assert [segment.mode for segment in first.segments] == [
        BatchSegmentMode.PARALLEL,
        BatchSegmentMode.PARALLEL,
        BatchSegmentMode.SERIAL,
    ]


def test_same_subject_serial_allows_only_distinct_known_subjects() -> None:
    operations = (
        _operation("op_1", resource_fingerprints=(_resource("a"),)),
        _operation("op_2", resource_fingerprints=(_resource("b"),)),
    )
    policies = {
        operation.operation_id: _parallel_read(mode=ConcurrencyMode.SAME_SUBJECT_SERIAL)
        for operation in operations
    }

    plan = _plan(operations, policies)

    assert plan.segments[0].mode is BatchSegmentMode.PARALLEL


def test_unknown_policy_ids_fail_closed() -> None:
    with pytest.raises(ValueError, match="operations in the batch"):
        _plan(
            (_operation("op_1"),),
            {"not_in_batch": _parallel_read()},
        )


def test_batch_rejects_duplicate_ids_and_unknown_dependency_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        OperationBatch(
            batch_id="batch_1",
            operations=(_operation("op_1"), _operation("op_1")),
        )

    with pytest.raises(ValueError, match="reference operations"):
        OperationBatch(
            batch_id="batch_1",
            operations=(_operation("op_1", dependency_ids=("missing",)),),
        )

    with pytest.raises(ValueError, match="earlier operations"):
        OperationBatch(
            batch_id="batch_1",
            operations=(
                _operation("op_1", dependency_ids=("op_2",)),
                _operation("op_2"),
            ),
        )


def test_resource_fingerprints_reject_raw_subject_identifiers() -> None:
    with pytest.raises(ValueError, match="keyed HMAC"):
        _operation("op_1", resource_fingerprints=("customer:123",))
