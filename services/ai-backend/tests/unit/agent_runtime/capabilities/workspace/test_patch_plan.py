from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    WorkspaceEntryKind,
    blob_key_from_content_ref,
    content_ref_for_blob,
)
from agent_runtime.capabilities.workspace.patch_plan import (
    MAX_CHANGED_CONTENT_BYTES,
    MAX_HUNKS_PER_OPERATION,
    MAX_PATCH_BYTES,
    MAX_PATCH_HUNKS,
    MAX_PATCH_OPERATIONS,
    MAX_PATCH_TARGETS,
    PatchValidationIssueCode,
    WorkspaceEditPlanBinding,
    WorkspacePatchExpectedPath,
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
    expected_result: tuple[WorkspacePatchExpectedPath, ...] | None = None,
) -> WorkspacePatchSet:
    target_set = WorkspacePatchTargetSet.build(
        target_set_id="targets-1",
        target_set_revision=3,
        base_manifest_revision=7,
        targets=targets,
    )
    return WorkspacePatchSet.build(
        patch_set_id="patch-1",
        edit_plan=WorkspaceEditPlanBinding(
            edit_plan_id="edit-1",
            edit_plan_revision=2,
            edit_plan_digest=f"sha256:{_digest('edit-plan')}",
            base_manifest_revision=7,
            target_set_id=target_set.target_set_id,
            target_set_revision=target_set.target_set_revision,
            target_set_digest=target_set.target_set_digest,
        ),
        target_set=target_set,
        operations=operations,
        expected_changed_paths=expected_changed_paths,
        expected_result=expected_result
        or tuple(
            _expected_result(path=path, operations=operations, targets=targets)
            for path in expected_changed_paths
        ),
    )


def _expected_result(
    *,
    path: str,
    operations: tuple[WorkspacePatchOperation, ...],
    targets: tuple[WorkspacePatchTarget, ...],
) -> WorkspacePatchExpectedPath:
    for operation in operations:
        if path == operation.virtual_path:
            if operation.kind in {
                WorkspacePatchOperationKind.DELETE,
                WorkspacePatchOperationKind.MOVE,
            }:
                return WorkspacePatchExpectedPath(
                    virtual_path=path,
                    existence=BaseExistence.MUST_NOT_EXIST,
                )
            if operation.content_ref is not None:
                return WorkspacePatchExpectedPath(
                    virtual_path=path,
                    existence=BaseExistence.MUST_EXIST,
                    entry_kind=WorkspaceEntryKind.FILE,
                    content_digest=blob_key_from_content_ref(operation.content_ref),
                    size_bytes=operation.content_size_bytes,
                )
            return WorkspacePatchExpectedPath(
                virtual_path=path,
                existence=BaseExistence.MUST_EXIST,
                entry_kind=WorkspaceEntryKind.FILE,
                content_digest=_digest(f"expected:{path}"),
                size_bytes=0,
            )
        if path == operation.destination_virtual_path:
            return WorkspacePatchExpectedPath(
                virtual_path=path,
                existence=BaseExistence.MUST_EXIST,
                entry_kind=WorkspaceEntryKind.FILE,
                content_digest=operation.precondition.content_digest,
                size_bytes=0,
            )
    target = next((item for item in targets if item.virtual_path == path), None)
    if (
        target is not None
        and target.precondition.existence is BaseExistence.MUST_NOT_EXIST
    ):
        return WorkspacePatchExpectedPath(
            virtual_path=path,
            existence=BaseExistence.MUST_NOT_EXIST,
        )
    return WorkspacePatchExpectedPath(
        virtual_path=path,
        existence=BaseExistence.MUST_EXIST,
        entry_kind=WorkspaceEntryKind.FILE,
        content_digest=_digest(f"expected:{path}"),
        size_bytes=0,
    )


def _hunk(
    *,
    start: int = 0,
    end: int = 1,
    replacement: str = "",
    replacement_size_bytes: int | None = None,
) -> WorkspacePatchHunk:
    return WorkspacePatchHunk(
        old_start_byte=start,
        old_end_byte=end,
        before_anchor_digest=_digest(f"before-{start}"),
        after_anchor_digest=_digest(f"after-{end}"),
        old_span_digest=_digest(f"span-{start}-{end}"),
        replacement_ref=_ref(replacement),
        replacement_size_bytes=(
            len(replacement.encode())
            if replacement_size_bytes is None
            else replacement_size_bytes
        ),
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
                content_size_bytes=len("test body"),
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
                content_size_bytes=len("replacement"),
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


def test_case_collisions_and_unused_targets_are_rejected() -> None:
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
                content_size_bytes=len("readme"),
            ),
        ),
        expected_changed_paths=(upper,),
    )

    report = WorkspacePatchSetValidator.validate(patch_set)

    assert not report.valid
    assert tuple(issue.code for issue in report.issues) == (
        PatchValidationIssueCode.CASE_COLLISION,
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
        content_size_bytes=len("replacement"),
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
            replacement_size_bytes=len(replacement),
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
            replacement_size_bytes=3,
        )


