"""Deterministic, side-effect-free batch segmentation."""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.capabilities.concurrency.contracts import (
    BatchOperation,
    BatchPlan,
    BatchSegment,
    BatchSegmentMode,
    BatchSegmentReason,
    ConcurrencyMode,
    ConcurrencyPolicy,
    OperationBatch,
    PolicySource,
    SideEffectKind,
)


class BatchPlanner:
    """Plan only operations whose independence is explicitly established.

    This class does not execute, authorize, or retry operations. Missing policy
    metadata and missing dependency/resource facts both fail closed to serial
    segments.
    """

    def plan(
        self,
        batch: OperationBatch,
        policies: Mapping[str, ConcurrencyPolicy] | None = None,
    ) -> BatchPlan:
        supplied_policies = dict(policies or {})
        operation_ids = tuple(operation.operation_id for operation in batch.operations)
        unknown_policy_ids = set(supplied_policies) - set(operation_ids)
        if unknown_policy_ids:
            raise ValueError("policies must reference operations in the batch")

        segments: list[BatchSegment] = []
        candidates: list[tuple[BatchOperation, ConcurrencyPolicy]] = []

        def append_serial(
            operation: BatchOperation,
            reason: BatchSegmentReason,
        ) -> None:
            segments.append(
                BatchSegment(
                    segment_index=len(segments),
                    mode=BatchSegmentMode.SERIAL,
                    operation_ids=(operation.operation_id,),
                    reason=reason,
                    max_parallelism=1,
                )
            )

        def flush_candidates() -> None:
            if not candidates:
                return
            candidate_ids = tuple(
                operation.operation_id for operation, _policy in candidates
            )
            if len(candidates) == 1:
                append_serial(
                    candidates[0][0],
                    BatchSegmentReason.INSUFFICIENT_PARALLEL_MEMBERS,
                )
            else:
                segment_limit = min(
                    batch.max_parallelism,
                    *(
                        policy.max_parallelism or batch.max_parallelism
                        for _operation, policy in candidates
                    ),
                )
                segments.append(
                    BatchSegment(
                        segment_index=len(segments),
                        mode=BatchSegmentMode.PARALLEL,
                        operation_ids=candidate_ids,
                        reason=BatchSegmentReason.INDEPENDENT_READS,
                        max_parallelism=segment_limit,
                    )
                )
            candidates.clear()

        for operation in batch.operations:
            policy = supplied_policies.get(operation.operation_id, ConcurrencyPolicy())
            barrier_reason = self._barrier_reason(
                batch=batch,
                operation=operation,
                policy=policy,
            )
            if barrier_reason is not None:
                flush_candidates()
                append_serial(operation, barrier_reason)
                continue

            if candidates:
                if (
                    operation.authorization_epoch
                    != candidates[0][0].authorization_epoch
                ):
                    flush_candidates()
                    append_serial(
                        operation,
                        BatchSegmentReason.AUTHORIZATION_EPOCH_BARRIER,
                    )
                    continue
                candidate_resources = {
                    resource
                    for candidate, _candidate_policy in candidates
                    for resource in candidate.resource_fingerprints or ()
                }
                if candidate_resources.intersection(
                    operation.resource_fingerprints or ()
                ):
                    flush_candidates()
                    append_serial(operation, BatchSegmentReason.RESOURCE_CONFLICT)
                    continue

            effective_limit = min(
                batch.max_parallelism,
                policy.max_parallelism or batch.max_parallelism,
                *(
                    candidate_policy.max_parallelism or batch.max_parallelism
                    for _candidate, candidate_policy in candidates
                ),
            )
            if len(candidates) >= effective_limit:
                flush_candidates()

            candidates.append((operation, policy))
            candidate_limit = min(
                batch.max_parallelism,
                *(
                    candidate_policy.max_parallelism or batch.max_parallelism
                    for _candidate, candidate_policy in candidates
                ),
            )
            if len(candidates) == candidate_limit:
                flush_candidates()

        flush_candidates()
        return BatchPlan(
            batch_id=batch.batch_id,
            operation_ids=operation_ids,
            segments=tuple(segments),
        )

    @staticmethod
    def _barrier_reason(
        *,
        batch: OperationBatch,
        operation: BatchOperation,
        policy: ConcurrencyPolicy,
    ) -> BatchSegmentReason | None:
        if batch.max_parallelism == 1:
            return BatchSegmentReason.BATCH_SERIAL_DEFAULT
        if policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT:
            return BatchSegmentReason.CONSERVATIVE_POLICY_DEFAULT
        if policy.mode is ConcurrencyMode.SERIAL:
            return BatchSegmentReason.POLICY_REQUIRES_SERIAL
        if policy.max_parallelism == 1:
            return BatchSegmentReason.POLICY_PARALLELISM_DISABLED
        if policy.side_effect is SideEffectKind.UNKNOWN:
            return BatchSegmentReason.UNKNOWN_SIDE_EFFECT
        if policy.side_effect not in (SideEffectKind.NONE, SideEffectKind.READ):
            return BatchSegmentReason.EFFECTFUL_OPERATION
        if operation.dependency_ids is None:
            return BatchSegmentReason.UNKNOWN_DEPENDENCIES
        if operation.dependency_ids:
            return BatchSegmentReason.EXPLICIT_DEPENDENCIES
        if operation.resource_fingerprints is None:
            return BatchSegmentReason.UNKNOWN_RESOURCES
        if (
            policy.mode is ConcurrencyMode.SAME_SUBJECT_SERIAL
            and not operation.resource_fingerprints
        ):
            return BatchSegmentReason.SAME_SUBJECT_REQUIRES_RESOURCE
        return None


__all__ = ("BatchPlanner",)
