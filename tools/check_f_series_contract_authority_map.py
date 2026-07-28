"""Validate the versioned F1-F12 cross-authority contract map.

The map freezes the primary authority for every F-series record and event while
keeping supporting and consumer authorities explicit.  Validation is standard
library only so the guard can run from a service virtualenv or in pre-commit.

Usage:
    python tools/check_f_series_contract_authority_map.py
    python tools/check_f_series_contract_authority_map.py --write-digests
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP_PATH = (
    REPO_ROOT
    / "docs"
    / "plan"
    / "agent-runtime-quality"
    / "F1-F12-CONTRACT-AUTHORITY-MAP.v1.json"
)

SCHEMA_VERSION = "1.0.0"
MAP_ID = "agent-runtime-quality.f1-f12.contract-authority-map"
FEATURES = tuple(f"F{index}" for index in range(1, 13))
SCOPES = (*FEATURES, "integration")
AUTHORITIES = frozenset({"ai-backend", "backend", "desktop"})
KINDS = frozenset({"record", "event"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FULL_DOCUMENT_DIGEST_SCOPE = "full_document"
INTEGRATION_DIGEST_SCOPE = "document_without_execution_checklist"
_EXECUTION_CHECKLIST_START = "## 1.1 Ordered execution checklist"
_EXECUTION_CHECKLIST_END = "## 2. Problem statement"

EXPECTED_SOURCE_PATHS = {
    "integration": (
        "docs/plan/agent-runtime-quality/"
        "IMPLEMENTATION-PRD-F1-F12-PRODUCTION-INTEGRATION.md"
    ),
    "F1": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F1-harness-observability-evaluation-promotion.md"
    ),
    "F2": (
        "docs/plan/agent-runtime-quality/prds/PRD-AR-F2-cache-aware-prompt-assembly.md"
    ),
    "F3": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F3-policy-aware-capability-discovery.md"
    ),
    "F4": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F4-task-aware-tool-use-controller.md"
    ),
    "F5": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F5-context-budgeting-compression-evidence-recall.md"
    ),
    "F6": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F6-capability-concurrency-safe-batching.md"
    ),
    "F7": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F7-governed-dataflow-programmatic-tool-calling.md"
    ),
    "F8": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F8-mcp-control-plane-freshness-session-reuse.md"
    ),
    "F9": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F9-parallel-delegation-quality-controller.md"
    ),
    "F10": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F10-model-invocation-reliability-routing.md"
    ),
    "F11": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F11-workspace-edit-planning-patch-validation.md"
    ),
    "F12": (
        "docs/plan/agent-runtime-quality/prds/"
        "PRD-AR-F12-evidence-aware-answer-synthesis-verification.md"
    ),
}

EXPECTED_SOURCE_DIGEST_SCOPES = {
    source_name: (
        INTEGRATION_DIGEST_SCOPE
        if source_name == "integration"
        else FULL_DOCUMENT_DIGEST_SCOPE
    )
    for source_name in EXPECTED_SOURCE_PATHS
}

EXPECTED_INVENTORY: dict[str, dict[str, frozenset[str]]] = {
    "F1": {
        "record": frozenset(
            {
                "EvaluationCase",
                "EvaluationResult",
                "HarnessVariant",
                "PromotionDecision",
                "TrajectoryManifest",
            }
        ),
        "event": frozenset(
            {
                "harness.evaluation.completed.v1",
                "harness.evaluation.started.v1",
                "harness.promotion.decided.v1",
            }
        ),
    },
    "F2": {
        "record": frozenset(
            {"PromptAssemblyPlan", "PromptFragment", "ProviderCacheStrategy"}
        ),
        "event": frozenset({"prompt.assembled.v1", "prompt.cache_observed.v1"}),
    },
    "F3": {
        "record": frozenset(
            {
                "CapabilityCandidate",
                "CapabilityCatalogRevision",
                "CapabilityIndexEntry",
                "CapabilityInvocation",
            }
        ),
        "event": frozenset(
            {
                "capability.discovery.described.v1",
                "capability.discovery.searched.v1",
            }
        ),
    },
    "F4": {
        "record": frozenset(
            {"RunToolPlan", "TaskPolicyProfile", "ToolUseFeedback", "ToolUseIntent"}
        ),
        "event": frozenset(
            {
                "tool_policy.budget_exhausted.v1",
                "tool_policy.feedback.v1",
                "tool_policy.intent_recorded.v1",
                "tool_policy.profile_selected.v1",
            }
        ),
    },
    "F5": {
        "record": frozenset(
            {
                "ContextCandidate",
                "ContextPlan",
                "ContextRepresentation",
                "EvidenceSpanRequest",
            }
        ),
        "event": frozenset(
            {
                "context.content.compressed.v1",
                "context.evidence.read.v1",
                "context.item.omitted.v1",
                "context.plan.created.v1",
            }
        ),
    },
    "F6": {
        "record": frozenset(
            {"BatchResult", "BatchSegment", "ConcurrencyPolicy", "OperationBatch"}
        ),
        "event": frozenset(
            {
                "operation.batch.completed.v1",
                "operation.batch.planned.v1",
                "operation.batch.started.v1",
            }
        ),
    },
    "F7": {
        "record": frozenset(
            {
                "DataflowLimits",
                "DataflowNode",
                "DataflowPlan",
                "DataflowResult",
                "EffectBatchManifest",
            }
        ),
        "event": frozenset(
            {
                "dataflow.effect_manifest.proposed.v1",
                "dataflow.execution.completed.v1",
                "dataflow.execution.started.v1",
                "dataflow.plan.validated.v1",
            }
        ),
    },
    "F8": {
        "record": frozenset(
            {
                "McpClientLease",
                "McpDescriptorRevision",
                "McpRevisionFeedItem",
                "McpRevisionPage",
                "McpSessionLease",
            }
        ),
        "event": frozenset(),
    },
    "F9": {
        "record": frozenset(
            {
                "DelegationBudget",
                "DelegationContextPacket",
                "DelegationRequest",
                "DelegationResult",
                "DelegationVerification",
            }
        ),
        "event": frozenset(
            {
                "subagent.delegation.admitted.v1",
                "subagent.delegation.rejected.v1",
                "subagent.result.verified.v1",
            }
        ),
    },
    "F10": {
        "record": frozenset(
            {
                "ModelAttempt",
                "ModelDeploymentDescriptor",
                "ModelInvocation",
                "ModelInvocationRequirements",
                "ModelRoutePlan",
            }
        ),
        "event": frozenset(
            {
                "model.attempt.failed.v1",
                "model.attempt.started.v1",
                "model.invocation.completed.v1",
                "model.invocation.planned.v1",
                "model.invocation.rerouted.v1",
            }
        ),
    },
    "F11": {
        "record": frozenset(
            {
                "ValidationProfile",
                "ValidationReport",
                "WorkspaceEditAttempt",
                "WorkspaceEditPlan",
                "WorkspacePatchHunk",
                "WorkspacePatchOperation",
                "WorkspacePatchSet",
                "WorkspaceTarget",
            }
        ),
        "event": frozenset(
            {
                "workspace.edit_blocked.v1",
                "workspace.edit_plan.created.v1",
                "workspace.edit_ready_for_review.v1",
                "workspace.patch_set.applied.v1",
                "workspace.patch_set.validated.v1",
                "workspace.repair.requested.v1",
                "workspace.validation.completed.v1",
                "workspace.validation.started.v1",
            }
        ),
    },
    "F12": {
        "record": frozenset(
            {
                "AnswerClaim",
                "AnswerEnvelope",
                "AnswerEvidenceBinding",
                "AnswerRepairAttempt",
                "AnswerRequirement",
                "AnswerRequirementLedger",
                "AnswerRequirementResult",
                "AnswerVerificationFailure",
                "AnswerVerificationReport",
                "EvidenceConflictResolution",
            }
        ),
        "event": frozenset(
            {
                "answer.degraded.v1",
                "answer.finalized.v1",
                "answer.repair.completed.v1",
                "answer.repair.requested.v1",
                "answer.requirements.compiled.v1",
                "answer.verification.completed.v1",
                "answer.verification.started.v1",
            }
        ),
    },
    "integration": {
        "record": frozenset({"RunControlDecision", "RunControlSnapshot"}),
        "event": frozenset(
            {
                "answer.finalization.v1",
                "model.attempt.v1",
                "quality.control_bound.v1",
                "quality.decision.v1",
            }
        ),
    },
}

EXPECTED_AUTHORITY_ROOTS = {
    "ai-backend": "services/ai-backend",
    "backend": "services/backend",
    "desktop": "apps/desktop",
}

EXPECTED_CONSTRAINT_OWNERS = {
    "host_workspace_mutation": "desktop",
    "mcp_registration_oauth_credentials_remote_transport": "backend",
    "run_message_event_usage_state": "ai-backend",
}


class DuplicateJsonKeyError(ValueError):
    """Raised when JSON contains a duplicate object key."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_map(path: Path = DEFAULT_MAP_PATH) -> dict[str, Any]:
    """Load a contract map while rejecting JSON duplicate-key ambiguity."""

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError("contract map root must be a JSON object")
    return value