def test_patch_and_target_digests_are_canonical_and_tamper_evident() -> None:
    path = "/workspace/project/module.py"
    absent = _absent()
    operation = WorkspacePatchOperation(
        operation_id="create-module",
        kind=WorkspacePatchOperationKind.CREATE,
        virtual_path=path,
        precondition=absent,
        content_ref=_ref("body"),
        content_size_bytes=4,
    )

    first = _patch_set(
        targets=(WorkspacePatchTarget(virtual_path=path, precondition=absent),),
        operations=(operation,),
        expected_changed_paths=(path,),
    )
    second = _patch_set(
        targets=tuple(reversed(first.target_set.targets)),
        operations=tuple(reversed(first.operations)),
        expected_changed_paths=tuple(reversed(first.expected_changed_paths)),
    )

    assert first.patch_digest == second.patch_digest
    assert first.target_set.target_set_digest == second.target_set.target_set_digest

    rebound_plan = first.model_dump(mode="json")
    rebound_plan["edit_plan"]["edit_plan_revision"] += 1
    with pytest.raises(ValidationError, match="patch_digest"):
        WorkspacePatchSet.model_validate(rebound_plan)

    changed_result = first.model_dump(mode="json")
    changed_result["expected_result"][0]["size_bytes"] = 5
    with pytest.raises(ValidationError):
        WorkspacePatchSet.model_validate(changed_result)

    changed_target = first.target_set.model_dump(mode="json")
    changed_target["target_set_revision"] += 1
    with pytest.raises(ValidationError, match="target_set_digest"):
        WorkspacePatchTargetSet.model_validate(changed_target)


def test_edit_plan_must_bind_the_exact_target_revision_and_digest() -> None:
    path = "/workspace/project/module.py"
    absent = _absent()
    target_set = WorkspacePatchTargetSet.build(
        target_set_id="targets-1",
        target_set_revision=1,
        base_manifest_revision=7,
        targets=(WorkspacePatchTarget(virtual_path=path, precondition=absent),),
    )
    operation = WorkspacePatchOperation(
        operation_id="create-module",
        kind=WorkspacePatchOperationKind.CREATE,
        virtual_path=path,
        precondition=absent,
        content_ref=_ref("body"),
        content_size_bytes=4,
    )
    expected = WorkspacePatchExpectedPath(
        virtual_path=path,
        existence=BaseExistence.MUST_EXIST,
        entry_kind=WorkspaceEntryKind.FILE,
        content_digest=_digest("body"),
        size_bytes=4,
    )

    with pytest.raises(ValidationError, match="edit-plan binding"):
        WorkspacePatchSet.build(
            patch_set_id="patch-1",
            edit_plan=WorkspaceEditPlanBinding(
                edit_plan_id="edit-1",
                edit_plan_revision=1,
                edit_plan_digest=f"sha256:{_digest('edit')}",
                base_manifest_revision=7,
                target_set_id=target_set.target_set_id,
                target_set_revision=target_set.target_set_revision + 1,
                target_set_digest=target_set.target_set_digest,
            ),
            target_set=target_set,
            operations=(operation,),
            expected_changed_paths=(path,),
            expected_result=(expected,),
        )


def test_expected_results_are_exact_and_cover_the_declared_path_set() -> None:
    path = "/workspace/project/module.py"
    other = "/workspace/project/other.py"
    absent = _absent()
    operation = WorkspacePatchOperation(
        operation_id="create-module",
        kind=WorkspacePatchOperationKind.CREATE,
        virtual_path=path,
        precondition=absent,
        content_ref=_ref("body"),
        content_size_bytes=4,
    )

    with pytest.raises(ValidationError, match="cover exactly"):
        _patch_set(
            targets=(WorkspacePatchTarget(virtual_path=path, precondition=absent),),
            operations=(operation,),
            expected_changed_paths=(path,),
            expected_result=(
                WorkspacePatchExpectedPath(
                    virtual_path=other,
                    existence=BaseExistence.MUST_NOT_EXIST,
                ),
            ),
        )

    with pytest.raises(ValidationError, match="exact file kind"):
        WorkspacePatchExpectedPath(
            virtual_path=path,
            existence=BaseExistence.MUST_EXIST,
        )

    with pytest.raises(ValidationError, match="unconstrained"):
        WorkspacePatchExpectedPath(
            virtual_path=path,
            existence=BaseExistence.ANY,
        )


