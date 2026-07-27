from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    WorkspaceEntryKind,
    content_ref_for_blob,
)
from agent_runtime.capabilities.workspace.patch_plan import (
    PatchValidationIssueCode,
    WorkspacePatchHunk,
    WorkspacePatchOperation,
    WorkspacePatchOperationKind,
    WorkspacePatchSet,
    WorkspacePatchSetValidator,
    WorkspacePatchTarget,
    WorkspacePatchTargetSet,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _ref(value: str) -> str:
    return content_ref_for_blob(_digest(value))


def _existing(value: str) -> BasePrecondition:
    return BasePrecondition(
        existence=BaseExistence.MUST_EXIST,
        entry_kind=WorkspaceEntryKind.FILE,
        content_digest=_digest(value),
    )


def _absent() -> BasePrecondition:
    return BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST)


def _patch_set(
    *,
    targets: tuple[WorkspacePatchTarget, ...],
    operations: tuple[WorkspacePatchOperation, ...],
    expected_changed_paths: tuple[str, ...],
) -> WorkspacePatchSet:
    return WorkspacePatchSet(
        patch_set_id="patch-1",
        target_set=WorkspacePatchTargetSet(
            base_manifest_revision=7,
            targets=targets,
        ),
        operations=operations,
        expected_changed_paths=expected_changed_paths,
    )


def test_valid_multi_path_plan_has_stable_operation_order() -> None:
    old_path = "/workspace/project/old.py"
    new_path = "/workspace/project/new.py"
    created_path = "/workspace/project/test_new.py"
    old = _existing("old")
    absent = _absent()
    patch_set = _patch_set(
        targets=(
            WorkspacePatchTarget(virtual_path=new_path, precondition=absent),
            WorkspacePatchTarget(virtual_path=old_path, precondition=old),
            WorkspacePatchTarget(virtual_path=created_path, precondition=absent),
        ),
        operations=(
            WorkspacePatchOperation(
                operation_id="create-test",
                kind=WorkspacePatchOperationKind.CREATE,
                virtual_path=created_path,
                precondition=absent,
                content_ref=_ref("test body"),
            ),
            WorkspacePatchOperation(
                operation_id="move-source",
                kind=WorkspacePatchOperationKind.MOVE,
                virtual_path=old_path,
                destination_virtual_path=new_path,
                precondition=old,
                destination_precondition=absent,
            ),
        ),
        expected_changed_paths=(old_path, created_path, new_path),
    )

    report = WorkspacePatchSetValidator.validate(patch_set)

    assert report.valid
    assert report.issues == ()
    assert report.operation_order == ("move-source", "create-test")
    assert report.changed_paths == (new_path, old_path, created_path)


def test_replace_requires_exact_digest_and_target_precondition_match() -> None:
    path = "/workspace/project/module.py"
    target_precondition = _existing("base")
    patch_set = _patch_set(
        targets=(
            WorkspacePatchTarget(
                virtual_path=path,
                precondition=target_precondition,
            ),
        ),
        operations=(
            WorkspacePatchOperation(
                operation_id="replace-module",
                kind=WorkspacePatchOperationKind.REPLACE,
                virtual_path=path,
                precondition=_absent(),
                content_ref=_ref("replacement"),
            ),
        ),
        expected_changed_paths=(path,),
    )

    report = WorkspacePatchSetValidator.validate(patch_set)

    assert not report.valid
    assert tuple(issue.code for issue in report.issues) == (
        PatchValidationIssueCode.PRECONDITION_MISMATCH,
        PatchValidationIssueCode.PRECONDITION_NOT_EXACT,
    )


