"""Pure contracts and deterministic admission validation for workspace patch plans.

This module does not apply patches or resolve content references.  It validates the
closed, metadata-only plan that an eventual C1 overlay transaction may consume.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from enum import StrEnum
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
        return normalize_virtual_path(value)


class WorkspacePatchTargetSet(RuntimeContract):
    """Frozen target inventory for one overlay manifest revision."""

    base_manifest_revision: int = Field(ge=0)
    targets: tuple[WorkspacePatchTarget, ...] = Field(min_length=1)


class WorkspacePatchOperation(RuntimeContract):
    """One declarative mutation in a bounded patch set."""

    operation_id: str
    kind: WorkspacePatchOperationKind
    virtual_path: str
    precondition: BasePrecondition
    destination_virtual_path: str | None = None
    destination_precondition: BasePrecondition | None = None
    content_ref: str | None = None
    hunks: tuple[WorkspacePatchHunk, ...] = ()

    @field_validator("operation_id")
    @classmethod
    def _operation_id_is_safe(cls, value: str) -> str:
        if _OPERATION_ID.fullmatch(value) is None:
            raise ValueError("operation_id has an invalid format")
        return value

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_virtual_path(value)

    @field_validator("destination_virtual_path")
    @classmethod
    def _canonical_destination(cls, value: str | None) -> str | None:
        return normalize_virtual_path(value) if value is not None else None

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


class WorkspacePatchSet(RuntimeContract):
    """One target-bound, declarative multi-path patch proposal."""

    patch_set_id: str = Field(min_length=1, max_length=128)
    target_set: WorkspacePatchTargetSet
    operations: tuple[WorkspacePatchOperation, ...] = Field(min_length=1)
    expected_changed_paths: tuple[str, ...] = Field(min_length=1)

    @field_validator("expected_changed_paths")
    @classmethod
    def _canonical_expected_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_virtual_path(path) for path in value)


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
    "PatchValidationIssue",
    "PatchValidationIssueCode",
    "PatchValidationReport",
    "WorkspacePatchHunk",
    "WorkspacePatchOperation",
    "WorkspacePatchOperationKind",
    "WorkspacePatchSet",
    "WorkspacePatchSetValidator",
    "WorkspacePatchTarget",
    "WorkspacePatchTargetSet",
)
