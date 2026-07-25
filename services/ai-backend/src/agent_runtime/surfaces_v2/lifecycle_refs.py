"""Pure lifecycle-reference ownership and ledger enumeration (PRD-E1 D9).

Deletion, retention, legal-hold, and export work must never guess which
subsystem owns a reference.  This module is the deliberately small, pure
launch-gate for that later work:

* a closed registry maps every currently shipped v2.1 reference scheme to one
  lifecycle owner;
* a strict parser accepts only canonical logical references (never host paths,
  URLs, query strings, inline data, or decoded traversal);
* the enumerator reads only declared Work Ledger reference fields and produces
  a typed graph of run/event/entity/reference edges; and
* any unknown or malformed reference rejects the entire enumeration with safe,
  structured diagnostics.  Diagnostics intentionally omit raw references,
  bodies, paths, secrets, and exception text.

It has no persistence, resolver, or deletion dependency.  A future lifecycle
coordinator can use the graph as its complete precondition before it performs
any retention or cascade action.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from pydantic import Field

from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    ArtifactEffectFormatError,
    ArtifactIdCodec,
    EffectReceiptRefCodec,
    OperationArgsRefCodec,
    OperationIdCodec,
    ProposalUriCodec,
    WorkspaceTargetRefCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)


class LifecycleReferenceOwner(StrEnum):
    """The one subsystem authoritative for a registered reference scheme."""

    ARTIFACT_REPOSITORY = "artifact_repository"
    OPERATION_GATEWAY = "operation_gateway"
    SURFACE_PRESENTATION = "surface_presentation"
    EFFECT_STAGE = "effect_stage"
    WORKSPACE_AUTHORITY = "workspace_authority"
    BROWSER_AUTHORITY = "browser_authority"
    SANDBOX_RUNTIME = "sandbox_runtime"
    RUNTIME_EVENT_STORE = "runtime_event_store"
    POLICY_ENGINE = "policy_engine"
    IDENTITY = "identity"
    RECEIPT_LIFECYCLE = "receipt_lifecycle"
    LEGACY_STAGE = "legacy_stage"


class LifecycleReferenceScheme(StrEnum):
    """Canonical scheme names currently emitted or consumed by v2.1 code."""

    ACTIVITY = "activity"
    ARTIFACT = "artifact"
    ARTIFACT_BLOB = "artifact-blob"
    ARTIFACT_CODE_SURFACE = "artifact-code"
    ARTIFACT_DATASET_SURFACE = "artifact-dataset"
    ARTIFACT_DOCUMENT_SURFACE = "artifact-document"
    ARTIFACT_FILE_SURFACE = "artifact-file"
    BARE_SURFACE = "bare_surface"
    BROWSER_PAGE = "browser-page"
    BROWSER_PLAN = "browser-plan"
    BROWSER_PRECONDITION = "browser-precondition"
    BROWSER_PREPARED = "browser-prepared"
    BROWSER_RECEIPT = "browser-receipt"
    BROWSER_SESSION = "browser-session"
    BROWSER_TARGET = "browser-target"
    CALL = "call"
    CHAT = "chat"
    COMMIT = "commit"
    DASHBOARD_SURFACE = "dashboard"
    DOC_SURFACE = "doc"
    DRAFT = "draft"
    EFFECT_STAGE_SURFACE = "effect-stage"
    EVENT_SURFACE = "event"
    FILE_SURFACE = "file"
    FORM_SURFACE = "form"
    LEDGER = "ledger"
    MCP_TARGET = "mcp-target"
    MESSAGE = "message"
    MESSAGE_SURFACE = "message_surface"
    OPERATION = "operation"
    PAYLOAD = "payload"
    POLICY = "policy"
    PREPARED = "prepared"
    PRINCIPAL = "principal"
    PROPOSAL = "proposal"
    RECEIPT = "receipt"
    RECORD_SURFACE = "record"
    ROWSET_TARGET = "rowset-target"
    SANDBOX = "sandbox"
    SPEC = "spec"
    STAGE = "stage"
    TABLE_SURFACE = "table"
    TIMELINE_SURFACE = "timeline"
    WORKSPACE_MATERIAL = "workspace-material"
    WORKSPACE_PRECONDITION = "workspace-precondition"
    WORKSPACE_PREPARED = "workspace-prepared"
    WORKSPACE_RECEIPT = "workspace-receipt"
    WORKSPACE_TARGET = "workspace-target"
    BOARD_SURFACE = "board"


class LifecycleNodeKind(StrEnum):
    """Node families a future lifecycle coordinator can retain or hold."""

    RUN = "run"
    EVENT = "event"
    OPERATION = "operation"
    ARTIFACT = "artifact"
    EFFECT_STAGE = "effect_stage"
    SURFACE = "surface"
    GATE = "gate"
    CALL = "call"
    REFERENCE = "reference"


class LifecycleEdgeKind(StrEnum):
    """Directed graph relationships emitted by the closed ledger fold."""

    CONTAINS_EVENT = "contains_event"
    IDENTIFIES = "identifies"
    REFERENCES = "references"


class LifecycleReferenceField(StrEnum):
    """Closed set of Work Ledger fields allowed to carry a lifecycle ref."""

    ACTOR_REF = "actor_ref"
    AUTHOR_REF = "author_ref"
    CONNECTOR_RECEIPT_REF = "connector_receipt_ref"
    CONTENT_REF = "content_ref"
    DIFF_REF = "diff_ref"
    FOLD_REF = "fold_ref"
    OWNER_REF = "owner_ref"
    PAYLOAD_REF = "payload_ref"
    POLICY_SNAPSHOT_REF = "policy_snapshot_ref"
    PRECONDITION_REF = "precondition_ref"
    PROPOSAL_CONTENT_REF = "proposal_content_ref"
    PROPOSAL_REF = "proposal_ref"
    RECEIPT_REF = "receipt_ref"
    RESULT_REF = "result_ref"
    SAFE_DIFF_REF = "safe_diff_ref"
    SAFE_SUMMARY_REF = "safe_summary_ref"
    SOURCE_REF = "source_ref"
    SPEC_REF = "spec_ref"
    TARGET_REF = "target_ref"


class LifecycleDiagnosticCode(StrEnum):
    """Redacted failure classifications safe to persist or return to an operator."""

    INVALID_RUN_ID = "invalid_run_id"
    INVALID_LEDGER_EVENT = "invalid_ledger_event"
    INVALID_SEQUENCE = "invalid_sequence"
    DUPLICATE_SEQUENCE = "duplicate_sequence"
    UNKNOWN_SCHEME = "unknown_scheme"
    MALFORMED_REFERENCE = "malformed_reference"
    FORBIDDEN_REFERENCE = "forbidden_reference"
    UNMAPPED_CONTRACT_REFERENCE = "unmapped_contract_reference"
    DUPLICATE_SCHEME_OWNER = "duplicate_scheme_owner"


class LifecycleReferenceRegistration(RuntimeContract):
    """A canonical scheme's lifecycle owner and a safe executable example."""

    scheme: LifecycleReferenceScheme
    wire_scheme: str = Field(min_length=1, max_length=64)
    owner: LifecycleReferenceOwner
    node_kind: LifecycleNodeKind
    example: str = Field(min_length=1, max_length=2048)
    surface_id_only: bool = False