def test_case_collisions_and_expected_path_drift_are_rejected() -> None:
    upper = "/workspace/project/Readme.md"
    lower = "/workspace/project/readme.md"
    absent = _absent()
    patch_set = _patch_set(
        targets=(
            WorkspacePatchTarget(virtual_path=upper, precondition=absent),
            WorkspacePatchTarget(virtual_path=lower, precondition=absent),
        ),
        operations=(
            WorkspacePatchOperation(
                operation_id="create-readme",
                kind=WorkspacePatchOperationKind.CREATE,
                virtual_path=upper,
                precondition=absent,
                content_ref=_ref("readme"),
            ),
        ),
        expected_changed_paths=(lower,),
    )

    report = WorkspacePatchSetValidator.validate(patch_set)

    assert not report.valid
    assert tuple(issue.code for issue in report.issues) == (
        PatchValidationIssueCode.CASE_COLLISION,
        PatchValidationIssueCode.EXPECTED_PATH_SET_MISMATCH,
        PatchValidationIssueCode.UNUSED_TARGET,
    )
    assert report.issues[0].related_paths == (upper, lower)


def test_undeclared_duplicate_path_operations_are_reported_deterministically() -> None:
    path = "/workspace/project/module.py"
    old = _existing("base")
    operation = WorkspacePatchOperation(
        operation_id="replace",
        kind=WorkspacePatchOperationKind.REPLACE,
        virtual_path=path,
        precondition=old,
        content_ref=_ref("replacement"),
    )
    patch_set = _patch_set(
        targets=(
            WorkspacePatchTarget(
                virtual_path="/workspace/project/other.py",
                precondition=_existing("other"),
            ),
        ),
        operations=(operation, operation),
        expected_changed_paths=(path,),
    )

    first = WorkspacePatchSetValidator.validate(patch_set)
    second = WorkspacePatchSetValidator.validate(patch_set)

    assert first == second
    assert tuple(issue.code for issue in first.issues) == (
        PatchValidationIssueCode.DUPLICATE_OPERATION_ID,
        PatchValidationIssueCode.PATH_OPERATION_CONFLICT,
        PatchValidationIssueCode.UNDECLARED_TARGET,
        PatchValidationIssueCode.UNUSED_TARGET,
    )


def test_overlapping_or_out_of_order_hunks_are_rejected() -> None:
    path = "/workspace/project/module.py"
    old = _existing("0123456789")

    def hunk(start: int, end: int, replacement: str) -> WorkspacePatchHunk:
        return WorkspacePatchHunk(
            old_start_byte=start,
            old_end_byte=end,
            before_anchor_digest=_digest(f"before-{start}"),
            after_anchor_digest=_digest(f"after-{end}"),
            old_span_digest=_digest(f"span-{start}-{end}"),
            replacement_ref=_ref(replacement),
        )

    patch_set = _patch_set(
        targets=(WorkspacePatchTarget(virtual_path=path, precondition=old),),
        operations=(
            WorkspacePatchOperation(
                operation_id="patch-module",
                kind=WorkspacePatchOperationKind.HUNKS,
                virtual_path=path,
                precondition=old,
                hunks=(hunk(5, 9, "second"), hunk(3, 7, "first")),
            ),
        ),
        expected_changed_paths=(path,),
    )

    report = WorkspacePatchSetValidator.validate(patch_set)

    assert tuple(issue.code for issue in report.issues) == (
        PatchValidationIssueCode.HUNK_ORDER_INVALID,
        PatchValidationIssueCode.OVERLAPPING_HUNKS,
    )
    assert report.issues[1].hunk_index == 0


def test_hunks_require_digest_bound_non_empty_spans_and_immutable_refs() -> None:
    with pytest.raises(ValidationError):
        WorkspacePatchHunk(
            old_start_byte=3,
            old_end_byte=3,
            before_anchor_digest=_digest("before"),
            after_anchor_digest=_digest("after"),
            old_span_digest=_digest("old"),
            replacement_ref=_ref("new"),
        )

    with pytest.raises(ValidationError):
        WorkspacePatchHunk(
            old_start_byte=1,
            old_end_byte=2,
            before_anchor_digest=_digest("before"),
            after_anchor_digest=_digest("after"),
            old_span_digest=_digest("old"),
            replacement_ref="file:///tmp/replacement",
        )