def canonical_payload(document: dict[str, Any]) -> bytes:
    """Return canonical JSON bytes with only the self-referential digest omitted."""

    payload = deepcopy(document)
    integrity = payload.get("integrity")
    if isinstance(integrity, dict):
        integrity.pop("digest", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def contract_map_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(document)).hexdigest()


def source_document_digest(source_name: str, source_bytes: bytes) -> str:
    """Hash contract-bearing source bytes without hashing checklist progress.

    The implementation PRD is intentionally updated after every completed
    slice. Its execution checklist is operational state, not an authority
    contract. Excluding only that bounded section avoids meaningless digest
    churn while every architectural change elsewhere in the document still
    invalidates the map.
    """

    if source_name != "integration":
        digest_bytes = source_bytes
    else:
        source_text = source_bytes.decode("utf-8")
        prefix, start, remainder = source_text.partition(_EXECUTION_CHECKLIST_START)
        checklist, end, suffix = remainder.partition(_EXECUTION_CHECKLIST_END)
        if not start or not end or not checklist:
            raise ValueError(
                "integration PRD must retain the bounded ordered execution checklist"
            )
        digest_bytes = (prefix + _EXECUTION_CHECKLIST_END + suffix).encode("utf-8")
    return hashlib.sha256(digest_bytes).hexdigest()


