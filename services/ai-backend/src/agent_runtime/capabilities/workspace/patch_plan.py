"""Pure contracts and deterministic admission validation for workspace patch plans.

This module does not apply patches or resolve content references.  It validates the
closed, metadata-only plan that an eventual C1 overlay transaction may consume.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from enum import StrEnum
import hashlib
import json
import re

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    WorkspaceEntryKind,
    blob_key_from_content_ref,
    normalize_virtual_path,
)
from agent_runtime.execution.contracts import RuntimeContract

_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")

# Product safety ceilings. These are deliberately code-owned and cannot be widened
# by a model-authored plan or request.
MAX_PATCH_TARGETS = 100
MAX_PATCH_OPERATIONS = 100
MAX_HUNKS_PER_OPERATION = 100
MAX_PATCH_HUNKS = 1_000
MAX_PATCH_PATH_BYTES = 4_096
MAX_PATCH_PATH_DEPTH = 64
MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_CHANGED_FILE_BYTES = 20 * 1024 * 1024
MAX_CHANGED_CONTENT_BYTES = 20 * 1024 * 1024


def _canonical_patch_path(value: str) -> str:
    canonical = normalize_virtual_path(value)
    if len(canonical.encode("utf-8")) > MAX_PATCH_PATH_BYTES:
        raise ValueError("patch path exceeds the hard byte limit")
    if len(canonical.split("/")[2:]) > MAX_PATCH_PATH_DEPTH:
        raise ValueError("patch path exceeds the hard depth limit")
    return canonical


def _canonical_digest(contract: RuntimeContract) -> str:
    canonical = json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class WorkspacePatchOperationKind(StrEnum):
    """Closed mutation vocabulary understood by the patch-plan validator."""

    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    MOVE = "move"
    HUNKS = "hunks"


class PatchValidationIssueCode(StrEnum):
    """Stable, content-free reasons why a patch plan cannot be admitted."""

    CASE_COLLISION = "case_collision"
    DUPLICATE_OPERATION_ID = "duplicate_operation_id"
    DUPLICATE_TARGET_PATH = "duplicate_target_path"
    EXPECTED_PATH_SET_MISMATCH = "expected_path_set_mismatch"
    HUNK_ORDER_INVALID = "hunk_order_invalid"
    OVERLAPPING_HUNKS = "overlapping_hunks"
    PATH_OPERATION_CONFLICT = "path_operation_conflict"
    PRECONDITION_MISMATCH = "precondition_mismatch"
    PRECONDITION_NOT_EXACT = "precondition_not_exact"
    UNDECLARED_TARGET = "undeclared_target"
    UNUSED_TARGET = "unused_target"


class WorkspacePatchHunk(RuntimeContract):
    """One digest-bound replacement of an exact non-empty byte span.

    Byte offsets select the span for deterministic overlap checks.  They never
    authorize the edit on their own: the old span and both surrounding anchors are
    bound by SHA-256 digests, and the match count is fixed at one.
    """

    old_start_byte: int = Field(ge=0)
    old_end_byte: int = Field(gt=0)
    before_anchor_digest: str
    after_anchor_digest: str
    old_span_digest: str
    replacement_ref: str
    replacement_size_bytes: int = Field(ge=0, le=MAX_PATCH_BYTES)
    expected_match_count: int = Field(default=1, ge=1, le=1)

    @field_validator("before_anchor_digest", "after_anchor_digest", "old_span_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("hunk digests must be sha256 digests")
        return value

    @field_validator("replacement_ref")
    @classmethod
    def _replacement_is_immutable_blob(cls, value: str) -> str:
        blob_key_from_content_ref(value)
        return value

    @model_validator(mode="after")
    def _span_is_non_empty(self) -> WorkspacePatchHunk:
        if self.old_end_byte <= self.old_start_byte:
            raise ValueError("hunk byte span must be non-empty")
        return self


class WorkspacePatchTarget(RuntimeContract):
    """One path admitted by target discovery at an exact merged-view state."""

    virtual_path: str
    precondition: BasePrecondition

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return _canonical_patch_path(value)


class WorkspacePatchTargetSetMaterial(RuntimeContract):
    """Canonical target inventory material covered by ``target_set_digest``."""

    target_set_id: str = Field(pattern=_OPERATION_ID.pattern)
    target_set_revision: int = Field(ge=1)
    base_manifest_revision: int = Field(ge=0)
    targets: tuple[WorkspacePatchTarget, ...] = Field(
        min_length=1,
        max_length=MAX_PATCH_TARGETS,
    )

    @field_validator("targets")
    @classmethod
    def _canonical_target_order(
        cls,
        value: tuple[WorkspacePatchTarget, ...],
    ) -> tuple[WorkspacePatchTarget, ...]:
        return tuple(sorted(value, key=lambda target: target.virtual_path))


class WorkspacePatchTargetSet(WorkspacePatchTargetSetMaterial):
    """Frozen, digest-bound target inventory for one overlay manifest revision."""

    target_set_digest: str = Field(pattern=_SHA256_REF.pattern)

    @classmethod
    def build(
        cls,
        *,
        target_set_id: str,
        target_set_revision: int,
        base_manifest_revision: int,
        targets: tuple[WorkspacePatchTarget, ...],
    ) -> WorkspacePatchTargetSet:
        material = WorkspacePatchTargetSetMaterial(
            target_set_id=target_set_id,
            target_set_revision=target_set_revision,
            base_manifest_revision=base_manifest_revision,
            targets=targets,
        )
        return cls(
            **material.model_dump(),
            target_set_digest=_canonical_digest(material),
        )

    @model_validator(mode="after")
    def _digest_matches_material(self) -> WorkspacePatchTargetSet:
        material = WorkspacePatchTargetSetMaterial.model_validate(
            self.model_dump(exclude={"target_set_digest"})
        )
        if self.target_set_digest != _canonical_digest(material):
            raise ValueError("target_set_digest does not match target inventory")
        return self


class WorkspaceEditPlanBinding(RuntimeContract):
    """Trusted edit-plan identity that a patch set is allowed to implement."""

    edit_plan_id: str = Field(pattern=_OPERATION_ID.pattern)
    edit_plan_revision: int = Field(ge=1)
    edit_plan_digest: str = Field(pattern=_SHA256_REF.pattern)
    base_manifest_revision: int = Field(ge=0)
    target_set_id: str = Field(pattern=_OPERATION_ID.pattern)
    target_set_revision: int = Field(ge=1)
    target_set_digest: str = Field(pattern=_SHA256_REF.pattern)


class WorkspacePatchExpectedPath(RuntimeContract):
    """Exact post-state expected for one changed path."""

    virtual_path: str
    existence: BaseExistence
    entry_kind: WorkspaceEntryKind | None = None
    content_digest: str | None = None
    size_bytes: int | None = Field(default=None, ge=0, le=MAX_CHANGED_FILE_BYTES)

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return _canonical_patch_path(value)

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("expected content digest must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def _shape_is_exact(self) -> WorkspacePatchExpectedPath:
        if self.existence is BaseExistence.ANY:
            raise ValueError(
                "expected patch results cannot use unconstrained existence"
            )
        present = self.existence is BaseExistence.MUST_EXIST
        has_exact_file = (
            self.entry_kind is WorkspaceEntryKind.FILE
            and self.content_digest is not None
            and self.size_bytes is not None
        )
        if present != has_exact_file:
            raise ValueError(
                "present results require exact file kind, digest, and byte size"
            )
        if not present and any(
            value is not None
            for value in (self.entry_kind, self.content_digest, self.size_bytes)
        ):
            raise ValueError("absent results cannot carry file metadata")
        return self


class WorkspacePatchOperation(RuntimeContract):
    """One declarative mutation in a bounded patch set."""

    operation_id: str
    kind: WorkspacePatchOperationKind
    virtual_path: str
    precondition: BasePrecondition
    destination_virtual_path: str | None = None
    destination_precondition: BasePrecondition | None = None
    content_ref: str | None = None
    content_size_bytes: int | None = Field(default=None, ge=0, le=MAX_PATCH_BYTES)
    hunks: tuple[WorkspacePatchHunk, ...] = Field(
        default=(),
        max_length=MAX_HUNKS_PER_OPERATION,
    )

    @field_validator("operation_id")
    @classmethod
    def _operation_id_is_safe(cls, value: str) -> str:
        if _OPERATION_ID.fullmatch(value) is None:
            raise ValueError("operation_id has an invalid format")
        return value

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return _canonical_patch_path(value)

    @field_validator("destination_virtual_path")
    @classmethod
    def _canonical_destination(cls, value: str | None) -> str | None:
        return _canonical_patch_path(value) if value is not None else None

    @field_validator("content_ref")
    @classmethod
    def _content_is_immutable_blob(cls, value: str | None) -> str | None:
        if value is not None:
            blob_key_from_content_ref(value)
        return value

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> WorkspacePatchOperation:
        is_move = self.kind is WorkspacePatchOperationKind.MOVE
        if is_move != (self.destination_virtual_path is not None):
            raise ValueError("only move operations require a destination path")
        if is_move != (self.destination_precondition is not None):
            raise ValueError("only move operations require a destination precondition")
        if is_move and self.destination_virtual_path == self.virtual_path:
            raise ValueError("move source and destination must differ")

        needs_content = self.kind in {
            WorkspacePatchOperationKind.CREATE,
            WorkspacePatchOperationKind.REPLACE,
        }
        if needs_content != (self.content_ref is not None):
            raise ValueError("create and replace operations require content_ref")
        if needs_content != (self.content_size_bytes is not None):
            raise ValueError(
                "create and replace operations require exact content_size_bytes"
            )

        needs_hunks = self.kind is WorkspacePatchOperationKind.HUNKS
        if needs_hunks != bool(self.hunks):
            raise ValueError("hunk operations require at least one hunk")
        return self

    @property
    def touched_paths(self) -> tuple[str, ...]:
        """Return every path whose merged-view state this operation changes."""

        if self.destination_virtual_path is None:
            return (self.virtual_path,)
        return (self.virtual_path, self.destination_virtual_path)


class WorkspacePatchSetMaterial(RuntimeContract):
    """Canonical patch material covered by ``patch_digest``."""

    patch_set_id: str = Field(pattern=_OPERATION_ID.pattern)
    edit_plan: WorkspaceEditPlanBinding
    target_set: WorkspacePatchTargetSet
    operations: tuple[WorkspacePatchOperation, ...] = Field(
        min_length=1,
        max_length=MAX_PATCH_OPERATIONS,
    )
    expected_changed_paths: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_PATCH_TARGETS,
    )
    expected_result: tuple[WorkspacePatchExpectedPath, ...] = Field(
        min_length=1,
        max_length=MAX_PATCH_TARGETS,
    )

    @field_validator("expected_changed_paths")
    @classmethod
    def _canonical_expected_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_canonical_patch_path(path) for path in value))

    @field_validator("operations")
    @classmethod
    def _canonical_operation_order(
        cls,
        value: tuple[WorkspacePatchOperation, ...],
    ) -> tuple[WorkspacePatchOperation, ...]:
        return tuple(
            sorted(
                value,
                key=lambda operation: (
                    operation.virtual_path,
                    operation.destination_virtual_path or "",
                    operation.operation_id,
                ),
            )
        )

    @field_validator("expected_result")
    @classmethod
    def _canonical_result_order(
        cls,
        value: tuple[WorkspacePatchExpectedPath, ...],
    ) -> tuple[WorkspacePatchExpectedPath, ...]:
        return tuple(sorted(value, key=lambda result: result.virtual_path))

    @model_validator(mode="after")
    def _bindings_and_hard_bounds_hold(self) -> WorkspacePatchSetMaterial:
        target_set = self.target_set
        edit_plan = self.edit_plan
        if (
            edit_plan.target_set_id != target_set.target_set_id
            or edit_plan.target_set_revision != target_set.target_set_revision
            or edit_plan.target_set_digest != target_set.target_set_digest
            or edit_plan.base_manifest_revision != target_set.base_manifest_revision
        ):
            raise ValueError("patch target set does not match its edit-plan binding")

        total_hunks = sum(len(operation.hunks) for operation in self.operations)
        if total_hunks > MAX_PATCH_HUNKS:
            raise ValueError("patch set exceeds the hard aggregate hunk limit")
        authored_bytes = sum(
            (operation.content_size_bytes or 0)
            + sum(hunk.replacement_size_bytes for hunk in operation.hunks)
            for operation in self.operations
        )
        if authored_bytes > MAX_PATCH_BYTES:
            raise ValueError("patch set exceeds the hard authored-byte limit")

        result_paths = tuple(result.virtual_path for result in self.expected_result)
        if len(result_paths) != len(set(result_paths)):
            raise ValueError("expected_result paths must be unique")
        if set(result_paths) != set(self.expected_changed_paths):
            raise ValueError(
                "expected_result must cover exactly expected_changed_paths"
            )
        changed_content_bytes = sum(
            result.size_bytes or 0 for result in self.expected_result
        )
        if changed_content_bytes > MAX_CHANGED_CONTENT_BYTES:
            raise ValueError("expected result exceeds the hard changed-content limit")
        self._validate_expected_operation_results()
        return self

    def _validate_expected_operation_results(self) -> None:
        expected_by_path = {
            result.virtual_path: result for result in self.expected_result
        }
        for operation in self.operations:
            source = expected_by_path.get(operation.virtual_path)
            if operation.kind in {
                WorkspacePatchOperationKind.DELETE,
                WorkspacePatchOperationKind.MOVE,
            }:
                if (
                    source is None
                    or source.existence is not BaseExistence.MUST_NOT_EXIST
                ):
                    raise ValueError(
                        "delete and move sources require an absent expected result"
                    )
            else:
                self._validate_present_result(operation=operation, expected=source)

            if operation.kind is WorkspacePatchOperationKind.MOVE:
                destination = expected_by_path.get(
                    operation.destination_virtual_path or ""
                )
                if (
                    destination is None
                    or destination.existence is not BaseExistence.MUST_EXIST
                    or destination.content_digest
                    != operation.precondition.content_digest
                ):
                    raise ValueError(
                        "move destination must preserve the exact source digest"
                    )

    @staticmethod
    def _validate_present_result(
        *,
        operation: WorkspacePatchOperation,
        expected: WorkspacePatchExpectedPath | None,
    ) -> None:
        if expected is None or expected.existence is not BaseExistence.MUST_EXIST:
            raise ValueError(
                "write operations require an exact present expected result"
            )
        if operation.kind in {
            WorkspacePatchOperationKind.CREATE,
            WorkspacePatchOperationKind.REPLACE,
        }:
            content_digest = blob_key_from_content_ref(operation.content_ref or "")
            if (
                expected.content_digest != content_digest
                or expected.size_bytes != operation.content_size_bytes
            ):
                raise ValueError(
                    "write operation material does not match its expected result"
                )


class WorkspacePatchSet(WorkspacePatchSetMaterial):
    """One edit-plan-bound, digest-verified multi-path patch proposal."""

    patch_digest: str = Field(pattern=_SHA256_REF.pattern)

    @classmethod
    def build(
        cls,
        *,
        patch_set_id: str,
        edit_plan: WorkspaceEditPlanBinding,
        target_set: WorkspacePatchTargetSet,
        operations: tuple[WorkspacePatchOperation, ...],
        expected_changed_paths: tuple[str, ...],
        expected_result: tuple[WorkspacePatchExpectedPath, ...],
    ) -> WorkspacePatchSet:
        material = WorkspacePatchSetMaterial(
            patch_set_id=patch_set_id,
            edit_plan=edit_plan,
            target_set=target_set,
            operations=operations,
            expected_changed_paths=expected_changed_paths,
            expected_result=expected_result,
        )
        return cls(
            **material.model_dump(),
            patch_digest=_canonical_digest(material),
        )

    @model_validator(mode="after")
    def _digest_matches_material(self) -> WorkspacePatchSet:
        material = WorkspacePatchSetMaterial.model_validate(
            self.model_dump(exclude={"patch_digest"})
        )
        if self.patch_digest != _canonical_digest(material):
            raise ValueError("patch_digest does not match canonical patch material")
        return self


class PatchValidationIssue(RuntimeContract):
    """One stable diagnostic that carries no file or patch content."""

    code: PatchValidationIssueCode
    virtual_path: str | None = None
    operation_id: str | None = None
    hunk_index: int | None = Field(default=None, ge=0)
    related_paths: tuple[str, ...] = ()


class PatchValidationReport(RuntimeContract):
    """Deterministic validation outcome suitable for hashing and replay."""

    valid: bool
    operation_order: tuple[str, ...]
    changed_paths: tuple[str, ...]
    issues: tuple[PatchValidationIssue, ...]

    @model_validator(mode="after")
    def _validity_matches_issues(self) -> PatchValidationReport:
        if self.valid == bool(self.issues):
            raise ValueError("valid must be true exactly when issues is empty")
        return self


class WorkspacePatchSetValidator:
    """Validate patch metadata without reading bytes or mutating workspace state."""

    @classmethod
    def validate(cls, patch_set: WorkspacePatchSet) -> PatchValidationReport:
        issues: list[PatchValidationIssue] = []
        targets = patch_set.target_set.targets
        operations = patch_set.operations

        target_counts = Counter(target.virtual_path for target in targets)
        for path, count in target_counts.items():
            if count > 1:
                issues.append(
                    PatchValidationIssue(
                        code=PatchValidationIssueCode.DUPLICATE_TARGET_PATH,
                        virtual_path=path,
                    )
                )

        operation_id_counts = Counter(
            operation.operation_id for operation in operations
        )
        for operation_id, count in operation_id_counts.items():
            if count > 1:
                issues.append(
                    PatchValidationIssue(
                        code=PatchValidationIssueCode.DUPLICATE_OPERATION_ID,
                        operation_id=operation_id,
                    )
                )

        touched_by: dict[str, list[str]] = defaultdict(list)
        for operation in operations:
            for path in operation.touched_paths:
                touched_by[path].append(operation.operation_id)
        for path, operation_ids in touched_by.items():
            if len(operation_ids) > 1:
                issues.append(
                    PatchValidationIssue(
                        code=PatchValidationIssueCode.PATH_OPERATION_CONFLICT,
                        virtual_path=path,
                        operation_id=min(operation_ids),
                    )
                )

        all_declared_paths = tuple(target.virtual_path for target in targets)
        all_touched_paths = tuple(touched_by)
        all_expected_paths = patch_set.expected_changed_paths
        issues.extend(
            cls._case_collision_issues(
                (*all_declared_paths, *all_touched_paths, *all_expected_paths)
            )
        )

        target_by_path: dict[str, WorkspacePatchTarget] = {}
        for target in sorted(targets, key=lambda item: item.virtual_path):
            target_by_path.setdefault(target.virtual_path, target)

        for path in sorted(set(all_touched_paths) - set(all_declared_paths)):
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.UNDECLARED_TARGET,
                    virtual_path=path,
                )
            )
        for path in sorted(set(all_declared_paths) - set(all_touched_paths)):
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.UNUSED_TARGET,
                    virtual_path=path,
                )
            )

        changed_paths = tuple(sorted(set(all_touched_paths)))
        if (
            len(all_expected_paths) != len(set(all_expected_paths))
            or tuple(sorted(all_expected_paths)) != changed_paths
        ):
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.EXPECTED_PATH_SET_MISMATCH,
                    related_paths=tuple(sorted(set(all_expected_paths))),
                )
            )

        for operation in operations:
            cls._validate_operation_preconditions(
                operation=operation,
                target_by_path=target_by_path,
                issues=issues,
            )
            cls._validate_hunks(operation=operation, issues=issues)

        operation_order = tuple(
            operation.operation_id
            for operation in sorted(
                operations,
                key=lambda item: (
                    item.virtual_path,
                    item.destination_virtual_path or "",
                    item.operation_id,
                ),
            )
        )
        ordered_issues = tuple(sorted(issues, key=cls._issue_sort_key))
        return PatchValidationReport(
            valid=not ordered_issues,
            operation_order=operation_order,
            changed_paths=changed_paths,
            issues=ordered_issues,
        )

    @staticmethod
    def _case_collision_issues(paths: tuple[str, ...]) -> list[PatchValidationIssue]:
        aliases: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            aliases[path.casefold()].add(path)
        return [
            PatchValidationIssue(
                code=PatchValidationIssueCode.CASE_COLLISION,
                virtual_path=ordered[0],
                related_paths=ordered,
            )
            for values in aliases.values()
            if len(values) > 1
            for ordered in (tuple(sorted(values)),)
        ]

    @classmethod
    def _validate_operation_preconditions(
        cls,
        *,
        operation: WorkspacePatchOperation,
        target_by_path: dict[str, WorkspacePatchTarget],
        issues: list[PatchValidationIssue],
    ) -> None:
        cls._validate_one_precondition(
            operation_id=operation.operation_id,
            path=operation.virtual_path,
            actual=operation.precondition,
            expected=target_by_path.get(operation.virtual_path),
            must_be_absent=operation.kind is WorkspacePatchOperationKind.CREATE,
            issues=issues,
        )
        if (
            operation.destination_virtual_path is not None
            and operation.destination_precondition is not None
        ):
            cls._validate_one_precondition(
                operation_id=operation.operation_id,
                path=operation.destination_virtual_path,
                actual=operation.destination_precondition,
                expected=target_by_path.get(operation.destination_virtual_path),
                must_be_absent=True,
                issues=issues,
            )

    @staticmethod
    def _validate_one_precondition(
        *,
        operation_id: str,
        path: str,
        actual: BasePrecondition,
        expected: WorkspacePatchTarget | None,
        must_be_absent: bool,
        issues: list[PatchValidationIssue],
    ) -> None:
        exact = (
            actual.existence is BaseExistence.MUST_NOT_EXIST
            if must_be_absent
            else (
                actual.existence is BaseExistence.MUST_EXIST
                and actual.entry_kind is WorkspaceEntryKind.FILE
                and actual.content_digest is not None
            )
        )
        if not exact:
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.PRECONDITION_NOT_EXACT,
                    virtual_path=path,
                    operation_id=operation_id,
                )
            )
        if expected is not None and actual != expected.precondition:
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.PRECONDITION_MISMATCH,
                    virtual_path=path,
                    operation_id=operation_id,
                )
            )

    @staticmethod
    def _validate_hunks(
        *,
        operation: WorkspacePatchOperation,
        issues: list[PatchValidationIssue],
    ) -> None:
        if operation.kind is not WorkspacePatchOperationKind.HUNKS:
            return
        ordered = tuple(
            sorted(
                enumerate(operation.hunks),
                key=lambda item: (
                    item[1].old_start_byte,
                    item[1].old_end_byte,
                    item[0],
                ),
            )
        )
        if tuple(index for index, _hunk in ordered) != tuple(
            range(len(operation.hunks))
        ):
            issues.append(
                PatchValidationIssue(
                    code=PatchValidationIssueCode.HUNK_ORDER_INVALID,
                    virtual_path=operation.virtual_path,
                    operation_id=operation.operation_id,
                )
            )
        prior_end = -1
        for original_index, hunk in ordered:
            if hunk.old_start_byte < prior_end:
                issues.append(
                    PatchValidationIssue(
                        code=PatchValidationIssueCode.OVERLAPPING_HUNKS,
                        virtual_path=operation.virtual_path,
                        operation_id=operation.operation_id,
                        hunk_index=original_index,
                    )
                )
            prior_end = max(prior_end, hunk.old_end_byte)

    @staticmethod
    def _issue_sort_key(
        issue: PatchValidationIssue,
    ) -> tuple[str, str, str, int, tuple[str, ...]]:
        return (
            issue.code.value,
            issue.virtual_path or "",
            issue.operation_id or "",
            -1 if issue.hunk_index is None else issue.hunk_index,
            issue.related_paths,
        )


__all__ = (
    "MAX_CHANGED_CONTENT_BYTES",
    "MAX_CHANGED_FILE_BYTES",
    "MAX_HUNKS_PER_OPERATION",
    "MAX_PATCH_BYTES",
    "MAX_PATCH_HUNKS",
    "MAX_PATCH_OPERATIONS",
    "MAX_PATCH_PATH_BYTES",
    "MAX_PATCH_PATH_DEPTH",
    "MAX_PATCH_TARGETS",
    "PatchValidationIssue",
    "PatchValidationIssueCode",
    "PatchValidationReport",
    "WorkspaceEditPlanBinding",
    "WorkspacePatchExpectedPath",
    "WorkspacePatchHunk",
    "WorkspacePatchOperation",
    "WorkspacePatchOperationKind",
    "WorkspacePatchSet",
    "WorkspacePatchSetMaterial",
    "WorkspacePatchSetValidator",
    "WorkspacePatchTarget",
    "WorkspacePatchTargetSet",
    "WorkspacePatchTargetSetMaterial",
)