class LifecycleReference(RuntimeContract):
    """One validated canonical logical reference, never an untrusted path."""

    reference: str = Field(min_length=1, max_length=2048)
    scheme: LifecycleReferenceScheme
    owner: LifecycleReferenceOwner
    node_kind: LifecycleNodeKind
    parts: tuple[str, ...]


class LifecycleGraphNode(RuntimeContract):
    """A stable lifecycle graph node; identifiers are validated logical tokens."""

    node_id: str = Field(min_length=1, max_length=2300)
    kind: LifecycleNodeKind
    identifier: str = Field(min_length=1, max_length=2048)
    owner: LifecycleReferenceOwner | None = None


class LifecycleReferenceEdge(RuntimeContract):
    """One directed ownership/reachability edge in a run's lifecycle graph."""

    from_node_id: str = Field(min_length=1, max_length=2300)
    to_node_id: str = Field(min_length=1, max_length=2300)
    kind: LifecycleEdgeKind
    field: LifecycleReferenceField | None = None


class LifecycleReferenceGraph(RuntimeContract):
    """Complete reference graph for one run, produced only on a clean fold."""

    run_id: str = Field(min_length=1, max_length=256)
    nodes: tuple[LifecycleGraphNode, ...]
    edges: tuple[LifecycleReferenceEdge, ...]


class LifecycleReferenceDiagnostic(RuntimeContract):
    """A deliberately redacted reason lifecycle enumeration was refused.

    Never add a raw ref, exception message, request body, path, token, or
    rendered payload to this contract.  The optional enum fields are all drawn
    from trusted contract vocabulary.
    """

    code: LifecycleDiagnosticCode
    event_type: LedgerEventType | None = None
    sequence_no: int | None = Field(default=None, ge=1)
    field: LifecycleReferenceField | None = None
    scheme: LifecycleReferenceScheme | None = None


class LifecycleReferenceGraphError(ValueError):
    """Fail-closed, diagnostic-only error; it never formats untrusted input."""

    def __init__(self, diagnostics: tuple[LifecycleReferenceDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(_Messages.enumeration_rejected(len(diagnostics)))


class LifecycleReferenceParseError(LifecycleReferenceGraphError):
    """A reference-specific form of the fail-closed enumeration error."""


class _Patterns:
    """Strict logical-token grammars shared by the registry's scheme parsers."""

    SAFE_TOKEN: ClassVar[re.Pattern[str]] = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
    )
    SAFE_SEGMENT: ClassVar[re.Pattern[str]] = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
    )
    SHA256: ClassVar[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
    POSITIVE_INT: ClassVar[re.Pattern[str]] = re.compile(r"^[1-9][0-9]*$")
    DRAFT_PROPOSAL: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9][A-Za-z0-9._:-]{0,255})/v([1-9][0-9]*)$"
    )
    DRAFT_DIFF: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9][A-Za-z0-9._:-]{0,255})/v([1-9][0-9]*)\.\.v([1-9][0-9]*)$"
    )
    STAGE_REVISION: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9][A-Za-z0-9._:-]{0,255})/v([1-9][0-9]*)$"
    )
    LEDGER_FOLD: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9][A-Za-z0-9._:-]{0,255})@([1-9][0-9]*)$"
    )
    BARE_SURFACE_ID: ClassVar[re.Pattern[str]] = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
    )
    ARTIFACT_SURFACE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(art_[^@/]+)@([1-9][0-9]*)$"
    )


class _Messages:
    """Safe error text; detail remains in typed, redacted diagnostic codes."""

    @staticmethod
    def enumeration_rejected(count: int) -> str:
        return f"lifecycle reference enumeration rejected ({count} safe diagnostic(s))"