def _path_error(
    raw_path: object,
    *,
    repo_root: Path,
    expect_directory: bool,
) -> str | None:
    if not isinstance(raw_path, str) or not raw_path:
        return "must be a non-empty repo-relative POSIX path"
    posix_path = PurePosixPath(raw_path)
    if posix_path.is_absolute() or ".." in posix_path.parts or "\\" in raw_path:
        return "must not be absolute, traverse parents, or use backslashes"
    resolved = (repo_root / raw_path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return "resolves outside the repository"
    if expect_directory and not resolved.is_dir():
        return "does not name an existing directory"
    if not expect_directory and not resolved.is_file():
        return "does not name an existing file"
    return None


def _authority_list_errors(
    value: object,
    *,
    label: str,
    primary: object,
) -> list[str]:
    if not isinstance(value, list):
        return [f"{label} must be a list"]
    errors: list[str] = []
    if any(not isinstance(authority, str) for authority in value):
        return [f"{label} must contain authority strings only"]
    authorities = list(value)
    unknown = sorted(set(authorities) - AUTHORITIES)
    if unknown:
        errors.append(f"{label} contains invalid authorities: {unknown}")
    if len(authorities) != len(set(authorities)):
        errors.append(f"{label} contains duplicate authorities")
    if authorities != sorted(authorities):
        errors.append(f"{label} must be sorted")
    if primary in authorities:
        errors.append(f"{label} must not contain primary_authority")
    return errors


def _inventory_from_contract_groups(
    contract_groups: list[object],
) -> tuple[dict[str, dict[str, set[str]]], list[str]]:
    inventory = {scope: {kind: set() for kind in KINDS} for scope in SCOPES}
    errors: list[str] = []
    seen_ids: dict[str, int] = {}
    seen_identities: dict[tuple[str, str], tuple[int, str]] = {}

    for index, raw_group in enumerate(contract_groups):
        label = f"contract_groups[{index}]"
        if not isinstance(raw_group, dict):
            errors.append(f"{label} must be an object")
            continue
        required = {
            "group_id",
            "scope",
            "kind",
            "names",
            "primary_authority",
            "supporting_authorities",
            "consumer_authorities",
            "sources",
        }
        missing = sorted(required - raw_group.keys())
        extra = sorted(raw_group.keys() - required)
        if missing:
            errors.append(f"{label} missing fields: {missing}")
        if extra:
            errors.append(f"{label} has unknown fields: {extra}")

        group_id = raw_group.get("group_id")
        scope = raw_group.get("scope")
        kind = raw_group.get("kind")
        names = raw_group.get("names")
        primary = raw_group.get("primary_authority")

        if not isinstance(group_id, str) or not group_id:
            errors.append(f"{label}.group_id must be a non-empty string")
        elif group_id in seen_ids:
            errors.append(
                f"{label} duplicates group_id at "
                f"contract_groups[{seen_ids[group_id]}]: {group_id}"
            )
        else:
            seen_ids[group_id] = index

        if scope not in SCOPES:
            errors.append(f"{label}.scope is invalid: {scope!r}")
        if kind not in KINDS:
            errors.append(f"{label}.kind is invalid: {kind!r}")
        if not isinstance(names, list) or not names:
            errors.append(f"{label}.names must be a non-empty list")
            names = []
        elif any(not isinstance(name, str) or not name for name in names):
            errors.append(f"{label}.names must contain non-empty strings only")
            names = []
        elif names != sorted(names):
            errors.append(f"{label}.names must be sorted")
        elif len(names) != len(set(names)):
            errors.append(f"{label}.names contains duplicate contracts")
        if primary not in AUTHORITIES:
            errors.append(f"{label}.primary_authority is invalid: {primary!r}")

        if isinstance(kind, str):
            for name in names:
                identity = (kind, name)
                previous = seen_identities.get(identity)
                if previous is not None:
                    previous_index, previous_primary = previous
                    errors.append(
                        f"{label} creates duplicate primary ownership for "
                        f"{kind} {name!r}; contract_groups[{previous_index}] "
                        f"already assigns {previous_primary!r}"
                    )
                else:
                    seen_identities[identity] = (index, str(primary))

        supporting = raw_group.get("supporting_authorities")
        consumers = raw_group.get("consumer_authorities")
        errors.extend(
            _authority_list_errors(
                supporting,
                label=f"{label}.supporting_authorities",
                primary=primary,
            )
        )
        errors.extend(
            _authority_list_errors(
                consumers,
                label=f"{label}.consumer_authorities",
                primary=primary,
            )
        )
        if isinstance(supporting, list) and isinstance(consumers, list):
            overlap = sorted(set(supporting) & set(consumers))
            if overlap:
                errors.append(
                    f"{label} does not distinguish supporting and consumer "
                    f"authorities: {overlap}"
                )

        sources = raw_group.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{label}.sources must be a non-empty list")
        else:
            for source_index, source in enumerate(sources):
                source_label = f"{label}.sources[{source_index}]"
                if not isinstance(source, dict):
                    errors.append(f"{source_label} must be an object")
                    continue
                if set(source) != {"document", "section"}:
                    errors.append(
                        f"{source_label} must contain only document and section"
                    )
                    continue
                document_name = source.get("document")
                section = source.get("section")
                if document_name not in EXPECTED_SOURCE_PATHS:
                    errors.append(
                        f"{source_label}.document is invalid: {document_name!r}"
                    )
                if not isinstance(section, str) or not section:
                    errors.append(f"{source_label}.section must be a non-empty string")

        if scope in inventory and kind in KINDS:
            inventory[scope][kind].update(names)

        expected_id_prefix = None
        if scope in SCOPES and kind in KINDS and isinstance(primary, str):
            expected_id_prefix = f"{str(scope).lower()}.{kind}.{primary}"
        if (
            expected_id_prefix is not None
            and isinstance(group_id, str)
            and group_id != expected_id_prefix
            and not group_id.startswith(f"{expected_id_prefix}.")
        ):
            errors.append(f"{label}.group_id must start with {expected_id_prefix!r}")

    return inventory, errors


def validate_map(
    document: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[str, ...]:
    """Return every deterministic validation failure for ``document``."""

    errors: list[str] = []
    required_root = {
        "schema_version",
        "map_id",
        "description",
        "authority_semantics",
        "authorities",
        "source_documents",
        "authority_constraints",
        "contract_groups",
        "integrity",
    }
    missing_root = sorted(required_root - document.keys())
    extra_root = sorted(document.keys() - required_root)
    if missing_root:
        errors.append(f"root missing fields: {missing_root}")
    if extra_root:
        errors.append(f"root has unknown fields: {extra_root}")
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION!r}, "
            f"got {document.get('schema_version')!r}"
        )
    if document.get("map_id") != MAP_ID:
        errors.append(f"map_id must be {MAP_ID!r}")
    if not isinstance(document.get("description"), str):
        errors.append("description must be a string")

    semantics = document.get("authority_semantics")
    if not isinstance(semantics, dict) or set(semantics) != {
        "primary",
        "supporting",
        "consumer",
    }:
        errors.append(
            "authority_semantics must define exactly primary, supporting, and consumer"
        )

    authorities = document.get("authorities")
    if not isinstance(authorities, dict):
        errors.append("authorities must be an object")
    else:
        if set(authorities) != AUTHORITIES:
            errors.append(
                "authorities must contain exactly ai-backend, backend, and desktop"
            )
        for authority, expected_root in EXPECTED_AUTHORITY_ROOTS.items():
            value = authorities.get(authority)
            if not isinstance(value, dict) or set(value) != {
                "root_path",
                "description",
            }:
                errors.append(
                    f"authorities.{authority} must define root_path and description"
                )
                continue
            root_path = value.get("root_path")
            if root_path != expected_root:
                errors.append(
                    f"authorities.{authority}.root_path must be {expected_root!r}"
                )
            path_error = _path_error(
                root_path,
                repo_root=repo_root,
                expect_directory=True,
            )
            if path_error:
                errors.append(
                    f"authorities.{authority}.root_path {path_error}: {root_path!r}"
                )

    source_documents = document.get("source_documents")
    source_text: dict[str, str] = {}
    if not isinstance(source_documents, dict):
        errors.append("source_documents must be an object")
    else:
        if set(source_documents) != set(EXPECTED_SOURCE_PATHS):
            errors.append(
                "source_documents must contain exactly integration and F1-F12"
            )
        for source_name, expected_path in EXPECTED_SOURCE_PATHS.items():
            source = source_documents.get(source_name)
            if not isinstance(source, dict) or set(source) != {
                "path",
                "sha256",
                "digest_scope",
            }:
                errors.append(
                    f"source_documents.{source_name} must define path, sha256, "
                    "and digest_scope"
                )
                continue
            raw_path = source.get("path")
            if raw_path != expected_path:
                errors.append(
                    f"source_documents.{source_name}.path must be {expected_path!r}"
                )
            path_error = _path_error(
                raw_path,
                repo_root=repo_root,
                expect_directory=False,
            )
            if path_error:
                errors.append(
                    f"source_documents.{source_name}.path {path_error}: {raw_path!r}"
                )
                continue
            source_path = repo_root / str(raw_path)
            source_bytes = source_path.read_bytes()
            source_text[source_name] = source_bytes.decode("utf-8")
            digest_scope = source.get("digest_scope")
            expected_scope = EXPECTED_SOURCE_DIGEST_SCOPES[source_name]
            if digest_scope != expected_scope:
                errors.append(
                    f"source_documents.{source_name}.digest_scope must be "
                    f"{expected_scope!r}"
                )
            expected_digest = source.get("sha256")
            try:
                actual_digest = source_document_digest(source_name, source_bytes)
            except (UnicodeError, ValueError) as exc:
                errors.append(
                    f"source_documents.{source_name}.sha256 cannot be computed: {exc}"
                )
                continue
            if not isinstance(expected_digest, str) or not SHA256_PATTERN.fullmatch(
                expected_digest
            ):
                errors.append(
                    f"source_documents.{source_name}.sha256 must be lowercase SHA-256"
                )
            elif expected_digest != actual_digest:
                errors.append(
                    f"source_documents.{source_name}.sha256 mismatch: "
                    f"expected {expected_digest}, actual {actual_digest}"
                )

    constraints = document.get("authority_constraints")
    if not isinstance(constraints, list):
        errors.append("authority_constraints must be a list")
    else:
        seen_concerns: dict[str, int] = {}
        actual_owners: dict[str, object] = {}
        for index, constraint in enumerate(constraints):
            label = f"authority_constraints[{index}]"
            if not isinstance(constraint, dict):
                errors.append(f"{label} must be an object")
                continue
            required = {
                "concern",
                "primary_authority",
                "supporting_authorities",
                "consumer_authorities",
                "source",
            }
            if set(constraint) != required:
                errors.append(f"{label} must contain exactly {sorted(required)}")
                continue
            concern = constraint.get("concern")
            primary = constraint.get("primary_authority")
            if not isinstance(concern, str) or not concern:
                errors.append(f"{label}.concern must be a non-empty string")
            elif concern in seen_concerns:
                errors.append(
                    f"{label} duplicates concern at "
                    f"authority_constraints[{seen_concerns[concern]}]: {concern}"
                )
            else:
                seen_concerns[concern] = index
                actual_owners[concern] = primary
            if primary not in AUTHORITIES:
                errors.append(f"{label}.primary_authority is invalid: {primary!r}")
            supporting = constraint.get("supporting_authorities")
            consumers = constraint.get("consumer_authorities")
            errors.extend(
                _authority_list_errors(
                    supporting,
                    label=f"{label}.supporting_authorities",
                    primary=primary,
                )
            )
            errors.extend(
                _authority_list_errors(
                    consumers,
                    label=f"{label}.consumer_authorities",
                    primary=primary,
                )
            )
            if isinstance(supporting, list) and isinstance(consumers, list):
                overlap = sorted(set(supporting) & set(consumers))
                if overlap:
                    errors.append(
                        f"{label} does not distinguish supporting and consumer "
                        f"authorities: {overlap}"
                    )
            source = constraint.get("source")
            if (
                not isinstance(source, dict)
                or set(source) != {"document", "section"}
                or source.get("document") not in EXPECTED_SOURCE_PATHS
                or not isinstance(source.get("section"), str)
            ):
                errors.append(f"{label}.source is invalid")

        if actual_owners != EXPECTED_CONSTRAINT_OWNERS:
            errors.append(
                "authority_constraints must preserve canonical run state, MCP, "
                "and host-mutation owners"
            )

    contract_groups = document.get("contract_groups")
    if not isinstance(contract_groups, list):
        errors.append("contract_groups must be a list")
        contract_groups = []
    inventory, contract_errors = _inventory_from_contract_groups(contract_groups)
    errors.extend(contract_errors)

    for scope, expected_by_kind in EXPECTED_INVENTORY.items():
        for kind, expected_names in expected_by_kind.items():
            actual_names = inventory[scope][kind]
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            if missing:
                errors.append(f"{scope} missing {kind} coverage: {', '.join(missing)}")
            if extra:
                errors.append(
                    f"{scope} has unexpected {kind} contracts: {', '.join(extra)}"
                )

    for index, raw_group in enumerate(contract_groups):
        if not isinstance(raw_group, dict):
            continue
        for source_index, source in enumerate(raw_group.get("sources", [])):
            if not isinstance(source, dict):
                continue
            document_name = source.get("document")
            section = source.get("section")
            if (
                document_name in source_text
                and isinstance(section, str)
                and section
                and section not in source_text[document_name]
            ):
                errors.append(
                    f"contract_groups[{index}].sources[{source_index}].section "
                    f"not found in {document_name}: {section!r}"
                )

    integrity = document.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != {
        "algorithm",
        "canonicalization",
        "digest",
    }:
        errors.append(
            "integrity must define exactly algorithm, canonicalization, and digest"
        )
    else:
        if integrity.get("algorithm") != "sha256":
            errors.append("integrity.algorithm must be 'sha256'")
        if integrity.get("canonicalization") != (
            "UTF-8 JSON with sorted keys and compact separators; omit integrity.digest"
        ):
            errors.append("integrity.canonicalization is unsupported")
        expected_digest = integrity.get("digest")
        actual_digest = contract_map_digest(document)
        if not isinstance(expected_digest, str) or not SHA256_PATTERN.fullmatch(
            expected_digest
        ):
            errors.append("integrity.digest must be lowercase SHA-256")
        elif expected_digest != actual_digest:
            errors.append(
                f"integrity.digest mismatch (map tampering or stale digest): "
                f"expected {expected_digest}, actual {actual_digest}"
            )

    return tuple(errors)