def test_hard_target_operation_and_per_operation_hunk_limits() -> None:
    absent = _absent()
    with pytest.raises(ValidationError):
        WorkspacePatchTargetSet.build(
            target_set_id="targets",
            target_set_revision=1,
            base_manifest_revision=1,
            targets=tuple(
                WorkspacePatchTarget(
                    virtual_path=f"/workspace/project/file-{index}.py",
                    precondition=absent,
                )
                for index in range(MAX_PATCH_TARGETS + 1)
            ),
        )

    path = "/workspace/project/module.py"
    create = WorkspacePatchOperation(
        operation_id="create",
        kind=WorkspacePatchOperationKind.CREATE,
        virtual_path=path,
        precondition=absent,
        content_ref=_ref(""),
        content_size_bytes=0,
    )
    with pytest.raises(ValidationError):
        _patch_set(
            targets=(WorkspacePatchTarget(virtual_path=path, precondition=absent),),
            operations=(create,) * (MAX_PATCH_OPERATIONS + 1),
            expected_changed_paths=(path,),
        )

    with pytest.raises(ValidationError):
        WorkspacePatchOperation(
            operation_id="hunks",
            kind=WorkspacePatchOperationKind.HUNKS,
            virtual_path=path,
            precondition=_existing("base"),
            hunks=tuple(_hunk() for _index in range(MAX_HUNKS_PER_OPERATION + 1)),
        )


def test_hard_aggregate_hunk_and_authored_byte_limits() -> None:
    targets: list[WorkspacePatchTarget] = []
    operations: list[WorkspacePatchOperation] = []
    expected_paths: list[str] = []
    for index in range((MAX_PATCH_HUNKS // MAX_HUNKS_PER_OPERATION) + 1):
        path = f"/workspace/project/file-{index}.py"
        precondition = _existing(f"base-{index}")
        targets.append(
            WorkspacePatchTarget(virtual_path=path, precondition=precondition)
        )
        operations.append(
            WorkspacePatchOperation(
                operation_id=f"hunks-{index}",
                kind=WorkspacePatchOperationKind.HUNKS,
                virtual_path=path,
                precondition=precondition,
                hunks=tuple(
                    _hunk(start=hunk_index, end=hunk_index + 1)
                    for hunk_index in range(MAX_HUNKS_PER_OPERATION)
                ),
            )
        )
        expected_paths.append(path)

    with pytest.raises(ValidationError, match="aggregate hunk"):
        _patch_set(
            targets=tuple(targets),
            operations=tuple(operations),
            expected_changed_paths=tuple(expected_paths),
        )

    absent = _absent()
    oversized_operations = tuple(
        WorkspacePatchOperation(
            operation_id=f"create-{index}",
            kind=WorkspacePatchOperationKind.CREATE,
            virtual_path=f"/workspace/project/new-{index}.py",
            precondition=absent,
            content_ref=_ref(f"body-{index}"),
            content_size_bytes=(MAX_PATCH_BYTES // 2) + 1,
        )
        for index in range(2)
    )
    with pytest.raises(ValidationError, match="authored-byte"):
        _patch_set(
            targets=tuple(
                WorkspacePatchTarget(
                    virtual_path=operation.virtual_path,
                    precondition=absent,
                )
                for operation in oversized_operations
            ),
            operations=oversized_operations,
            expected_changed_paths=tuple(
                operation.virtual_path for operation in oversized_operations
            ),
        )


def test_hard_changed_content_and_utf8_path_byte_limits() -> None:
    paths = (
        "/workspace/project/first.py",
        "/workspace/project/second.py",
    )
    targets = tuple(
        WorkspacePatchTarget(
            virtual_path=path,
            precondition=_existing(f"base-{index}"),
        )
        for index, path in enumerate(paths)
    )
    operations = tuple(
        WorkspacePatchOperation(
            operation_id=f"hunks-{index}",
            kind=WorkspacePatchOperationKind.HUNKS,
            virtual_path=path,
            precondition=targets[index].precondition,
            hunks=(_hunk(),),
        )
        for index, path in enumerate(paths)
    )
    expected_result = tuple(
        WorkspacePatchExpectedPath(
            virtual_path=path,
            existence=BaseExistence.MUST_EXIST,
            entry_kind=WorkspaceEntryKind.FILE,
            content_digest=_digest(f"result-{index}"),
            size_bytes=(MAX_CHANGED_CONTENT_BYTES // 2) + 1,
        )
        for index, path in enumerate(paths)
    )
    with pytest.raises(ValidationError, match="changed-content"):
        _patch_set(
            targets=targets,
            operations=operations,
            expected_changed_paths=paths,
            expected_result=expected_result,
        )

    oversized_utf8_path = "/workspace/mount/" + "/".join(("😀" * 255,) * 5)
    with pytest.raises(ValidationError, match="hard byte limit"):
        WorkspacePatchTarget(
            virtual_path=oversized_utf8_path,
            precondition=_absent(),
        )

    with pytest.raises(ValidationError):
        WorkspacePatchHunk(
            old_start_byte=1,
            old_end_byte=2,
            before_anchor_digest=_digest("before"),
            after_anchor_digest=_digest("after"),
            old_span_digest=_digest("old"),
            replacement_ref="file:///tmp/replacement",
            replacement_size_bytes=3,
        )