class LifecycleReferenceRegistry:
    """Closed, strict scheme registry with one lifecycle owner per scheme.

    This class owns all parsing behavior so future references require an
    explicit registration and parser change instead of becoming silently
    discoverable through a recursive scan.
    """

    _CONTRACT: ClassVar[dict[str, object]] = load_work_ledger_contract()
    _MAX_REFERENCE_LENGTH: ClassVar[int] = int(
        dict(_CONTRACT["references"])["max_length"]
    )
    _MAX_SAFE_INTEGER: ClassVar[int] = int(
        dict(_CONTRACT["digests"])["max_safe_integer"]
    )
    _FORBIDDEN_SCHEMES: ClassVar[frozenset[str]] = frozenset(
        {"data", "file", "filesystem", "http", "https"}
    )
    _SURFACE_SCHEMES: ClassVar[frozenset[LifecycleReferenceScheme]] = frozenset(
        {
            LifecycleReferenceScheme.ARTIFACT_CODE_SURFACE,
            LifecycleReferenceScheme.ARTIFACT_DATASET_SURFACE,
            LifecycleReferenceScheme.ARTIFACT_DOCUMENT_SURFACE,
            LifecycleReferenceScheme.ARTIFACT_FILE_SURFACE,
            LifecycleReferenceScheme.BOARD_SURFACE,
            LifecycleReferenceScheme.DASHBOARD_SURFACE,
            LifecycleReferenceScheme.DOC_SURFACE,
            LifecycleReferenceScheme.EVENT_SURFACE,
            LifecycleReferenceScheme.FILE_SURFACE,
            LifecycleReferenceScheme.FORM_SURFACE,
            LifecycleReferenceScheme.MESSAGE_SURFACE,
            LifecycleReferenceScheme.RECORD_SURFACE,
            LifecycleReferenceScheme.TABLE_SURFACE,
            LifecycleReferenceScheme.TIMELINE_SURFACE,
        }
    )
    _ARTIFACT_SURFACE_SCHEMES: ClassVar[frozenset[LifecycleReferenceScheme]] = (
        frozenset(
            {
                LifecycleReferenceScheme.ARTIFACT_CODE_SURFACE,
                LifecycleReferenceScheme.ARTIFACT_DATASET_SURFACE,
                LifecycleReferenceScheme.ARTIFACT_DOCUMENT_SURFACE,
                LifecycleReferenceScheme.ARTIFACT_FILE_SURFACE,
            }
        )
    )

    def __init__(
        self, registrations: tuple[LifecycleReferenceRegistration, ...]
    ) -> None:
        by_scheme: dict[LifecycleReferenceScheme, LifecycleReferenceRegistration] = {}
        by_wire_scheme: dict[str, list[LifecycleReferenceRegistration]] = {}
        for registration in registrations:
            if registration.scheme in by_scheme:
                self._raise(
                    LifecycleDiagnosticCode.DUPLICATE_SCHEME_OWNER,
                    scheme=registration.scheme,
                )
            by_scheme[registration.scheme] = registration
            by_wire_scheme.setdefault(registration.wire_scheme, []).append(registration)
        self._registrations = tuple(registrations)
        self._by_scheme = by_scheme
        self._by_wire_scheme = {
            wire_scheme: tuple(rows) for wire_scheme, rows in by_wire_scheme.items()
        }

    @classmethod
    def default(cls) -> LifecycleReferenceRegistry:
        """Build the shipped v2.1 registry without importing owning services."""

        registrations = (
            cls._registration(
                LifecycleReferenceScheme.BARE_SURFACE,
                LifecycleReferenceOwner.SURFACE_PRESENTATION,
                LifecycleNodeKind.SURFACE,
                "surface_01",
                surface_id_only=True,
                wire_scheme="bare-surface-id",
            ),
            cls._registration(
                LifecycleReferenceScheme.ACTIVITY,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.REFERENCE,
                "activity://run_01/1",
            ),
            cls._registration(
                LifecycleReferenceScheme.ARTIFACT,
                LifecycleReferenceOwner.ARTIFACT_REPOSITORY,
                LifecycleNodeKind.ARTIFACT,
                "artifact://art_018f47a6-7b2c-7b10-8f21-12345678b002/revisions/1",
            ),
            cls._registration(
                LifecycleReferenceScheme.ARTIFACT_BLOB,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "artifact-blob://sha256/" + "a" * 64,
            ),
            cls._artifact_surface_registration(
                LifecycleReferenceScheme.ARTIFACT_CODE_SURFACE
            ),
            cls._artifact_surface_registration(
                LifecycleReferenceScheme.ARTIFACT_DOCUMENT_SURFACE
            ),
            cls._artifact_surface_registration(
                LifecycleReferenceScheme.ARTIFACT_DATASET_SURFACE
            ),
            cls._artifact_surface_registration(
                LifecycleReferenceScheme.ARTIFACT_FILE_SURFACE
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_PAGE,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-page://pg_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_PLAN,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-plan://" + "b" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_PRECONDITION,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-precondition://" + "c" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_PREPARED,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-prepared://ses_01/one",
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_RECEIPT,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-receipt://ses_01/one",
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_SESSION,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-session://ses_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.BROWSER_TARGET,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "browser-target://" + "d" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.CALL,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.CALL,
                "call:call_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.CHAT,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.REFERENCE,
                "chat://run_01/final",
            ),
            cls._registration(
                LifecycleReferenceScheme.COMMIT,
                LifecycleReferenceOwner.LEGACY_STAGE,
                LifecycleNodeKind.REFERENCE,
                "commit://stage_01/1",
            ),
            cls._surface_registration(LifecycleReferenceScheme.BOARD_SURFACE),
            cls._surface_registration(LifecycleReferenceScheme.DASHBOARD_SURFACE),
            cls._surface_registration(LifecycleReferenceScheme.DOC_SURFACE),
            cls._registration(
                LifecycleReferenceScheme.DRAFT,
                LifecycleReferenceOwner.LEGACY_STAGE,
                LifecycleNodeKind.REFERENCE,
                "draft://draft_01/v1",
            ),
            cls._registration(
                LifecycleReferenceScheme.EFFECT_STAGE_SURFACE,
                LifecycleReferenceOwner.EFFECT_STAGE,
                LifecycleNodeKind.SURFACE,
                "effect-stage://stg_01",
                surface_id_only=True,
            ),
            cls._surface_registration(LifecycleReferenceScheme.EVENT_SURFACE),
            cls._surface_registration(LifecycleReferenceScheme.FILE_SURFACE),
            cls._surface_registration(LifecycleReferenceScheme.FORM_SURFACE),
            cls._registration(
                LifecycleReferenceScheme.LEDGER,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.REFERENCE,
                "ledger://run_01@1",
            ),
            cls._registration(
                LifecycleReferenceScheme.MCP_TARGET,
                LifecycleReferenceOwner.OPERATION_GATEWAY,
                LifecycleNodeKind.REFERENCE,
                "mcp-target://linear/issue_ENG_1",
            ),
            cls._registration(
                LifecycleReferenceScheme.MESSAGE,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.REFERENCE,
                "message://msg_01",
            ),
            cls._surface_registration(
                LifecycleReferenceScheme.MESSAGE_SURFACE,
                wire_scheme="message",
            ),
            cls._registration(
                LifecycleReferenceScheme.OPERATION,
                LifecycleReferenceOwner.OPERATION_GATEWAY,
                LifecycleNodeKind.OPERATION,
                "operation://op_018f47a6-7b2c-7a10-8f21-12345678a004/args",
            ),
            cls._registration(
                LifecycleReferenceScheme.PAYLOAD,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                LifecycleNodeKind.REFERENCE,
                "payload://evt_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.POLICY,
                LifecycleReferenceOwner.POLICY_ENGINE,
                LifecycleNodeKind.REFERENCE,
                "policy://runs/run_01/mcp",
            ),
            cls._registration(
                LifecycleReferenceScheme.PREPARED,
                LifecycleReferenceOwner.EFFECT_STAGE,
                LifecycleNodeKind.REFERENCE,
                "prepared://effects/stg_01/idem_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.PRINCIPAL,
                LifecycleReferenceOwner.IDENTITY,
                LifecycleNodeKind.REFERENCE,
                "principal://users/user_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.PROPOSAL,
                LifecycleReferenceOwner.EFFECT_STAGE,
                LifecycleNodeKind.EFFECT_STAGE,
                "proposal://stg_018f47a6-7b2c-7c10-8f21-12345678c005/revisions/1",
            ),
            cls._registration(
                LifecycleReferenceScheme.RECEIPT,
                LifecycleReferenceOwner.RECEIPT_LIFECYCLE,
                LifecycleNodeKind.REFERENCE,
                "receipt://effects/stg_018f47a6-7b2c-7c10-8f21-12345678c006/claim_06",
            ),
            cls._surface_registration(LifecycleReferenceScheme.RECORD_SURFACE),
            cls._registration(
                LifecycleReferenceScheme.ROWSET_TARGET,
                LifecycleReferenceOwner.EFFECT_STAGE,
                LifecycleNodeKind.REFERENCE,
                "rowset-target://" + "e" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.SANDBOX,
                LifecycleReferenceOwner.SANDBOX_RUNTIME,
                LifecycleNodeKind.REFERENCE,
                "sandbox://objects/csv_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.SPEC,
                LifecycleReferenceOwner.SURFACE_PRESENTATION,
                LifecycleNodeKind.REFERENCE,
                "spec:linear/get_issue",
            ),
            cls._registration(
                LifecycleReferenceScheme.STAGE,
                LifecycleReferenceOwner.LEGACY_STAGE,
                LifecycleNodeKind.EFFECT_STAGE,
                "stage://stage_01/v1",
            ),
            cls._surface_registration(LifecycleReferenceScheme.TABLE_SURFACE),
            cls._surface_registration(LifecycleReferenceScheme.TIMELINE_SURFACE),
            cls._registration(
                LifecycleReferenceScheme.WORKSPACE_MATERIAL,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "workspace-material://sha256/" + "f" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.WORKSPACE_PRECONDITION,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "workspace-precondition://sha256/" + "a" * 64,
            ),
            cls._registration(
                LifecycleReferenceScheme.WORKSPACE_PREPARED,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "workspace-prepared://prepared_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.WORKSPACE_RECEIPT,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "workspace-receipt://claim_01",
            ),
            cls._registration(
                LifecycleReferenceScheme.WORKSPACE_TARGET,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                LifecycleNodeKind.REFERENCE,
                "workspace-target://grant_01/pathToken_01",
            ),
        )
        return cls(registrations)

    @classmethod
    def _registration(
        cls,
        scheme: LifecycleReferenceScheme,
        owner: LifecycleReferenceOwner,
        node_kind: LifecycleNodeKind,
        example: str,
        *,
        surface_id_only: bool = False,
        wire_scheme: str | None = None,
    ) -> LifecycleReferenceRegistration:
        return LifecycleReferenceRegistration(
            scheme=scheme,
            wire_scheme=wire_scheme if wire_scheme is not None else scheme.value,
            owner=owner,
            node_kind=node_kind,
            example=example,
            surface_id_only=surface_id_only,
        )

    @classmethod
    def _surface_registration(
        cls, scheme: LifecycleReferenceScheme, *, wire_scheme: str | None = None
    ) -> LifecycleReferenceRegistration:
        return cls._registration(
            scheme,
            LifecycleReferenceOwner.SURFACE_PRESENTATION,
            LifecycleNodeKind.SURFACE,
            f"{wire_scheme if wire_scheme is not None else scheme.value}://linear/get_issue/ENG_1",
            surface_id_only=True,
            wire_scheme=wire_scheme,
        )

    @classmethod
    def _artifact_surface_registration(
        cls, scheme: LifecycleReferenceScheme
    ) -> LifecycleReferenceRegistration:
        return cls._registration(
            scheme,
            LifecycleReferenceOwner.ARTIFACT_REPOSITORY,
            LifecycleNodeKind.SURFACE,
            f"{scheme.value}://art_018f47a6-7b2c-7b10-8f21-12345678b002@1",
            surface_id_only=True,
        )

    @property
    def registrations(self) -> tuple[LifecycleReferenceRegistration, ...]:
        """Stable registrations in declaration order for audit/test consumers."""

        return self._registrations

    def parse(self, reference: object) -> LifecycleReference:
        """Strictly parse a non-surface lifecycle reference.

        Surface archetype URIs have an intentionally separate entry point so a
        logical ``file://`` surface cannot accidentally authorize a physical
        filesystem reference in ordinary ledger ref fields.
        """

        return self._parse(reference, allow_surface_id_schemes=False)

    def parse_surface_id(self, surface_id: object) -> LifecycleReference:
        """Parse every surface id through an explicit URI or bare-id registry."""

        if not isinstance(surface_id, str):
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        if "://" not in surface_id:
            return self._parse_bare_surface_id(surface_id)
        return self._parse(surface_id, allow_surface_id_schemes=True)

    def assert_contract_coverage(self) -> None:
        """Fail closed when SSOT reference shapes/fields outgrow this registry."""

        diagnostics: list[LifecycleReferenceDiagnostic] = []
        references = dict(self._CONTRACT.get("references") or {})
        for value in references.values():
            if not isinstance(value, str) or "://" not in value:
                continue
            scheme_text = value.split("://", 1)[0]
            if not self._by_wire_scheme.get(scheme_text):
                diagnostics.append(
                    LifecycleReferenceDiagnostic(
                        code=LifecycleDiagnosticCode.UNMAPPED_CONTRACT_REFERENCE
                    )
                )

        expected = LifecycleReferenceEnumerator.contract_reference_fields()
        actual = LifecycleReferenceEnumerator.mapped_reference_fields()
        for event_type, field in expected - actual:
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.UNMAPPED_CONTRACT_REFERENCE,
                    event_type=event_type,
                    field=field,
                )
            )
        for (
            event_type
        ) in LifecycleReferenceEnumerator.unmapped_contract_reference_events():
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.UNMAPPED_CONTRACT_REFERENCE,
                    event_type=event_type,
                )
            )
        if diagnostics:
            raise LifecycleReferenceGraphError(tuple(diagnostics))

    def assert_registered_examples(self) -> None:
        """Verify every declared scheme is parseable and has exactly one owner."""

        for registration in self._registrations:
            parsed = (
                self.parse_surface_id(registration.example)
                if registration.scheme is LifecycleReferenceScheme.BARE_SURFACE
                else self._parse(
                    registration.example,
                    allow_surface_id_schemes=registration.surface_id_only,
                )
            )
            if (
                parsed.owner is not registration.owner
                or parsed.scheme is not registration.scheme
            ):
                self._raise(
                    LifecycleDiagnosticCode.MALFORMED_REFERENCE,
                    scheme=registration.scheme,
                )

    def _parse(
        self, reference: object, *, allow_surface_id_schemes: bool
    ) -> LifecycleReference:
        value = self._validate_outer_reference(reference)
        if value.startswith("call:") and not value.startswith("call://"):
            return self._validated_reference(
                self._by_scheme[LifecycleReferenceScheme.CALL],
                value,
                (value.removeprefix("call:"),),
                allow_surface_id_schemes=allow_surface_id_schemes,
            )
        if value.startswith("spec:") and not value.startswith("spec://"):
            parts = tuple(value.removeprefix("spec:").split("/"))
            return self._validated_reference(
                self._by_scheme[LifecycleReferenceScheme.SPEC],
                value,
                parts,
                allow_surface_id_schemes=allow_surface_id_schemes,
            )
        if value.startswith("payload/"):
            parts = tuple(value.removeprefix("payload/").split("/"))
            return self._validated_reference(
                self._by_scheme[LifecycleReferenceScheme.PAYLOAD],
                value,
                parts,
                allow_surface_id_schemes=allow_surface_id_schemes,
            )
        if value.startswith("spec/"):
            parts = tuple(value.removeprefix("spec/").split("/"))
            return self._validated_reference(
                self._by_scheme[LifecycleReferenceScheme.SPEC],
                value,
                parts,
                allow_surface_id_schemes=allow_surface_id_schemes,
            )

        if value.lower().startswith(("filesystem:", "data:", "http:", "https:")):
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        if value.lower().startswith("file:") and not (
            allow_surface_id_schemes and value.startswith("file://")
        ):
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        if "://" not in value:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        try:
            parsed = urlsplit(value)
        except ValueError:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        scheme_text = parsed.scheme
        if (
            not scheme_text
            or scheme_text != scheme_text.lower()
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or (
                scheme_text
                not in {
                    LifecycleReferenceScheme.LEDGER.value,
                    *(scheme.value for scheme in self._ARTIFACT_SURFACE_SCHEMES),
                }
                and "@" in parsed.netloc
            )
        ):
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        if scheme_text in self._FORBIDDEN_SCHEMES and not (
            allow_surface_id_schemes
            and scheme_text == LifecycleReferenceScheme.FILE_SURFACE.value
        ):
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        registrations = self._by_wire_scheme.get(scheme_text, ())
        if not registrations:
            self._raise(LifecycleDiagnosticCode.UNKNOWN_SCHEME)
        parts = (
            self._ledger_parts(value)
            if scheme_text == LifecycleReferenceScheme.LEDGER.value
            else self._artifact_surface_parts(value, scheme_text)
            if scheme_text
            in {scheme.value for scheme in self._ARTIFACT_SURFACE_SCHEMES}
            else self._uri_parts(parsed.netloc, parsed.path)
        )
        registration = self._registration_for(
            registrations,
            parts=parts,
            allow_surface_id_schemes=allow_surface_id_schemes,
        )
        return self._validated_reference(
            registration,
            value,
            parts,
            allow_surface_id_schemes=allow_surface_id_schemes,
        )

    def _validated_reference(
        self,
        registration: LifecycleReferenceRegistration,
        value: str,
        parts: tuple[str, ...],
        *,
        allow_surface_id_schemes: bool,
    ) -> LifecycleReference:
        scheme = registration.scheme
        if registration.surface_id_only and not allow_surface_id_schemes:
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE, scheme=scheme)
        if not parts or any(
            _Patterns.SAFE_TOKEN.fullmatch(part) is None for part in parts
        ):
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)
        self._validate_scheme_shape(scheme, value, parts)
        return LifecycleReference(
            reference=value,
            scheme=scheme,
            owner=registration.owner,
            node_kind=registration.node_kind,
            parts=parts,
        )

    def _validate_scheme_shape(
        self,
        scheme: LifecycleReferenceScheme,
        value: str,
        parts: tuple[str, ...],
    ) -> None:
        """Apply canonical grammar after hostile outer URI forms are excluded."""

        try:
            if scheme is LifecycleReferenceScheme.ARTIFACT:
                ArtifactContentRefCodec.parse(value)
            elif scheme in self._ARTIFACT_SURFACE_SCHEMES:
                self._validate_artifact_surface(parts, scheme)
            elif scheme is LifecycleReferenceScheme.OPERATION:
                self._validate_operation_reference(value, parts)
            elif scheme is LifecycleReferenceScheme.PROPOSAL:
                ProposalUriCodec.parse(value)
            elif scheme is LifecycleReferenceScheme.RECEIPT:
                self._validate_receipt_reference(value, parts)
            elif scheme is LifecycleReferenceScheme.WORKSPACE_TARGET:
                WorkspaceTargetRefCodec.parse(value)
            elif scheme in {
                LifecycleReferenceScheme.ARTIFACT_BLOB,
                LifecycleReferenceScheme.WORKSPACE_MATERIAL,
                LifecycleReferenceScheme.WORKSPACE_PRECONDITION,
            }:
                self._validate_digest_reference(parts)
            elif scheme in {
                LifecycleReferenceScheme.BROWSER_PLAN,
                LifecycleReferenceScheme.BROWSER_PRECONDITION,
            }:
                self._validate_digest_only_reference(parts)
            elif scheme is LifecycleReferenceScheme.BROWSER_TARGET:
                self._validate_browser_target(parts)
            elif scheme in {
                LifecycleReferenceScheme.BROWSER_PREPARED,
                LifecycleReferenceScheme.BROWSER_RECEIPT,
            }:
                self._validate_exact_parts(parts, 2)
            elif scheme is LifecycleReferenceScheme.PREPARED:
                self._validate_exact_parts(parts, 3)
            elif scheme in {
                LifecycleReferenceScheme.BROWSER_PAGE,
                LifecycleReferenceScheme.BROWSER_SESSION,
                LifecycleReferenceScheme.WORKSPACE_PREPARED,
                LifecycleReferenceScheme.WORKSPACE_RECEIPT,
                LifecycleReferenceScheme.MESSAGE,
            }:
                self._validate_exact_parts(parts, 1)
            elif scheme in {
                LifecycleReferenceScheme.CALL,
                LifecycleReferenceScheme.EFFECT_STAGE_SURFACE,
            }:
                self._validate_exact_parts(parts, 1)
            elif scheme is LifecycleReferenceScheme.CHAT:
                self._validate_exact_parts(parts, 2)
                if parts[1] != "final":
                    self._raise(
                        LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme
                    )
            elif scheme is LifecycleReferenceScheme.COMMIT:
                self._validate_exact_parts(parts, 2)
                self._validate_positive_part(parts[1], scheme)
            elif scheme is LifecycleReferenceScheme.DRAFT:
                self._validate_draft(parts, scheme)
            elif scheme is LifecycleReferenceScheme.LEDGER:
                self._validate_ledger(value, parts, scheme)
            elif scheme is LifecycleReferenceScheme.MCP_TARGET:
                self._validate_exact_parts(parts, 2)
            elif scheme in {
                LifecycleReferenceScheme.PAYLOAD,
                LifecycleReferenceScheme.SPEC,
            }:
                self._validate_path_parts(parts, scheme)
            elif scheme is LifecycleReferenceScheme.POLICY:
                self._validate_policy(parts, scheme)
            elif scheme is LifecycleReferenceScheme.PRINCIPAL:
                self._validate_principal(parts, scheme)
            elif scheme is LifecycleReferenceScheme.ROWSET_TARGET:
                self._validate_digest_only_reference(parts)
            elif scheme is LifecycleReferenceScheme.SANDBOX:
                self._validate_exact_parts(parts, 2)
                if parts[0] != "objects":
                    self._raise(
                        LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme
                    )
            elif scheme is LifecycleReferenceScheme.STAGE:
                self._validate_stage_revision(parts, scheme)
            elif scheme is LifecycleReferenceScheme.ACTIVITY:
                self._validate_exact_parts(parts, 2)
                self._validate_positive_part(parts[1], scheme)
            elif scheme in self._SURFACE_SCHEMES:
                self._validate_exact_parts(parts, 3)
            else:  # pragma: no cover - enum coverage is pinned by examples/tests.
                self._raise(LifecycleDiagnosticCode.UNKNOWN_SCHEME)
        except ArtifactEffectFormatError:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_outer_reference(self, reference: object) -> str:
        if (
            not isinstance(reference, str)
            or not reference
            or len(reference) > self._MAX_REFERENCE_LENGTH
            or reference != reference.strip()
            or any(character in reference for character in ("\x00", "\n", "\r", "\\"))
            or reference.startswith(("/", "~"))
            or (len(reference) >= 3 and reference[1:3] in {":/", ":\\"})
        ):
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        decoded = self._fully_decode(reference)
        if decoded != reference:
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        if any(part in {".", ".."} for part in decoded.split("/")):
            self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
        return reference

    @staticmethod
    def _fully_decode(value: str) -> str:
        decoded = value
        for _ in range(4):
            next_decoded = unquote(decoded)
            if next_decoded == decoded:
                return decoded
            decoded = next_decoded
        return decoded

    def _parse_bare_surface_id(self, value: str) -> LifecycleReference:
        """Parse the registered, deliberately colon-free opaque surface form."""

        if ":" in value:
            candidate_scheme = value.split(":", 1)[0].lower()
            if candidate_scheme in self._FORBIDDEN_SCHEMES:
                self._raise(LifecycleDiagnosticCode.FORBIDDEN_REFERENCE)
            self._raise(LifecycleDiagnosticCode.UNKNOWN_SCHEME)
        if _Patterns.BARE_SURFACE_ID.fullmatch(value) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        registration = self._by_scheme[LifecycleReferenceScheme.BARE_SURFACE]
        return LifecycleReference(
            reference=value,
            scheme=registration.scheme,
            owner=registration.owner,
            node_kind=registration.node_kind,
            parts=(value,),
        )

    @staticmethod
    def _uri_parts(netloc: str, path: str) -> tuple[str, ...]:
        path_parts = tuple(part for part in path.split("/") if part)
        return (netloc, *path_parts)

    def _artifact_surface_parts(self, value: str, scheme_text: str) -> tuple[str, ...]:
        body = value.removeprefix(f"{scheme_text}://")
        match = _Patterns.ARTIFACT_SURFACE.fullmatch(body)
        if match is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)
        return (match.group(1), match.group(2))

    def _ledger_parts(self, value: str) -> tuple[str, ...]:
        body = value.removeprefix("ledger://")
        match = _Patterns.LEDGER_FOLD.fullmatch(body)
        if match is None:
            self._raise(
                LifecycleDiagnosticCode.MALFORMED_REFERENCE,
                scheme=LifecycleReferenceScheme.LEDGER,
            )
        return (match.group(1), match.group(2))

    def _registration_for(
        self,
        registrations: tuple[LifecycleReferenceRegistration, ...],
        *,
        parts: tuple[str, ...],
        allow_surface_id_schemes: bool,
    ) -> LifecycleReferenceRegistration:
        if len(registrations) == 1:
            return registrations[0]
        surface_candidates = tuple(
            registration
            for registration in registrations
            if registration.surface_id_only
        )
        non_surface_candidates = tuple(
            registration
            for registration in registrations
            if not registration.surface_id_only
        )
        if (
            allow_surface_id_schemes
            and len(parts) == 3
            and len(surface_candidates) == 1
        ):
            return surface_candidates[0]
        if len(non_surface_candidates) == 1:
            return non_surface_candidates[0]
        self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)

    def _validate_operation_reference(self, value: str, parts: tuple[str, ...]) -> None:
        self._validate_exact_parts(parts, 2)
        if parts[1] not in {"args", "result"}:
            self._raise(
                LifecycleDiagnosticCode.MALFORMED_REFERENCE,
                scheme=LifecycleReferenceScheme.OPERATION,
            )
        if parts[1] == "args":
            OperationArgsRefCodec.parse(value)
        else:
            OperationIdCodec.parse(parts[0])

    def _validate_artifact_surface(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        self._validate_exact_parts(parts, 2)
        ArtifactIdCodec.parse(parts[0])
        if (
            _Patterns.POSITIVE_INT.fullmatch(parts[1]) is None
            or int(parts[1]) > self._MAX_SAFE_INTEGER
        ):
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_receipt_reference(self, value: str, parts: tuple[str, ...]) -> None:
        if len(parts) == 3 and parts[0] == "effects":
            EffectReceiptRefCodec.parse(value)
            return
        self._validate_exact_parts(parts, 1)

    def _validate_digest_reference(self, parts: tuple[str, ...]) -> None:
        self._validate_exact_parts(parts, 2)
        if parts[0] != "sha256" or _Patterns.SHA256.fullmatch(parts[1]) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)

    def _validate_digest_only_reference(self, parts: tuple[str, ...]) -> None:
        self._validate_exact_parts(parts, 1)
        if _Patterns.SHA256.fullmatch(parts[0]) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)

    def _validate_browser_target(self, parts: tuple[str, ...]) -> None:
        if len(parts) == 1 and _Patterns.SHA256.fullmatch(parts[0]) is not None:
            return
        self._validate_exact_parts(parts, 2)

    def _validate_draft(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        body = "/".join(parts)
        if len(parts) == 1 and re.fullmatch(r"[0-9a-f]{32}", parts[0]) is not None:
            return
        if _Patterns.DRAFT_PROPOSAL.fullmatch(body) is not None:
            return
        diff = _Patterns.DRAFT_DIFF.fullmatch(body)
        if diff is not None and int(diff.group(2)) < int(diff.group(3)):
            return
        self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_ledger(
        self, value: str, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        self._validate_exact_parts(parts, 2)
        body = value.removeprefix("ledger://")
        if _Patterns.LEDGER_FOLD.fullmatch(body) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_path_parts(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        if not parts or any(
            _Patterns.SAFE_SEGMENT.fullmatch(part) is None for part in parts
        ):
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_policy(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        if len(parts) < 2 or len(parts) > 4:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)
        if parts[0] not in {"run", "runs"}:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_principal(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        self._validate_exact_parts(parts, 2)
        if parts[0] not in {"system", "users"}:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_stage_revision(
        self, parts: tuple[str, ...], scheme: LifecycleReferenceScheme
    ) -> None:
        self._validate_exact_parts(parts, 2)
        if _Patterns.STAGE_REVISION.fullmatch("/".join(parts)) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_positive_part(
        self, value: str, scheme: LifecycleReferenceScheme
    ) -> None:
        if _Patterns.POSITIVE_INT.fullmatch(value) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE, scheme=scheme)

    def _validate_exact_parts(self, parts: tuple[str, ...], count: int) -> None:
        if len(parts) != count:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)

    def _validate_opaque_identifier(self, value: object) -> None:
        if not isinstance(value, str) or _Patterns.SAFE_TOKEN.fullmatch(value) is None:
            self._raise(LifecycleDiagnosticCode.MALFORMED_REFERENCE)

    @staticmethod
    def _raise(
        code: LifecycleDiagnosticCode,
        *,
        scheme: LifecycleReferenceScheme | None = None,
    ) -> None:
        raise LifecycleReferenceParseError(
            (LifecycleReferenceDiagnostic(code=code, scheme=scheme),)
        )


class LifecycleReferenceEnumerator:
    """Closed-field Work Ledger → lifecycle graph projection.

    It intentionally does not inspect arbitrary values nested inside a payload:
    body text and future opaque data must not be mistaken for lifecycle edges.
    Contract additions have to opt into this field map explicitly.
    """

    _REFERENCE_FIELDS_BY_EVENT: ClassVar[
        Mapping[LedgerEventType, tuple[LifecycleReferenceField, ...]]
    ] = {
        LedgerEventType.READ_EXECUTED: (LifecycleReferenceField.PAYLOAD_REF,),
        LedgerEventType.SURFACE_CREATED: (LifecycleReferenceField.PAYLOAD_REF,),
        LedgerEventType.VIEW_DERIVED: (LifecycleReferenceField.SPEC_REF,),
        LedgerEventType.WRITE_STAGED: (LifecycleReferenceField.PROPOSAL_REF,),
        LedgerEventType.REVISION_ADDED: (
            LifecycleReferenceField.DIFF_REF,
            LifecycleReferenceField.PROPOSAL_REF,
        ),
        LedgerEventType.WRITE_APPLIED: (LifecycleReferenceField.CONNECTOR_RECEIPT_REF,),
        LedgerEventType.RECEIPT_EMITTED: (LifecycleReferenceField.FOLD_REF,),
        LedgerEventType.OPERATION_COMPLETED: (LifecycleReferenceField.RESULT_REF,),
        LedgerEventType.ARTIFACT_CREATED: (LifecycleReferenceField.CONTENT_REF,),
        LedgerEventType.ARTIFACT_REVISED: (LifecycleReferenceField.CONTENT_REF,),
        LedgerEventType.ARTIFACT_PROMOTED: (LifecycleReferenceField.SOURCE_REF,),
        LedgerEventType.EFFECT_STAGED: (
            LifecycleReferenceField.TARGET_REF,
            LifecycleReferenceField.PROPOSAL_REF,
            LifecycleReferenceField.PROPOSAL_CONTENT_REF,
            LifecycleReferenceField.PRECONDITION_REF,
            LifecycleReferenceField.POLICY_SNAPSHOT_REF,
            LifecycleReferenceField.SAFE_SUMMARY_REF,
            LifecycleReferenceField.OWNER_REF,
            LifecycleReferenceField.AUTHOR_REF,
        ),
        LedgerEventType.EFFECT_REVISED: (
            LifecycleReferenceField.PROPOSAL_REF,
            LifecycleReferenceField.PROPOSAL_CONTENT_REF,
            LifecycleReferenceField.TARGET_REF,
            LifecycleReferenceField.PRECONDITION_REF,
            LifecycleReferenceField.SAFE_DIFF_REF,
            LifecycleReferenceField.AUTHOR_REF,
        ),
        LedgerEventType.EFFECT_DECISION_RECORDED: (LifecycleReferenceField.ACTOR_REF,),
        LedgerEventType.EFFECT_APPLIED: (LifecycleReferenceField.RECEIPT_REF,),
        LedgerEventType.EFFECT_RECONCILED: (LifecycleReferenceField.RECEIPT_REF,),
    }
    _ENTITY_FIELDS: ClassVar[Mapping[str, LifecycleNodeKind]] = {
        "artifact_id": LifecycleNodeKind.ARTIFACT,
        "call_id": LifecycleNodeKind.CALL,
        "gate_id": LifecycleNodeKind.GATE,
        "operation_id": LifecycleNodeKind.OPERATION,
        "parent_operation_id": LifecycleNodeKind.OPERATION,
        "stage_id": LifecycleNodeKind.EFFECT_STAGE,
        "surface_id": LifecycleNodeKind.SURFACE,
    }

    def __init__(self, registry: LifecycleReferenceRegistry | None = None) -> None:
        self._registry = (
            registry if registry is not None else LifecycleReferenceRegistry.default()
        )

    @classmethod
    def contract_reference_fields(
        cls,
    ) -> set[tuple[LedgerEventType, LifecycleReferenceField]]:
        """Derive all declared ref fields from SSOT, not from local assumptions."""

        contract = load_work_ledger_contract()
        event_specs = dict(contract.get("events") or {})
        declared: set[tuple[LedgerEventType, LifecycleReferenceField]] = set()
        for event_text, metadata in event_specs.items():
            if not isinstance(metadata, Mapping):
                continue
            try:
                event_type = LedgerEventType(str(event_text))
            except ValueError:
                continue
            fields = tuple(metadata.get("required") or ()) + tuple(
                metadata.get("optional") or ()
            )
            for field_value in fields:
                if not isinstance(field_value, str) or not field_value.endswith("_ref"):
                    continue
                try:
                    field = LifecycleReferenceField(field_value)
                except ValueError:
                    continue
                declared.add((event_type, field))
        return declared

    @classmethod
    def mapped_reference_fields(
        cls,
    ) -> set[tuple[LedgerEventType, LifecycleReferenceField]]:
        """Return the closed field allow-list this enumerator actually reads."""

        return {
            (event_type, field)
            for event_type, fields in cls._REFERENCE_FIELDS_BY_EVENT.items()
            for field in fields
        }

    @classmethod
    def unmapped_contract_reference_events(cls) -> set[LedgerEventType]:
        """Detect newly advertised ``*_ref`` fields with no typed enum mapping."""

        contract = load_work_ledger_contract()
        event_specs = dict(contract.get("events") or {})
        unknown: set[LedgerEventType] = set()
        for event_text, metadata in event_specs.items():
            if not isinstance(metadata, Mapping):
                continue
            try:
                event_type = LedgerEventType(str(event_text))
            except ValueError:
                continue
            fields = tuple(metadata.get("required") or ()) + tuple(
                metadata.get("optional") or ()
            )
            for field_value in fields:
                if not isinstance(field_value, str) or not field_value.endswith("_ref"):
                    continue
                try:
                    LifecycleReferenceField(field_value)
                except ValueError:
                    unknown.add(event_type)
        return unknown

    def enumerate(
        self,
        *,
        run_id: str,
        events: Iterable[Mapping[str, object]],
    ) -> LifecycleReferenceGraph:
        """Enumerate one run or fail closed without returning a partial graph."""

        diagnostics: list[LifecycleReferenceDiagnostic] = []
        if not self._safe_identifier(run_id):
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_RUN_ID
                )
            )
            raise LifecycleReferenceGraphError(tuple(diagnostics))

        nodes: dict[str, LifecycleGraphNode] = {}
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ] = {}
        run_node = self._node(
            nodes,
            kind=LifecycleNodeKind.RUN,
            identifier=run_id,
            owner=None,
        )
        seen_sequences: set[int] = set()
        for index, event in enumerate(events, start=1):
            event_type, sequence_no, payload = self._validated_event(
                event,
                fallback_sequence_no=index,
                diagnostics=diagnostics,
            )
            if event_type is None or sequence_no is None or payload is None:
                continue
            if sequence_no in seen_sequences:
                diagnostics.append(
                    LifecycleReferenceDiagnostic(
                        code=LifecycleDiagnosticCode.DUPLICATE_SEQUENCE,
                        event_type=event_type,
                        sequence_no=sequence_no,
                    )
                )
                continue
            seen_sequences.add(sequence_no)
            event_node = self._node(
                nodes,
                kind=LifecycleNodeKind.EVENT,
                identifier=f"{run_id}:{sequence_no}",
                owner=LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
            )
            self._edge(edges, run_node, event_node, LifecycleEdgeKind.CONTAINS_EVENT)
            self._enumerate_entity_fields(
                event_type=event_type,
                sequence_no=sequence_no,
                payload=payload,
                event_node=event_node,
                nodes=nodes,
                edges=edges,
                diagnostics=diagnostics,
            )
            self._enumerate_reference_fields(
                event_type=event_type,
                sequence_no=sequence_no,
                payload=payload,
                event_node=event_node,
                nodes=nodes,
                edges=edges,
                diagnostics=diagnostics,
            )
            self._enumerate_derived_references(
                event_type=event_type,
                sequence_no=sequence_no,
                payload=payload,
                event_node=event_node,
                nodes=nodes,
                edges=edges,
                diagnostics=diagnostics,
            )

        if diagnostics:
            raise LifecycleReferenceGraphError(tuple(diagnostics))
        return LifecycleReferenceGraph(
            run_id=run_id,
            nodes=tuple(nodes[key] for key in sorted(nodes)),
            edges=tuple(edges[key] for key in sorted(edges, key=str)),
        )

    def _validated_event(
        self,
        event: Mapping[str, object],
        *,
        fallback_sequence_no: int,
        diagnostics: list[LifecycleReferenceDiagnostic],
    ) -> tuple[LedgerEventType | None, int | None, Mapping[str, object] | None]:
        event_type_value = event.get("event_type")
        try:
            event_type = LedgerEventType(event_type_value)
        except (TypeError, ValueError):
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_LEDGER_EVENT
                )
            )
            return None, None, None
        sequence_value = event.get("sequence_no", fallback_sequence_no)
        if (
            not isinstance(sequence_value, int)
            or isinstance(sequence_value, bool)
            or sequence_value < 1
        ):
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_SEQUENCE,
                    event_type=event_type,
                )
            )
            return event_type, None, None
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_LEDGER_EVENT,
                    event_type=event_type,
                    sequence_no=sequence_value,
                )
            )
            return event_type, sequence_value, None
        try:
            validated = WorkLedgerVocabulary.validate_payload(event_type.value, payload)
        except ValueError:
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_LEDGER_EVENT,
                    event_type=event_type,
                    sequence_no=sequence_value,
                )
            )
            return event_type, sequence_value, None
        return (
            event_type,
            sequence_value,
            validated.model_dump(
                by_alias=True,
                exclude_none=True,
            ),
        )

    def _enumerate_entity_fields(
        self,
        *,
        event_type: LedgerEventType,
        sequence_no: int,
        payload: Mapping[str, object],
        event_node: LifecycleGraphNode,
        nodes: dict[str, LifecycleGraphNode],
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ],
        diagnostics: list[LifecycleReferenceDiagnostic],
    ) -> None:
        for field_name, kind in self._ENTITY_FIELDS.items():
            value = payload.get(field_name)
            if value is None:
                continue
            if field_name == "surface_id":
                self._add_surface_node(
                    value,
                    event_type,
                    sequence_no,
                    event_node,
                    nodes,
                    edges,
                    diagnostics,
                )
                continue
            if not isinstance(value, str) or not self._safe_identifier(value):
                diagnostics.append(
                    LifecycleReferenceDiagnostic(
                        code=LifecycleDiagnosticCode.INVALID_LEDGER_EVENT,
                        event_type=event_type,
                        sequence_no=sequence_no,
                    )
                )
                continue
            entity_node = self._node(nodes, kind=kind, identifier=value, owner=None)
            self._edge(edges, event_node, entity_node, LifecycleEdgeKind.IDENTIFIES)

    def _add_surface_node(
        self,
        value: object,
        event_type: LedgerEventType,
        sequence_no: int,
        event_node: LifecycleGraphNode,
        nodes: dict[str, LifecycleGraphNode],
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ],
        diagnostics: list[LifecycleReferenceDiagnostic],
    ) -> None:
        try:
            parsed = self._registry.parse_surface_id(value)
        except LifecycleReferenceParseError as error:
            diagnostics.extend(
                self._contextualize(
                    error.diagnostics,
                    event_type=event_type,
                    sequence_no=sequence_no,
                    field=None,
                )
            )
            return
        surface_node = self._node(
            nodes,
            kind=LifecycleNodeKind.SURFACE,
            identifier=parsed.reference,
            owner=parsed.owner,
        )
        self._edge(edges, event_node, surface_node, LifecycleEdgeKind.IDENTIFIES)

    def _enumerate_reference_fields(
        self,
        *,
        event_type: LedgerEventType,
        sequence_no: int,
        payload: Mapping[str, object],
        event_node: LifecycleGraphNode,
        nodes: dict[str, LifecycleGraphNode],
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ],
        diagnostics: list[LifecycleReferenceDiagnostic],
    ) -> None:
        for field in self._REFERENCE_FIELDS_BY_EVENT.get(event_type, ()):
            raw_reference = payload.get(field.value)
            if raw_reference is None:
                continue
            try:
                parsed = self._registry.parse(raw_reference)
            except LifecycleReferenceParseError as error:
                diagnostics.extend(
                    self._contextualize(
                        error.diagnostics,
                        event_type=event_type,
                        sequence_no=sequence_no,
                        field=field,
                    )
                )
                continue
            reference_node = self._node(
                nodes,
                kind=parsed.node_kind,
                identifier=parsed.reference,
                owner=parsed.owner,
            )
            self._edge(
                edges,
                event_node,
                reference_node,
                LifecycleEdgeKind.REFERENCES,
                field=field,
            )

    def _enumerate_derived_references(
        self,
        *,
        event_type: LedgerEventType,
        sequence_no: int,
        payload: Mapping[str, object],
        event_node: LifecycleGraphNode,
        nodes: dict[str, LifecycleGraphNode],
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ],
        diagnostics: list[LifecycleReferenceDiagnostic],
    ) -> None:
        """Add canonical refs implied by a typed event, without body inspection."""

        if event_type is not LedgerEventType.OPERATION_REQUESTED:
            return
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str):
            diagnostics.append(
                LifecycleReferenceDiagnostic(
                    code=LifecycleDiagnosticCode.INVALID_LEDGER_EVENT,
                    event_type=event_type,
                    sequence_no=sequence_no,
                )
            )
            return
        try:
            parsed = self._registry.parse(f"operation://{operation_id}/args")
        except LifecycleReferenceParseError as error:
            diagnostics.extend(
                self._contextualize(
                    error.diagnostics,
                    event_type=event_type,
                    sequence_no=sequence_no,
                    field=None,
                )
            )
            return
        reference_node = self._node(
            nodes,
            kind=parsed.node_kind,
            identifier=parsed.reference,
            owner=parsed.owner,
        )
        self._edge(edges, event_node, reference_node, LifecycleEdgeKind.REFERENCES)

    @staticmethod
    def _safe_identifier(value: object) -> bool:
        return (
            isinstance(value, str) and _Patterns.SAFE_TOKEN.fullmatch(value) is not None
        )

    @staticmethod
    def _contextualize(
        diagnostics: tuple[LifecycleReferenceDiagnostic, ...],
        *,
        event_type: LedgerEventType,
        sequence_no: int,
        field: LifecycleReferenceField | None,
    ) -> tuple[LifecycleReferenceDiagnostic, ...]:
        return tuple(
            diagnostic.model_copy(
                update={
                    "event_type": event_type,
                    "sequence_no": sequence_no,
                    "field": field,
                }
            )
            for diagnostic in diagnostics
        )

    @staticmethod
    def _node(
        nodes: dict[str, LifecycleGraphNode],
        *,
        kind: LifecycleNodeKind,
        identifier: str,
        owner: LifecycleReferenceOwner | None,
    ) -> LifecycleGraphNode:
        node_id = f"{kind.value}:{identifier}"
        node = nodes.get(node_id)
        if node is None:
            node = LifecycleGraphNode(
                node_id=node_id,
                kind=kind,
                identifier=identifier,
                owner=owner,
            )
            nodes[node_id] = node
        return node

    @staticmethod
    def _edge(
        edges: dict[
            tuple[str, str, LifecycleEdgeKind, LifecycleReferenceField | None],
            LifecycleReferenceEdge,
        ],
        source: LifecycleGraphNode,
        target: LifecycleGraphNode,
        kind: LifecycleEdgeKind,
        *,
        field: LifecycleReferenceField | None = None,
    ) -> None:
        key = (source.node_id, target.node_id, kind, field)
        edges.setdefault(
            key,
            LifecycleReferenceEdge(
                from_node_id=source.node_id,
                to_node_id=target.node_id,
                kind=kind,
                field=field,
            ),
        )


__all__ = [
    "LifecycleDiagnosticCode",
    "LifecycleEdgeKind",
    "LifecycleGraphNode",
    "LifecycleNodeKind",
    "LifecycleReference",
    "LifecycleReferenceDiagnostic",
    "LifecycleReferenceEdge",
    "LifecycleReferenceEnumerator",
    "LifecycleReferenceField",
    "LifecycleReferenceGraph",
    "LifecycleReferenceGraphError",
    "LifecycleReferenceOwner",
    "LifecycleReferenceParseError",
    "LifecycleReferenceRegistration",
    "LifecycleReferenceRegistry",
    "LifecycleReferenceScheme",
]