def _replace_object_string_field(
    raw_text: str,
    *,
    object_key: str,
    field_name: str,
    field_value: str,
) -> str:
    """Replace one string field while preserving reviewed JSON formatting."""

    object_marker = f'"{object_key}": {{'
    object_start = raw_text.find(object_marker)
    if object_start < 0:
        raise ValueError(f"object {object_key!r} is missing")
    object_end = raw_text.find("}", object_start)
    if object_end < 0:
        raise ValueError(f"object {object_key!r} is not bounded")
    object_text = raw_text[object_start:object_end]
    field_pattern = re.compile(
        rf'("{re.escape(field_name)}"\s*:\s*")[^"]*(")',
    )
    updated_object, replacements = field_pattern.subn(
        rf"\g<1>{field_value}\g<2>",
        object_text,
        count=1,
    )
    if replacements != 1:
        raise ValueError(
            f"object {object_key!r} must contain one {field_name!r} string field"
        )
    return raw_text[:object_start] + updated_object + raw_text[object_end:]


def write_digests(path: Path, *, repo_root: Path = REPO_ROOT) -> None:
    """Refresh digests without rewriting human-reviewed JSON formatting."""

    raw_text = path.read_text(encoding="utf-8")
    document = load_map(path)
    source_documents = document.get("source_documents")
    if not isinstance(source_documents, dict):
        raise ValueError("source_documents must be an object before writing digests")
    for source_name, expected_path in EXPECTED_SOURCE_PATHS.items():
        source = source_documents.get(source_name)
        if not isinstance(source, dict):
            raise ValueError(
                f"source_documents.{source_name} must be an object before "
                "writing digests"
            )
        if source.get("digest_scope") != EXPECTED_SOURCE_DIGEST_SCOPES[source_name]:
            raise ValueError(
                f"source_documents.{source_name}.digest_scope must be reviewed "
                "before writing digests"
            )
        digest = source_document_digest(
            source_name,
            (repo_root / expected_path).read_bytes(),
        )
        raw_text = _replace_object_string_field(
            raw_text,
            object_key=source_name,
            field_name="sha256",
            field_value=digest,
        )

    updated_document = json.loads(
        raw_text,
        object_pairs_hook=_reject_duplicate_keys,
    )
    integrity = updated_document.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity must be an object before writing its digest")
    raw_text = _replace_object_string_field(
        raw_text,
        object_key="integrity",
        field_name="digest",
        field_value=contract_map_digest(updated_document),
    )
    path.write_text(raw_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_f_series_contract_authority_map")
    parser.add_argument(
        "--map",
        type=Path,
        default=DEFAULT_MAP_PATH,
        help="Contract-map JSON path (default: repository F1-F12 map).",
    )
    parser.add_argument(
        "--write-digests",
        action="store_true",
        help="Rewrite source and map digests after a reviewed contract change.",
    )
    args = parser.parse_args(argv)

    try:
        if args.write_digests:
            write_digests(args.map)
        document = load_map(args.map)
        errors = validate_map(document)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"FAIL: cannot load contract authority map: {exc}\n")
        return 1

    if errors:
        sys.stderr.write("FAIL: F1-F12 contract authority map is invalid:\n")
        for error in errors:
            sys.stderr.write(f"  - {error}\n")
        return 1

    record_count = sum(
        len(group["names"])
        for group in document["contract_groups"]
        if group["kind"] == "record"
    )
    event_count = sum(
        len(group["names"])
        for group in document["contract_groups"]
        if group["kind"] == "event"
    )
    relative_map = args.map
    try:
        relative_map = args.map.resolve().relative_to(REPO_ROOT)
    except ValueError:
        pass
    sys.stdout.write(
        f"OK: {relative_map} assigns {record_count} records and "
        f"{event_count} events across F1-F12\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
