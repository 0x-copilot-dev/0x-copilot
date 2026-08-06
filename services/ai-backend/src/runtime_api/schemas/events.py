"""Replayable runtime event schemas and projection helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import ClassVar, Literal
from uuid import uuid4

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from copilot_service_contracts.work_ledger import LEDGER_EVENT_TYPES

from agent_runtime.execution.contracts import (
    JsonObject,
    RuntimeContract,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.capabilities.task_policy_journal import TaskPolicyJournalRecord
from agent_runtime.execution.model_invocation.journal import ModelInvocationRecord
from agent_runtime.prompts.observation import (
    PromptAssembledRecord,
    PromptCacheObservedRecord,
)

# Lazy import: ``McpDispatcherUnwrap`` lives under ``agent_runtime.capabilities.mcp``,
# whose package ``__init__`` eagerly imports the MCP middleware. That middleware
# imports back through ``runtime_api.schemas`` (via the citation tooling), so a
# top-level import here triggers a circular load during ``agent_runtime`` init.
# ``_display_title_for`` resolves the helper at call time when both modules are
# fully initialised.
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpecError,
    validate_surface_spec,
)
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.surfaces_v2.constants import Keys as _LedgerKeys
from agent_runtime.surfaces_v2.constants import Values as _LedgerValues
from agent_runtime.surfaces_v2.ledger_models import (
    CURRENT_LEDGER_WRITER,
    LedgerWriter,
    UnknownLedgerWriterError,
    ViewBasis,
    ViewTier,
    WorkLedgerVocabulary,
)
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    AgentRunStatus,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)
from runtime_api.schemas.context_occupancy import ContextOccupancySnapshotPayload

# PRD-D3 — hard cap for row-set text the projector lets through (hold reasons,
# row titles, change field names / string values). Rendered UI text is treated
# as plain, length-capped strings; the domain validator caps the source too.
_ROWSET_TEXT_MAX = 200

# Host-folder grant ask — mirrors the producer's own bounds on
# ``WorkspaceGrantRequest`` (capabilities/desktop/workspace_grant.py) so the
# projection cannot admit a longer string than the domain accepted.
_WORKSPACE_GRANT_PATH_MAX = 1024
_WORKSPACE_GRANT_TEXT_MAX = 255


class _Fields:
    """Field name constants for presentation model validators and key references."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    PRESENTATION = "presentation"
    # Host-folder grant ask. ``WORKSPACE_GRANT`` is the client contract —
    # packages/chat-surface exports the same string as
    # ``WORKSPACE_GRANT_PAYLOAD_KEY`` and keys its Grant card on the block's
    # presence; ``PATH`` is REQUIRED by its parser.
    WORKSPACE_GRANT = "workspace_grant"
    # The folder an ``allow_always`` grant option would attach, named on the
    # card. Same block shape as ``WORKSPACE_GRANT`` and validated by the same
    # helper, under a different key on purpose: this one ADDS a control to the
    # ordinary approve/reject card, where ``workspace_grant`` REPLACES the card
    # (and with it the allow-once path). See ``_FilesystemApproval.GRANT_SCOPE``.
    GRANT_SCOPE = "grant_scope"
    PATH = "path"
    FOLDER_NAME = "folder_name"
    PLATFORM = "platform"
    MODE = "mode"
    URL = "url"
    BADGE = "badge"
    SUMMARY = "summary"
    GROUP_KEY = "group_key"
    PRIMARY_ENTITY = "primary_entity"
    ACTION_LABEL = "action_label"
    DEBUG_LABEL = "debug_label"
    ACTIVITY_KIND = "activity_kind"
    CITATION = "citation"
    CITATIONS = "citations"
    # PR 1.1-rev2 — model-declared citation pointer payload key.
    LINK = "link"
    CITED_ORDINALS = "cited_ordinals"
    CONVERSATION_ORDINAL = "conversation_ordinal"
    MESSAGE_ID = "message_id"
    PROSE_OFFSET = "prose_offset"
    PROSE_LENGTH = "prose_length"
    SOURCE_TOOL_CALL_ID = "source_tool_call_id"
    # Generative-UI (PRD-01) — surface_spec_generated payload keys + title.
    SURFACE_URI = "surface_uri"
    ARCHETYPE = "archetype"
    SPEC = "spec"
    SPEC_VERSION = "spec_version"
    GENERATOR_MODEL = "generator_model"
    SKILL_VERSION = "skill_version"
    SURFACE_PREPARED_TITLE = "Prepared a view"
    # Generative Surfaces v2 (PRD-D2, FR-C3) — write.applied display microcopy.
    # ``applied`` is the FR-C3 requirement string (verbatim); Phase-2 polishes
    # the ``failed`` wording. Single-use titles inlined here (matches
    # ``SURFACE_PREPARED_TITLE``).
    WRITE_APPLIED_TITLE = "Sent — exactly the revision you approved."
    WRITE_FAILED_TITLE = "Apply refused — nothing was sent."
    # Generative Surfaces v2 (PRD-E1, FR-E2) — the receipt seal's timeline title.
    RECEIPT_EMITTED_TITLE = "Run receipt"
    # Generative Surfaces v2 (PRD-A2, SDR §5) — usage.recorded payload keys.
    USAGE_V = "v"
    USAGE_PURPOSE = "purpose"
    USAGE_MODEL = "model"
    USAGE_TOKENS_IN = "tokens_in"
    USAGE_TOKENS_OUT = "tokens_out"
    USAGE_SURFACE_ID = "surface_id"


class _SurfaceStateFields:
    """Keys of the ``SurfaceState`` block carried on ``surface.created``.

    Declared here rather than on ``surfaces_v2.constants.Keys.Field``, which
    mirrors the A3-frozen SDR §5 *ledger* vocabulary. These are the RENDERER
    contract — the shape shared with ``packages/api-types`` (``SurfaceState``)
    and ``packages/surface-renderers`` — and the two disagree on purpose:
    provenance is ``{connector, op}`` on the ledger and ``{server, tool}`` to a
    renderer. ``spec`` is reused from the ledger keys because both spell it the
    same.
    """

    STATE = "state"
    SOURCE = "source"
    DATA = "data"
    SERVER = "server"
    TOOL = "tool"


class _LedgerWriterStamp:
    """The ledger row's writer stamp: its wire key and the set this build reads.

    Declared here for the same reason :class:`_SurfaceStateFields` is —
    ``surfaces_v2.constants.Keys.Field`` mirrors the A3-frozen SDR §5 field set,
    and ``w`` is additive to it. The vocabulary itself is
    :class:`~agent_runtime.surfaces_v2.ledger_models.LedgerWriter`; this only
    names the key and freezes the membership test.

    ``EVENT_TYPES`` scopes the whole rule to rows that speak the ledger
    vocabulary. ``w`` is one letter, and a tool or model payload may legitimately
    carry a ``w`` that means width.

    ``CURRENT`` is what this build signs with. It is a single value rather than a
    per-producer argument for the same reason ``LedgerWriter`` is a closed enum:
    a caller free to name its own writer can claim to be any generation it likes.
    """

    KEY = "w"
    CURRENT: str = CURRENT_LEDGER_WRITER.value
    KNOWN: frozenset[str] = frozenset(writer.value for writer in LedgerWriter)
    EVENT_TYPES: frozenset[str] = frozenset(LEDGER_EVENT_TYPES)


class _OperationFields:
    """A1 operation-event wire keys; all projection remains reference-only."""

    VERSION = "v"
    OPERATION_ID = "operation_id"
    PRODUCER = "producer"
    CAPABILITY = "capability"
    OP = "op"
    ARGS_DIGEST = "args_digest"
    PARENT_OPERATION_ID = "parent_operation_id"
    EFFECT_CLASS = "effect_class"
    BASIS = "basis"
    CONFIDENCE = "confidence"
    OUTCOME = "outcome"
    RESULT_REF = "result_ref"
    LATENCY_MS = "latency_ms"
    FAILURE_CODE = "failure_code"
    RETRYABLE = "retryable"


class _ViewDerivedVocabulary:
    """The two ``view.derived`` fields whose values are a closed set, not free text.

    Derived from the ledger enums rather than restated, so a member added to
    ``ViewBasis`` reaches the wire by being declared once — which is what makes
    this file a genuine fourth declaration of that key rather than a
    pass-through that lets new values ride along by accident.

    The filtering earns its keep in the other direction. ``_text`` forwards any
    non-empty string, so an emitter that skipped the payload model could put an
    undeclared basis on the wire, and every reader downstream would have to
    decide for itself what a word it has never heard of means. An unlisted value
    is dropped instead.
    """

    TIER: ClassVar[frozenset[str]] = frozenset(member.value for member in ViewTier)
    BASIS: ClassVar[frozenset[str]] = frozenset(member.value for member in ViewBasis)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_QUALITY_REF_MAX = 512
# Ceilings for the decision row's numeric extension. Each is the product bound
# with headroom, so a runaway producer is rejected at validation rather than
# persisting an unbounded integer: F3 search answers at most 10 candidates, a
# bridge answer is capped at 16 KiB inline, and one decision is one model turn.
_QUALITY_COUNT_MAX = 64
_QUALITY_TOKEN_MAX = 1_000_000
_QUALITY_TURN_MAX = 1_000
_QualityFeature = Literal[
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
]
_QualityMode = Literal["off", "shadow", "enforce"]


class QualityControlBoundPayload(RuntimeContract):
    """Closed, content-free canonical snapshot row carried by one run event."""

    schema_version: Literal[1, 2] = 1
    snapshot_id: str = Field(min_length=1, max_length=160)
    snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    subject_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    deployment_profile: str = Field(min_length=1, max_length=80)
    harness_variant_ref: str = Field(min_length=1, max_length=256)
    task_policy_selection_ref: str = Field(min_length=1, max_length=256)
    prompt_policy_revision: str = Field(min_length=1, max_length=256)
    capability_policy_revision: str = Field(min_length=1, max_length=256)
    context_policy_revision: str = Field(min_length=1, max_length=256)
    tool_controller_policy_revision: str = Field(min_length=1, max_length=256)
    concurrency_policy_revision: str = Field(min_length=1, max_length=256)
    dataflow_policy_revision: str = Field(min_length=1, max_length=256)
    mcp_freshness_policy_revision: str = Field(min_length=1, max_length=256)
    delegation_policy_revision: str = Field(min_length=1, max_length=256)
    model_route_policy_revision: str = Field(min_length=1, max_length=256)
    workspace_edit_policy_revision: str = Field(min_length=1, max_length=256)
    answer_verification_policy_revision: str = Field(
        min_length=1,
        max_length=256,
    )
    feature_mode_f1: _QualityMode
    feature_mode_f2: _QualityMode
    feature_mode_f3: _QualityMode
    feature_mode_f4: _QualityMode
    feature_mode_f5: _QualityMode
    feature_mode_f6: _QualityMode
    feature_mode_f7: _QualityMode
    feature_mode_f8: _QualityMode
    feature_mode_f9: _QualityMode
    feature_mode_f10: _QualityMode
    feature_mode_f11: _QualityMode
    feature_mode_f12: _QualityMode
    model_same_deployment_retry_mode: _QualityMode = "off"
    model_alternate_route_mode: _QualityMode = "off"
    model_equivalent_route_mode: _QualityMode = "off"
    model_circuit_influence_mode: _QualityMode = "off"
    model_qualification_authority_ref: str | None = Field(
        default=None,
        max_length=256,
    )
    model_qualification_authority_revision: str | None = Field(
        default=None,
        max_length=256,
    )
    budget_envelope_ref: str = Field(min_length=1, max_length=_QUALITY_REF_MAX)
    assignment_revision: str = Field(min_length=1, max_length=256)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class QualityDecisionPayload(RuntimeContract):
    """Closed, content-free canonical decision row carried by one run event.

    The four ``*_count`` / ``*_rank`` / ``*_tokens`` / ``*_turns`` members are
    the decision row's **bounded numeric extension**.  PRD 9.3 already lists
    "counts" among the things a run event may carry, and a rank is a count of
    positions, so these belong to the existing permission rather than widening
    it.  Four things keep them inside it:

    * **Explicitly named and separately range-constrained**, rather than one
      ``dict[str, float]``.  A generic numeric map would make the payload's
      key set a function of whatever a producer felt like measuring, which is
      precisely the unbounded vocabulary a closed event family exists to
      prevent, and it would have no place to state a per-quantity ceiling.
    * **Bounded above as well as below.**  Each ceiling is the real
      product bound (see each field), so a runaway producer fails validation
      instead of writing an arbitrarily large integer into a durable row.
    * **Body-free.**  A rank is a number; the *thing* ranked never travels
      with it.  There is no field here a query, capability name, description,
      argument, or result could enter through, which is a structural
      guarantee rather than a reviewed-each-call-site one.
    * **Optional, defaulting to ``None``.**  ``None`` means *not observed*,
      which is deliberately distinct from an observed ``0`` — a ceiling of
      zero must be able to tell "nothing came back" from "nothing was
      measured".  Older ``quality.decision.v1`` rows written before this
      extension carry none of these keys and still validate unchanged.
    """

    schema_version: Literal[1] = 1
    decision_id: str = Field(min_length=1, max_length=160)
    decision_digest: str = Field(pattern=_SHA256_PATTERN)
    snapshot_id: str = Field(min_length=1, max_length=160)
    phase: str = Field(min_length=1, max_length=80)
    feature: _QualityFeature
    policy_revision: str = Field(min_length=1, max_length=256)
    input_digest: str = Field(pattern=_SHA256_PATTERN)
    outcome_code: str = Field(min_length=1, max_length=120)
    record_ref: str | None = Field(default=None, max_length=_QUALITY_REF_MAX)
    parent_decision_refs: tuple[str, ...] = Field(default=(), max_length=64)
    #: How many candidates this decision's search answered with. Ceiling is
    #: F3's own ``search returns at most 10 candidates`` with headroom.
    candidate_count: int | None = Field(default=None, ge=0, le=_QUALITY_COUNT_MAX)
    #: The 1-based position the selected reference held in the search that
    #: offered it; ``0`` means the selection came back from no search at all,
    #: which is the miss selection recall exists to catch. Same ceiling as the
    #: candidate list it indexes into.
    selection_rank: int | None = Field(default=None, ge=0, le=_QUALITY_COUNT_MAX)
    #: Model-visible tokens this decision's answer cost.
    result_tokens: int | None = Field(default=None, ge=0, le=_QUALITY_TOKEN_MAX)
    #: Model turns this decision consumed.
    model_turns: int | None = Field(default=None, ge=0, le=_QUALITY_TURN_MAX)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class TaskPolicyJournalPayload(RuntimeContract):
    """Strict content-free F4 record carried by the canonical run journal."""

    record: TaskPolicyJournalRecord


class PromptAssembledPayload(RuntimeContract):
    """Strict body-free F2 assembly record carried by the run journal."""

    record: PromptAssembledRecord


class PromptCacheObservedPayload(RuntimeContract):
    """Strict provider-reported F2 cache fact carried by the run journal."""

    record: PromptCacheObservedRecord


class ModelInvocationJournalPayload(RuntimeContract):
    """Strict F10.3 invocation lineage carried by the canonical run journal."""

    record: ModelInvocationRecord = Field(discriminator="record_kind")


class ContextOccupancyPayload(RuntimeContract):
    """One measured context-window decomposition carried by the run stream.

    The live half of the Context Occupancy Ledger's §7 surface. ``snapshot`` is
    the *same* :class:`ContextOccupancySnapshotPayload` the read API returns, not
    a stream-only variant — a client that folds this event and a client that
    fetches ``/context/occupancy`` are looking at one shape through one reducer,
    and there is no second contract to keep in step.

    Content-free by construction (§6.5): every field is a count, a closed
    vocabulary value, or a bounded identifier. The nested contract's own
    validation is what enforces that, which is why the projector below validates
    rather than allow-lists key by key — the shape already *is* the allow-list.
    """

    snapshot: ContextOccupancySnapshotPayload


class RuntimeEventPresentationProjector:
    """Project normalized runtime events into stable UI timeline semantics."""

    SUBAGENT_STARTED_STATUSES = frozenset(
        {
            Values.Status.QUEUED,
            Values.Status.STARTED,
        }
    )
    SUBAGENT_COMPLETED_STATUSES = frozenset(
        {
            Values.Status.CANCELLED,
            Values.Status.COMPLETED,
            Values.Status.FAILED,
            "succeeded",
            "success",
        }
    )

    @classmethod
    def event_type_for_stream_event(
        cls, stream_event: StreamEvent
    ) -> RuntimeApiEventType:
        """Return the most specific API event type for a normalized runtime event."""

        override = cls._event_type_override(stream_event.payload, stream_event.metadata)
        if override is not None:
            return override
        if stream_event.event_type is StreamEventType.TOOL_CALL:
            return RuntimeApiEventType.TOOL_CALL_STARTED
        if stream_event.event_type is StreamEventType.TOOL_RESULT:
            return RuntimeApiEventType.TOOL_RESULT
        if stream_event.event_type in {
            StreamEventType.LIFECYCLE,
            StreamEventType.SUBAGENT_UPDATE,
        }:
            return cls._subagent_event_type(stream_event.payload)
        if (
            stream_event.source is StreamEventSource.SUBAGENT
            and stream_event.event_type
            in {
                StreamEventType.CUSTOM,
                StreamEventType.PROGRESS,
            }
        ):
            return RuntimeApiEventType.SUBAGENT_PROGRESS
        return RuntimeApiEventType.from_stream_event_type(stream_event.event_type)

    @classmethod
    def payload_for_event(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Return the client-visible payload for an API event type.

        Two steps, in this order: project through the per-type allow-list, then
        sign the row with the ledger writer stamp. The stamp is handled once,
        here, rather than inside each of the ~30 projections below — an
        allow-list that does not name a key deletes it silently, and that
        failure mode has already cost this pipeline a release (see
        :meth:`_surface_created_payload`). One seam cannot forget a branch.

        **This is the append funnel, and only the append funnel.** All three
        callers — ``RuntimeEventProducer``'s single and batch append entry
        points, and ``_build_from_stream_event`` — build a row on its way *into*
        the store. Replay reads envelopes back out and never re-projects, so
        signing here signs new rows without rewriting history, which is what
        lets absence of ``w`` keep meaning "written before the stamp existed".
        """

        return cls._sign_ledger_writer(
            event_type=event_type,
            payload=payload,
            projected=cls._project_payload(event_type=event_type, payload=payload),
        )

    @classmethod
    def _sign_ledger_writer(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        projected: JsonObject,
    ) -> JsonObject:
        """Sign a projected ledger payload with ``w``, or reject the record.

        The writer stamp is this seam's property alone: it is popped off the
        projection first, so no branch below can invent one, pass one through
        unchecked, or leave the ``null`` a ``model_dump`` produces for a payload
        model whose ``w`` was never set. ``null`` is a claim this ledger never
        makes: on a stored row, absence says "nobody signed", while ``null``
        would say "the writer is known to be nothing".

        An unsigned row is signed with :data:`CURRENT_LEDGER_WRITER` rather than
        left bare. Producer-side signing was tried and covered 6 of the 34 event
        types, which makes "no ``w``" ambiguous between a historic row and a live
        row from a producer that forgot — and a reader keyed on it classifies
        live rows as historic, which is the defect the stamp was added to close.
        A producer that signs itself is carried through unchanged; it is
        re-validated here either way, so a producer cannot widen the vocabulary.

        A stamp this build does not understand REJECTS the whole record, by
        raising, rather than being dropped or blanked. Dropping it would hand the
        client a payload written by an unknown writer formatted as though this
        build had written it — the silent mis-render the stamp exists to prevent
        — and blanking it persists a ``surface.created`` with no ``surface_id``,
        a ghost row the client fold skips without a word. The append fails
        instead, at the seam that would have written it.

        Restricted to ledger event types on purpose. ``w`` is a one-letter key,
        and an arbitrary tool or model payload is free to use it for something
        else entirely; only rows that speak the ledger vocabulary are read as
        speaking it.
        """

        if event_type.value not in _LedgerWriterStamp.EVENT_TYPES:
            return projected
        if projected is payload:
            # The default branch of ``_project_payload`` returns its argument;
            # this seam must never mutate a caller's dict.
            projected = dict(payload)
        projected.pop(_LedgerWriterStamp.KEY, None)
        writer = payload.get(_LedgerWriterStamp.KEY)
        if writer is None:
            writer = _LedgerWriterStamp.CURRENT
        elif writer not in _LedgerWriterStamp.KNOWN:
            logging.getLogger(__name__).error(
                "Refused a ledger append from an unknown writer event_type=%s",
                event_type.value,
            )
            raise UnknownLedgerWriterError.for_writer(writer)
        if projected:
            # An empty projection is the allow-list's rejection sentinel, pinned
            # by ``test_projector_rejects_uncontracted_payload_fields`` — an
            # uncontracted field projects to exactly ``{}`` so it cannot ride the
            # ledger. Signing it would both break that contract and risk making a
            # contentless row look processable to a reader that skips ``{}``.
            #
            # KNOWN GAP, deliberately left: the caller appends that row anyway
            # (``_artifact_ledger_payload`` / ``_effect_ledger_payload`` log and
            # return ``{}`` across 13 event types), so an unsigned, contentless
            # row can reach the store. It is inert — it carries no id for any
            # reader to key on — but it is not covered by the sentence above
            # about a bare row being historic. The fix belongs upstream, in
            # refusing the append, not here in widening the stamp.
            projected[_LedgerWriterStamp.KEY] = writer
        return projected

    @classmethod
    def _project_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Return the client-visible payload for an API event type."""

        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return cls._reasoning_summary_payload(
                event_type=event_type, payload=payload
            )
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return cls._mcp_auth_required_payload(payload)
        if event_type is RuntimeApiEventType.APPROVAL_REQUESTED:
            return cls._approval_requested_payload(payload)
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return cls._approval_forwarded_payload(payload)
        if event_type is RuntimeApiEventType.SOURCE_INGESTED:
            return cls._source_ingested_payload(payload)
        if event_type is RuntimeApiEventType.SOURCES_INGESTED:
            return cls._sources_ingested_payload(payload)
        if event_type is RuntimeApiEventType.CITATION_MADE:
            return cls._citation_made_payload(payload)
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            return cls._surface_spec_generated_payload(payload)
        if event_type is RuntimeApiEventType.USAGE_RECORDED:
            return cls._usage_recorded_payload(payload)
        if event_type is RuntimeApiEventType.ACTION_CLASSIFIED:
            return cls._action_classified_payload(payload)
        if event_type is RuntimeApiEventType.READ_EXECUTED:
            return cls._read_executed_payload(payload)
        if event_type is RuntimeApiEventType.SURFACE_CREATED:
            return cls._surface_created_payload(payload)
        if event_type is RuntimeApiEventType.VIEW_DERIVED:
            return cls._view_derived_payload(payload)
        if event_type is RuntimeApiEventType.VIEW_PREFERENCE:
            return cls._view_preference_payload(payload)
        if event_type is RuntimeApiEventType.SHAPE_REQUESTED:
            return cls._shape_requested_payload(payload)
        if event_type is RuntimeApiEventType.SHAPE_RESOLVED:
            return cls._shape_resolved_payload(payload)
        if event_type is RuntimeApiEventType.GATE_OPENED:
            return cls._gate_opened_payload(payload)
        if event_type is RuntimeApiEventType.GATE_RESOLVED:
            return cls._gate_resolved_payload(payload)
        if event_type is RuntimeApiEventType.WRITE_STAGED:
            return cls._write_staged_payload(payload)
        if event_type is RuntimeApiEventType.REVISION_ADDED:
            return cls._revision_added_payload(payload)
        if event_type is RuntimeApiEventType.DECISION_RECORDED:
            return cls._decision_recorded_payload(payload)
        if event_type is RuntimeApiEventType.WRITE_APPLIED:
            return cls._write_applied_payload(payload)
        if event_type is RuntimeApiEventType.RECEIPT_EMITTED:
            return cls._receipt_emitted_payload(payload)
        if event_type in {
            RuntimeApiEventType.ARTIFACT_CREATED,
            RuntimeApiEventType.ARTIFACT_REVISED,
            RuntimeApiEventType.ARTIFACT_PROMOTED,
            RuntimeApiEventType.ARTIFACT_PRESENTATION_DECIDED,
        }:
            return cls._artifact_ledger_payload(
                event_type=event_type,
                payload=payload,
            )
        if event_type in {
            RuntimeApiEventType.EFFECT_STAGED,
            RuntimeApiEventType.EFFECT_PROJECTION_BOUND,
            RuntimeApiEventType.EFFECT_REVISED,
            RuntimeApiEventType.EFFECT_DECISION_RECORDED,
            RuntimeApiEventType.EFFECT_CLAIMED,
            RuntimeApiEventType.EFFECT_APPLIED,
            RuntimeApiEventType.EFFECT_INDETERMINATE,
            RuntimeApiEventType.EFFECT_RECONCILED,
            RuntimeApiEventType.EFFECT_ROW_DECISIONS_RECORDED,
        }:
            return cls._effect_ledger_payload(
                event_type=event_type,
                payload=payload,
            )
        if event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND:
            return cls._quality_control_payload(payload)
        if event_type is RuntimeApiEventType.QUALITY_DECISION:
            return cls._quality_decision_payload(payload)
        if event_type is RuntimeApiEventType.TOOL_POLICY_JOURNAL:
            return cls._tool_policy_journal_payload(payload)
        if event_type is RuntimeApiEventType.PROMPT_ASSEMBLED:
            return cls._prompt_assembled_payload(payload)
        if event_type is RuntimeApiEventType.PROMPT_CACHE_OBSERVED:
            return cls._prompt_cache_observed_payload(payload)
        if event_type in {
            RuntimeApiEventType.MODEL_INVOCATION_PLANNED,
            RuntimeApiEventType.MODEL_INVOCATION_ROUTE,
            RuntimeApiEventType.MODEL_INVOCATION_EXCLUSION,
            RuntimeApiEventType.MODEL_ATTEMPT_ADMISSION,
            RuntimeApiEventType.MODEL_ATTEMPT_STATE,
            RuntimeApiEventType.MODEL_ATTEMPT_USAGE,
            RuntimeApiEventType.MODEL_ATTEMPT_FAILED,
            RuntimeApiEventType.MODEL_INVOCATION_RECOVERY,
            RuntimeApiEventType.MODEL_INVOCATION_COMPLETED,
            RuntimeApiEventType.MODEL_INVOCATION_FAILED,
        }:
            return cls._model_invocation_payload(payload)
        if event_type is RuntimeApiEventType.CONTEXT_OCCUPANCY:
            return cls._context_occupancy_payload(payload)
        if event_type is RuntimeApiEventType.OPERATION_REQUESTED:
            return cls._operation_requested_payload(payload)
        if event_type is RuntimeApiEventType.OPERATION_CLASSIFIED:
            return cls._operation_classified_payload(payload)
        if event_type is RuntimeApiEventType.OPERATION_COMPLETED:
            return cls._operation_completed_payload(payload)
        if event_type is RuntimeApiEventType.OPERATION_FAILED:
            return cls._operation_failed_payload(payload)
        return payload

    @classmethod
    def presentation_fields(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        parent_task_id: str | None,
        payload: JsonObject,
        metadata: JsonObject,
        subagent_id: str | None = None,
    ) -> dict[str, object]:
        """Return additive UI timeline fields for an event envelope or draft."""

        task_id = cls._text(payload.get(Keys.Field.TASK_ID)) or parent_task_id
        subagent_id = (
            cls._text(subagent_id)
            or cls._text(payload.get(Keys.Field.SUBAGENT_NAME))
            or cls._text(payload.get(Keys.Field.SUBAGENT_ID))
        )
        span_id = cls._span_id_for(
            event_type=event_type, task_id=task_id, payload=payload
        )
        return {
            Keys.Field.PARENT_EVENT_ID: cls._text(
                payload.get(Keys.Field.PARENT_EVENT_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_EVENT_ID)),
            Keys.Field.SPAN_ID: span_id,
            Keys.Field.PARENT_SPAN_ID: cls._text(
                payload.get(Keys.Field.PARENT_SPAN_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_SPAN_ID))
            or parent_task_id,
            Keys.Field.TASK_ID: task_id,
            Keys.Field.SUBAGENT_ID: subagent_id,
            Keys.Field.DISPLAY_TITLE: cls._display_title_for(
                event_type=event_type,
                payload=payload,
            ),
            Keys.Field.SUMMARY: cls._summary_for(payload=payload, metadata=metadata),
            Keys.Field.STATUS: cls._status_for(event_type=event_type, payload=payload),
            _Fields.ACTIVITY_KIND: cls.activity_kind_for(
                event_type=event_type, source=source
            ),
            Keys.Field.VISIBILITY: cls._visibility_for(source=source, payload=payload),
            Keys.Field.REDACTION_STATE: cls._redaction_state_for(
                payload=payload,
                metadata=metadata,
            ),
        }

    @classmethod
    def presentation_metadata(
        cls, metadata: JsonObject
    ) -> RuntimeEventPresentation | None:
        """Extract and validate the ``presentation`` sub-object from event metadata, or return None."""
        raw = metadata.get("presentation")
        if not isinstance(raw, dict):
            return None
        try:
            return RuntimeEventPresentation.model_validate(raw)
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to validate presentation metadata", exc_info=True
            )
            return None

    @classmethod
    def activity_kind_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
    ) -> RuntimeActivityKind:
        """Project transport event details into a stable client activity bucket."""

        if event_type is RuntimeApiEventType.HEARTBEAT:
            return RuntimeActivityKind.HEARTBEAT
        if event_type is RuntimeApiEventType.PRESENTATION_UPDATED:
            return RuntimeActivityKind.EVENT
        if event_type in {
            RuntimeApiEventType.MODEL_DELTA,
            RuntimeApiEventType.FINAL_RESPONSE,
        }:
            return RuntimeActivityKind.MESSAGE
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return RuntimeActivityKind.REASONING
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return RuntimeActivityKind.MCP_AUTH
        if event_type is RuntimeApiEventType.DRAFT_UPDATED:
            return RuntimeActivityKind.DRAFT
        if event_type is RuntimeApiEventType.TODO_LIST_UPDATED:
            # The agent's checklist is state the todo panel folds, not a card
            # per revision — a five-step plan would otherwise put five
            # near-identical rows on the timeline. Explicit rather than left to
            # the default because the emit is TOOL-sourced (it is projected off
            # a ``write_todos`` frame), and the source fallback would route it
            # into the tool bucket, which is the very rendering this replaces.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            # Generative-UI (PRD-01) — an out-of-band "prepared a view" note.
            # Explicit so a TOOL-sourced emit can't reroute it into the tool
            # bucket; the FE consumes it as a surface-state merge, not a card.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.USAGE_RECORDED:
            # Generative Surfaces v2 (PRD-A2) — a metering ledger event, not a
            # timeline card. Explicit (matches the default) so a MODEL-sourced
            # emit can't be rerouted; A3's UsageTotals fold consumes it.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.CONTEXT_OCCUPANCY:
            # Context Occupancy Ledger (§7) — an occupancy meter is state to
            # merge, not a card on the timeline. Stated explicitly rather than
            # left to the default because measurement happens inside the model
            # call: the emit is MODEL-sourced, and a source-driven fallback would
            # be one refactor away from routing it into a message bucket. It also
            # carries no display title or status for the same reason — there is
            # no per-turn "Measured the context window" beat to render.
            return RuntimeActivityKind.EVENT
        if event_type in {
            RuntimeApiEventType.ACTION_CLASSIFIED,
            RuntimeApiEventType.READ_EXECUTED,
            RuntimeApiEventType.SURFACE_CREATED,
            RuntimeApiEventType.VIEW_DERIVED,
            RuntimeApiEventType.VIEW_PREFERENCE,
            RuntimeApiEventType.SHAPE_REQUESTED,
            RuntimeApiEventType.SHAPE_RESOLVED,
            RuntimeApiEventType.GATE_OPENED,
            RuntimeApiEventType.GATE_RESOLVED,
            RuntimeApiEventType.WRITE_STAGED,
            RuntimeApiEventType.REVISION_ADDED,
            RuntimeApiEventType.DECISION_RECORDED,
            RuntimeApiEventType.WRITE_APPLIED,
            RuntimeApiEventType.RECEIPT_EMITTED,
            RuntimeApiEventType.ARTIFACT_CREATED,
            RuntimeApiEventType.ARTIFACT_REVISED,
            RuntimeApiEventType.ARTIFACT_PROMOTED,
            RuntimeApiEventType.ARTIFACT_PRESENTATION_DECIDED,
            RuntimeApiEventType.OPERATION_REQUESTED,
            RuntimeApiEventType.OPERATION_CLASSIFIED,
            RuntimeApiEventType.OPERATION_COMPLETED,
            RuntimeApiEventType.OPERATION_FAILED,
            RuntimeApiEventType.EFFECT_STAGED,
            RuntimeApiEventType.EFFECT_PROJECTION_BOUND,
            RuntimeApiEventType.EFFECT_REVISED,
            RuntimeApiEventType.EFFECT_DECISION_RECORDED,
            RuntimeApiEventType.EFFECT_CLAIMED,
            RuntimeApiEventType.EFFECT_APPLIED,
            RuntimeApiEventType.EFFECT_INDETERMINATE,
            RuntimeApiEventType.EFFECT_RECONCILED,
            RuntimeApiEventType.EFFECT_ROW_DECISIONS_RECORDED,
            RuntimeApiEventType.TOOL_POLICY_JOURNAL,
            RuntimeApiEventType.PROMPT_ASSEMBLED,
            RuntimeApiEventType.PROMPT_CACHE_OBSERVED,
            RuntimeApiEventType.MODEL_INVOCATION_PLANNED,
            RuntimeApiEventType.MODEL_INVOCATION_ROUTE,
            RuntimeApiEventType.MODEL_INVOCATION_EXCLUSION,
            RuntimeApiEventType.MODEL_ATTEMPT_ADMISSION,
            RuntimeApiEventType.MODEL_ATTEMPT_STATE,
            RuntimeApiEventType.MODEL_ATTEMPT_USAGE,
            RuntimeApiEventType.MODEL_ATTEMPT_FAILED,
            RuntimeApiEventType.MODEL_INVOCATION_RECOVERY,
            RuntimeApiEventType.MODEL_INVOCATION_COMPLETED,
            RuntimeApiEventType.MODEL_INVOCATION_FAILED,
        }:
            # Generative Surfaces v2 (PRD-A3/B3/C2/D1/D2/E1) — ledger events the SurfaceStore
            # + client ledger fold consume as surface/gate-state merges, never
            # timeline cards. Explicit so a TOOL/SYSTEM-sourced emit can't reroute
            # into the tool bucket. The gate pair rides beside the
            # ``mcp_auth_required`` approval (which keeps its own MCP_AUTH kind);
            # the canvas gate card + posture chip read these, not the legacy
            # approval event, when the v2 flag is on.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.COMPRESSION_NOTE:
            # PR A1 — context-compression note. Renders as an inline
            # dim line ("Atlas summarised 3 older messages…") rather
            # than a card; FE consumes via `<NoteCard>`.
            return RuntimeActivityKind.NOTE
        if event_type in {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.APPROVAL_RESOLVED,
            RuntimeApiEventType.APPROVAL_FORWARDED,
        }:
            return RuntimeActivityKind.APPROVAL
        if source is StreamEventSource.TOOL or event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.SOURCE_INGESTED,
            RuntimeApiEventType.SOURCES_INGESTED,
            RuntimeApiEventType.CITATION_MADE,
        }:
            return RuntimeActivityKind.TOOL
        if source is StreamEventSource.SUBAGENT or event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            # PR A2 — fleet group bookends share the SUBAGENT bucket so
            # the FE can render fleets and singletons through the same
            # reducer; per-event `parent_fleet_id` discriminates.
            RuntimeApiEventType.SUBAGENT_FLEET_STARTED,
            RuntimeApiEventType.SUBAGENT_FLEET_FINISHED,
        }:
            return RuntimeActivityKind.SUBAGENT
        if event_type in {
            RuntimeApiEventType.RUN_QUEUED,
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.RUN_CANCELLING,
            RuntimeApiEventType.RUN_CANCELLED,
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.RUN_FAILED,
            RuntimeApiEventType.MODEL_CALL_STARTED,
        }:
            return RuntimeActivityKind.RUN
        return RuntimeActivityKind.EVENT

    @classmethod
    def _event_type_override(
        cls,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeApiEventType | None:
        value = cls._text(payload.get(Keys.Field.API_EVENT_TYPE)) or cls._text(
            metadata.get(Keys.Field.API_EVENT_TYPE)
        )
        if value is None:
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def _subagent_event_type(cls, payload: JsonObject) -> RuntimeApiEventType:
        status = cls._status_text(payload)
        if status in cls.SUBAGENT_STARTED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_STARTED
        if status in cls.SUBAGENT_COMPLETED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_COMPLETED
        return RuntimeApiEventType.SUBAGENT_PROGRESS

    @classmethod
    def _reasoning_summary_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        summary = cls._text(payload.get(Keys.Field.SUMMARY)) or cls._text(
            payload.get(Keys.Payload.MESSAGE)
        )
        safe_payload: JsonObject = {}
        if summary is not None:
            safe_payload[Keys.Field.SUMMARY] = summary
        if event_type is RuntimeApiEventType.REASONING_SUMMARY_DELTA:
            delta = cls._text(payload.get(Keys.Payload.DELTA))
            if delta is not None:
                safe_payload[Keys.Payload.DELTA] = delta
        return safe_payload

    @classmethod
    def _span_id_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        task_id: str | None,
        payload: JsonObject,
    ) -> str | None:
        configured_span_id = cls._text(payload.get(Keys.Field.SPAN_ID))
        if configured_span_id is not None:
            return configured_span_id
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return cls._text(payload.get(Keys.Field.CALL_ID))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        }:
            return task_id
        return None

    @classmethod
    def _display_title_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._text(payload.get(Keys.Field.DISPLAY_TITLE)) or cls._text(
            payload.get(Keys.Payload.DISPLAY_TITLE)
        )
        if configured is not None:
            return configured
        # Use the dispatcher-unwrap helper so ``call_mcp_tool`` events render
        # their inner tool name (e.g. ``"list_issues"``) instead of the raw
        # dispatcher name. For non-dispatcher events the helper just returns
        # ``payload.tool_name`` verbatim. Imported lazily (see module docstring
        # at top) to avoid a circular import during ``agent_runtime`` init.
        from agent_runtime.capabilities.mcp.dispatcher import McpDispatcherUnwrap

        tool_name = McpDispatcherUnwrap.effective_tool_name(payload)
        if event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_started_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_CALL_DELTA:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_running_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_RESULT:
            if tool_name is None:
                return Messages.Event.TOOL_RESULT
            return Messages.Event.tool_result_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_CALL_COMPLETED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_completed_title(tool_name)
        subagent_name = cls._text(payload.get(Keys.Field.SUBAGENT_NAME))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.SUBAGENT_UPDATE,
        }:
            if subagent_name is None:
                return Messages.Event.SUBAGENT
            return Messages.Event.subagent_title(subagent_name)
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return Messages.Event.REASONING
        if event_type is RuntimeApiEventType.MODEL_DELTA:
            return Messages.Event.MODEL_DELTA
        if event_type is RuntimeApiEventType.FINAL_RESPONSE:
            return Messages.Event.FINAL_RESPONSE
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return Messages.Event.MCP_AUTH_REQUIRED
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return Messages.Event.APPROVAL_FORWARDED
        if event_type is RuntimeApiEventType.SOURCE_INGESTED:
            citation = payload.get(_Fields.CITATION)
            if isinstance(citation, dict):
                title = cls._text(citation.get(Keys.Field.TITLE))
                if title is not None:
                    return Messages.Event.source_cited_title(title)
            return Messages.Event.SOURCE_INGESTED
        if event_type is RuntimeApiEventType.SOURCES_INGESTED:
            citations = payload.get("citations")
            if isinstance(citations, list) and citations:
                return Messages.Event.sources_cited_title(len(citations))
            return Messages.Event.SOURCES_INGESTED
        if event_type is RuntimeApiEventType.CITATION_MADE:
            link = payload.get(_Fields.LINK)
            if isinstance(link, dict):
                ordinal = link.get(_Fields.CONVERSATION_ORDINAL)
                if isinstance(ordinal, int) and ordinal > 0:
                    return Messages.Event.citation_made_title(ordinal)
            return Messages.Event.CITATION_MADE
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            # Generative-UI (PRD-01). The user-facing message class lives in
            # ``agent_runtime.api.constants`` (out of this PR's scope); the
            # single-use title is inlined here until PRD-02 wires the emitter.
            return _Fields.SURFACE_PREPARED_TITLE
        if event_type is RuntimeApiEventType.WRITE_APPLIED:
            # Generative Surfaces v2 (PRD-D2, FR-C3). ``applied`` shows the
            # requirement microcopy verbatim; ``failed`` shows the refusal line.
            result = cls._text(payload.get(_LedgerKeys.Field.RESULT))
            if result == _LedgerValues.RESULT_FAILED:
                return _Fields.WRITE_FAILED_TITLE
            return _Fields.WRITE_APPLIED_TITLE
        if event_type is RuntimeApiEventType.RECEIPT_EMITTED:
            # Generative Surfaces v2 (PRD-E1, FR-E2) — the accountability seal.
            return _Fields.RECEIPT_EMITTED_TITLE
        return None

    @classmethod
    def _summary_for(cls, *, payload: JsonObject, metadata: JsonObject) -> str | None:
        return (
            cls._text(payload.get(Keys.Field.SUMMARY))
            or cls._text(payload.get(Keys.Payload.MESSAGE))
            or cls._text(metadata.get(Keys.Field.SUMMARY))
        )

    @classmethod
    def _status_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._status_text(payload)
        if configured is not None:
            return configured
        if event_type in {RuntimeApiEventType.RUN_QUEUED}:
            return Values.Status.QUEUED
        if event_type in {
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.MODEL_CALL_STARTED,
        }:
            return Values.Status.STARTED
        if event_type in {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            RuntimeApiEventType.MODEL_DELTA,
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.TOOL_CALL_DELTA,
        }:
            return Values.Status.RUNNING
        if event_type in {
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.FINAL_RESPONSE,
            RuntimeApiEventType.SOURCE_INGESTED,
            RuntimeApiEventType.SOURCES_INGESTED,
            RuntimeApiEventType.CITATION_MADE,
        }:
            return Values.Status.COMPLETED
        # PR 1.4 — forwarded approvals project as a non-terminal "waiting"
        # status so the FE renders the card as "Waiting on @marcus" (not
        # "Done"). The actual terminal state is the child's APPROVAL_RESOLVED.
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return Values.Status.WAITING
        if event_type in {RuntimeApiEventType.RUN_FAILED, RuntimeApiEventType.ERROR}:
            return Values.Status.FAILED
        if event_type is RuntimeApiEventType.RUN_CANCELLED:
            return Values.Status.CANCELLED
        return None

    @classmethod
    def _mcp_auth_required_payload(cls, payload: JsonObject) -> JsonObject:
        safe_payload: JsonObject = {}
        # PR 3.3 — ``DISCOVERY_REASON`` and ``EXPECTED_VALUE`` are optional
        # additions that flip the FE card variant from blocking auth-gate
        # to non-blocking Connect/Skip suggestion. Both pass through the
        # same allow-list — emitters never set them on a blocking call.
        for key in (
            Keys.Field.APPROVAL_ID,
            "action_id",
            Keys.Field.APPROVAL_KIND,
            Keys.Field.BATCH_ID,
            Keys.Field.SERVER_ID,
            Keys.Field.SERVER_NAME,
            "display_name",
            Keys.Field.AUTH_URL,
            Keys.Field.EXPIRES_AT,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
            Keys.Field.DISCOVERY_REASON,
            Keys.Field.EXPECTED_VALUE,
            # The consent card's trust line — the scope the connector is being
            # granted, the host the sign-in will actually open, and which of the
            # two connector surfaces raised the card. All three are derived
            # server-side (see ``agent_runtime.api.connector_trust``); they ride
            # the same allow-list so a blocking gate and a suggestion make
            # identical promises. ``AUTH_HOST`` is legitimately empty when no
            # auth session was issued, so it is emitted via ``_text_or_empty``
            # below rather than dropped by the non-empty filter here.
            Keys.Field.ACCESS_MODE,
            Keys.Field.SOURCE_TOOL,
            # PR 4.4.7 Phase 2 (Slice C) — present iff the suggestion
            # came from the catalog (uninstalled connector). The FE
            # branches Connect on this so it routes to the install
            # flow rather than starting OAuth against a server row
            # that doesn't exist yet.
            "catalog_slug",
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        # ``auth_host`` carries meaning when EMPTY: no auth session was issued,
        # so the card has no sign-in host it can honestly name and drops that
        # clause. The non-empty filter above would erase that signal and let the
        # client fall back to guessing, so the key is projected explicitly
        # whenever the emitter set it at all.
        auth_host = payload.get(Keys.Field.AUTH_HOST)
        if isinstance(auth_host, str):
            safe_payload[Keys.Field.AUTH_HOST] = auth_host
        # PR 4.4.7 follow-up — boolean flag (string-only ``_text``
        # would coerce False to None). Pass through bool values
        # verbatim; absent/non-bool keys are dropped.
        requires_pre = payload.get("requires_pre_registered_client")
        if isinstance(requires_pre, bool):
            safe_payload["requires_pre_registered_client"] = requires_pre
        # PR #43 — preserve typed batch_index through projection so the FE
        # receives it alongside batch_id on every approval-style event.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
        return safe_payload

    @classmethod
    def _source_ingested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``source_ingested`` payloads through a strict allow-list.

        The CitationLedger is the only intended emitter and always supplies
        the full ``CitationSourceRef`` shape, but we whitelist defensively
        in case a future caller (e.g. a provider adapter) over-shares.
        """

        citation = payload.get(_Fields.CITATION)
        safe_citation = cls._safe_citation_ref(citation)
        if safe_citation is None:
            return {}
        return {_Fields.CITATION: safe_citation}

    @classmethod
    def _sources_ingested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``sources_ingested`` payloads through a strict allow-list.

        Plural variant of :meth:`_source_ingested_payload` (P7). Iterates
        ``payload.citations`` and applies the same per-citation allow-list,
        preserving order so the FE registry sees ordinals in the order the
        ledger allocated them.
        """

        citations = payload.get("citations")
        if not isinstance(citations, list):
            return {"citations": []}
        safe_citations: list[JsonObject] = []
        for citation in citations:
            safe = cls._safe_citation_ref(citation)
            if safe is not None:
                safe_citations.append(safe)
        return {"citations": safe_citations}

    @staticmethod
    def _safe_citation_ref(value: object) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        safe: JsonObject = {}
        for text_key in (
            "citation_id",
            "source_connector",
            "source_doc_id",
            "source_url",
            "title",
            "snippet",
            "freshness_at",
            "source_tool_call_id",
        ):
            v = value.get(text_key)
            if isinstance(v, str) and v.strip():
                safe[text_key] = v
            elif v is None and text_key in {
                "source_url",
                "snippet",
                "freshness_at",
                "source_tool_call_id",
            }:
                safe[text_key] = None
        ordinal = value.get("ordinal")
        if isinstance(ordinal, int) and ordinal > 0:
            safe["ordinal"] = ordinal
        return safe

    @classmethod
    def _citation_made_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``citation_made`` payloads through a strict allow-list.

        The CitationResolver is the only intended emitter and always supplies
        the full ``CitationLink`` shape (conversation_ordinal, message_id,
        prose offsets, source_tool_call_id). We whitelist defensively so a
        future emitter can't over-share.
        """

        link = payload.get(_Fields.LINK)
        if not isinstance(link, dict):
            return {}
        safe_link: JsonObject = {}
        ordinal = link.get(_Fields.CONVERSATION_ORDINAL)
        if isinstance(ordinal, int) and ordinal > 0:
            safe_link[_Fields.CONVERSATION_ORDINAL] = ordinal
        # ``message_id`` must be a non-empty string — without it the FE
        # cannot key the chip back to its assistant message.
        message_id = link.get(_Fields.MESSAGE_ID)
        if isinstance(message_id, str) and message_id.strip():
            safe_link[_Fields.MESSAGE_ID] = message_id
        # ``source_tool_call_id`` is *allowed* to be empty: when the
        # model emits ``[[N]]`` for an ordinal the allocator hasn't
        # bound to a tool_call_id (hallucinated ordinal, or
        # provider-native passthrough that fired before the tool
        # message materialized), the resolver still emits the event so
        # the chip can render — the FE renders it as a muted
        # placeholder when the call_id is empty. Preserve the field as
        # a string (possibly empty) so the FE type guard accepts it.
        source_tool_call_id = link.get(_Fields.SOURCE_TOOL_CALL_ID)
        if isinstance(source_tool_call_id, str):
            safe_link[_Fields.SOURCE_TOOL_CALL_ID] = source_tool_call_id
        else:
            safe_link[_Fields.SOURCE_TOOL_CALL_ID] = ""
        for offset_key in (_Fields.PROSE_OFFSET, _Fields.PROSE_LENGTH):
            value = link.get(offset_key)
            if isinstance(value, int) and value >= 0:
                safe_link[offset_key] = value
        return {_Fields.LINK: safe_link}

    @classmethod
    def _surface_spec_generated_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``surface_spec_generated`` payloads through a strict allow-list.

        The async spec generator (PRD-07) is the intended emitter; we whitelist
        defensively so a future caller cannot over-share. ``spec`` is the
        SurfaceSpec dict — passed through only when it is an object (it was
        schema-validated upstream, and the SurfaceSpec schema has no
        side-effectful members, plan D9). PRD-01 freezes this projection; no
        emitter exists yet.
        """

        safe_payload: JsonObject = {}
        for text_key in (
            _Fields.SURFACE_URI,
            _Fields.ARCHETYPE,
            _Fields.GENERATOR_MODEL,
            _Fields.SKILL_VERSION,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        spec_version = payload.get(_Fields.SPEC_VERSION)
        if isinstance(spec_version, int) and not isinstance(spec_version, bool):
            safe_payload[_Fields.SPEC_VERSION] = spec_version
        spec = payload.get(_Fields.SPEC)
        if isinstance(spec, dict):
            safe_payload[_Fields.SPEC] = spec
        return safe_payload

    @classmethod
    def _usage_recorded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``usage.recorded`` payloads through a strict allow-list.

        Keeps exactly the SDR §5 fields — ``v`` / ``purpose`` / ``model`` /
        ``tokens_in`` / ``tokens_out`` / ``surface_id`` — so an emitter can
        never over-share (tenant ids stay off the envelope; ``surface_id`` is
        optional). The :class:`UsageMeter` is the intended emitter (PRD-A2);
        this projection re-filters on append regardless.
        """

        safe_payload: JsonObject = {}
        version = payload.get(_Fields.USAGE_V)
        if isinstance(version, int) and not isinstance(version, bool):
            safe_payload[_Fields.USAGE_V] = version
        for text_key in (
            _Fields.USAGE_PURPOSE,
            _Fields.USAGE_MODEL,
            _Fields.USAGE_SURFACE_ID,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        for token_key in (_Fields.USAGE_TOKENS_IN, _Fields.USAGE_TOKENS_OUT):
            tokens = payload.get(token_key)
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                safe_payload[token_key] = tokens
        return safe_payload

    @classmethod
    def _operation_requested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project the operation identity/digest row without argument content."""

        safe_payload = cls._operation_base(payload)
        for text_key in (
            _OperationFields.PRODUCER,
            _OperationFields.CAPABILITY,
            _OperationFields.OP,
            _OperationFields.ARGS_DIGEST,
            _OperationFields.PARENT_OPERATION_ID,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _operation_classified_payload(cls, payload: JsonObject) -> JsonObject:
        """Project bounded classification facts; reasons and arguments stay private."""

        safe_payload = cls._operation_base(payload)
        for text_key in (
            _OperationFields.EFFECT_CLASS,
            _OperationFields.BASIS,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        confidence = payload.get(_OperationFields.CONFIDENCE)
        if (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and 0 <= confidence <= 1
        ):
            safe_payload[_OperationFields.CONFIDENCE] = confidence
        return safe_payload

    @classmethod
    def _operation_completed_payload(cls, payload: JsonObject) -> JsonObject:
        """Project a bounded outcome and optional immutable result reference."""

        safe_payload = cls._operation_base(payload)
        for text_key in (
            _OperationFields.OUTCOME,
            _OperationFields.RESULT_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        latency_ms = payload.get(_OperationFields.LATENCY_MS)
        if (
            isinstance(latency_ms, int)
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            safe_payload[_OperationFields.LATENCY_MS] = latency_ms
        return safe_payload

    @classmethod
    def _operation_failed_payload(cls, payload: JsonObject) -> JsonObject:
        """Project a safe code and retry hint; exception text is never exposed."""

        safe_payload = cls._operation_base(payload)
        failure_code = cls._text(payload.get(_OperationFields.FAILURE_CODE))
        if failure_code is not None:
            safe_payload[_OperationFields.FAILURE_CODE] = failure_code
        retryable = payload.get(_OperationFields.RETRYABLE)
        if isinstance(retryable, bool):
            safe_payload[_OperationFields.RETRYABLE] = retryable
        return safe_payload

    @classmethod
    def _operation_base(cls, payload: JsonObject) -> JsonObject:
        safe_payload: JsonObject = {}
        version = payload.get(_OperationFields.VERSION)
        if isinstance(version, int) and not isinstance(version, bool):
            safe_payload[_OperationFields.VERSION] = version
        operation_id = cls._text(payload.get(_OperationFields.OPERATION_ID))
        if operation_id is not None:
            safe_payload[_OperationFields.OPERATION_ID] = operation_id
        return safe_payload

    @classmethod
    def _action_classified_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``action.classified`` through a strict allow-list (PRD-A3 D5).

        Keeps exactly the SDR §5 fields — ``v`` / ``call_id`` / ``connector`` /
        ``op`` / ``class`` / ``basis`` — so an emitter can never over-share. In
        Wave A ``class`` is always ``"unknown"`` and ``basis`` ``"default"`` (no
        classifier yet); this projection re-filters regardless of the emitter.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.CALL_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.OP,
            _LedgerKeys.Field.CLASS,
            _LedgerKeys.Field.BASIS,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _read_executed_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``read.executed`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``call_id`` / ``connector`` / ``op`` / ``payload_ref`` and
        the optional non-negative ``latency_ms``. ``payload_ref`` trips the
        ``"ref"``-key OFFLOADED marker in ``_redaction_state_for`` — correct, it
        *is* a reference.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.CALL_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.OP,
            _LedgerKeys.Field.PAYLOAD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        latency = payload.get(_LedgerKeys.Field.LATENCY_MS)
        if isinstance(latency, int) and not isinstance(latency, bool) and latency >= 0:
            safe_payload[_LedgerKeys.Field.LATENCY_MS] = latency
        return safe_payload

    @classmethod
    def _surface_created_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``surface.created`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``surface_id`` / ``kind`` / ``source{connector,op}`` /
        ``title`` / ``payload_ref`` / optional ``state``. ``source`` is re-built
        from its own nested allow-list so untrusted extra keys cannot ride
        through, and ``state`` from :meth:`_surface_state`.

        This method is why the generative-UI floor was inert in production for
        a release: an allow-list that does not name a key **deletes it**, with
        no error at any layer, so a spec resolved correctly backend-side simply
        never arrived and the client rendered the spec-less view. That failure
        mode is silent by construction, which is exactly why every widening of
        the surface payload has to land here in the same pass.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.KIND,
            _LedgerKeys.Field.TITLE,
            _LedgerKeys.Field.PAYLOAD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        source = cls._op_ref(payload.get(_LedgerKeys.Field.SOURCE))
        if source is not None:
            safe_payload[_LedgerKeys.Field.SOURCE] = source
        state = cls._surface_state(payload.get(_SurfaceStateFields.STATE))
        if state is not None:
            safe_payload[_SurfaceStateFields.STATE] = state
        return safe_payload

    @classmethod
    def _surface_state(cls, value: object) -> JsonObject | None:
        """Re-validate the carried ``{spec?, source?, data}`` renderer state.

        This value reaches the client and decides what a user is SHOWN, so it
        is rebuilt member by member rather than passed through as a dict — the
        same posture the consent card's ``presentation`` block takes. Arriving
        on a trusted event type is not evidence that a payload is well formed:
        ``data`` is connector output and ``spec`` may have been generated by a
        model, and neither is trusted anywhere else in this file.

        Each member degrades on its own. A malformed spec is dropped and the
        client renders the deterministic inference floor — a correct surface
        rather than a wrong one. A half-named source is dropped rather than
        printing a blank where a tool name belongs. ``data`` is carried
        verbatim: it is untrusted tool output that the renderers treat as
        inert, and it is the same payload the run already publishes on
        ``tool_result``, so re-shaping it here would put a second, disagreeing
        representation of one read on the wire — the defect this field exists
        to close.
        """

        if not isinstance(value, dict):
            return None
        state: JsonObject = {}
        spec = cls._surface_spec(value.get(_LedgerKeys.Field.SPEC))
        if spec is not None:
            state[_LedgerKeys.Field.SPEC] = spec
        source = cls._surface_state_source(value.get(_SurfaceStateFields.SOURCE))
        if source is not None:
            state[_SurfaceStateFields.SOURCE] = source
        if _SurfaceStateFields.DATA in value:
            state[_SurfaceStateFields.DATA] = value[_SurfaceStateFields.DATA]
        return state or None

    @classmethod
    def _surface_state_source(cls, value: object) -> JsonObject | None:
        """Rebuild the renderer's ``{server, tool}`` provenance, or ``None``.

        Sibling of :meth:`_op_ref`, which rebuilds the ledger's
        ``{connector, op}`` spelling of the same two facts. Both members must
        resolve: a source naming only its server would put "unknown" in front of
        the user in a register that reads as "this is what the system knows".
        """

        if not isinstance(value, dict):
            return None
        server = cls._text(value.get(_SurfaceStateFields.SERVER))
        tool = cls._text(value.get(_SurfaceStateFields.TOOL))
        if server is None or tool is None:
            return None
        return {_SurfaceStateFields.SERVER: server, _SurfaceStateFields.TOOL: tool}

    @classmethod
    def _surface_spec(cls, value: object) -> JsonObject | None:
        """Validate a ledger-carried SurfaceSpec, or ``None`` if it is not one.

        Total by construction: the surface path is best-effort presentation, so
        a spec that does not validate costs the client its shaping and nothing
        else. Round-tripping through the canonical validator (rather than
        hand-checking keys) keeps this in step with the schema automatically.
        """

        if not isinstance(value, dict):
            return None
        try:
            spec = validate_surface_spec(value)
        except SurfaceSpecError:
            return None
        return spec.model_dump(mode="json", exclude_none=True)

    @classmethod
    def _view_derived_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``view.derived`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``surface_id`` / ``tier`` / ``basis`` / optional
        ``spec_ref`` / optional ``gen{model}``. ``gen`` is re-built from a nested
        allow-list (``ms`` is not measured in A3, so only ``model`` survives).

        ``tier`` and ``basis`` are closed vocabularies rather than free text and
        are filtered as such (:class:`_ViewDerivedVocabulary`) — this is the
        provenance a receipt reads, and a basis nobody declared is worse than no
        basis at all.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key, vocabulary in (
            (_LedgerKeys.Field.SURFACE_ID, None),
            (_LedgerKeys.Field.TIER, _ViewDerivedVocabulary.TIER),
            (_LedgerKeys.Field.BASIS, _ViewDerivedVocabulary.BASIS),
            (_LedgerKeys.Field.SPEC_REF, None),
        ):
            raw_value = payload.get(text_key)
            value = (
                cls._text(raw_value)
                if vocabulary is None
                else cls._vocabulary_text(raw_value, vocabulary)
            )
            if value is not None:
                safe_payload[text_key] = value
        gen = payload.get(_LedgerKeys.Field.GEN)
        if isinstance(gen, dict):
            model = cls._text(gen.get(_LedgerKeys.Field.MODEL))
            if model is not None:
                safe_gen: JsonObject = {_LedgerKeys.Field.MODEL: model}
                # PRD-B3 widens A3's ``gen`` allow-list to admit the generation
                # duration ``ms`` (int) the ViewDeriver now populates. Without it
                # the projector would silently drop ``gen.ms`` and the B3 payload
                # spec (``gen: {model, ms}``) would not survive the wire.
                ms = gen.get(_LedgerKeys.Field.MS)
                if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
                    safe_gen[_LedgerKeys.Field.MS] = ms
                safe_payload[_LedgerKeys.Field.GEN] = safe_gen
        return safe_payload

    @classmethod
    def _view_preference_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``view.preference`` through a strict allow-list (PRD-B3).

        Keeps exactly the SDR §5 fields — ``v`` / ``surface_id`` / ``keep`` /
        ``actor`` — so a user-initiated preference append can never over-share.
        ``keep`` and ``actor`` are constrained value strings; anything else is
        dropped (the ledger append re-filters regardless of the caller).
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.KEEP,
            _LedgerKeys.Field.ACTOR,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _shape_requested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``shape.requested`` through a strict allow-list (PRD-B4).

        Keeps exactly the SDR §5 fields — ``v`` / ``surface_id`` / ``actor`` — so
        a user-invited request append can never over-share.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.ACTOR,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _shape_resolved_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``shape.resolved`` through a strict allow-list (PRD-B4).

        Keeps ``v`` / ``surface_id`` / ``outcome`` / optional ``reason``. The
        ``reason`` is the safe lint/validation summary the runner already
        sanitised (never raw model output); re-filtered here as defence in depth.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.OUTCOME,
            _LedgerKeys.Field.REASON,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _gate_opened_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``gate.opened`` through a strict allow-list (PRD-C2, SDR §5).

        Keeps exactly ``v`` / ``gate_id`` / ``connector`` / ``purpose`` /
        ``display_title`` / ``scopes[]`` / ``auth_state`` — so a gate emit can
        never over-share (the interrupt payload carries the connect URL +
        display copy; none of it rides the ledger row). ``scopes`` is re-built
        from its own list so a non-string element can't slip through.

        ``display_title`` is the human sibling of ``purpose`` and is subject to
        the same rule: the emitter builds it from the op + connector tokens
        ONLY, so admitting it here cannot let a tool argument onto the ledger.
        It is optional — a connect gate omits it, because its ``purpose`` is
        already written for a person.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.GATE_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.PURPOSE,
            _LedgerKeys.Field.DISPLAY_TITLE,
            _LedgerKeys.Field.AUTH_STATE,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        scopes = payload.get(_LedgerKeys.Field.SCOPES)
        if isinstance(scopes, (list, tuple)):
            safe_payload[_LedgerKeys.Field.SCOPES] = [
                s for s in scopes if isinstance(s, str)
            ]
        return safe_payload

    @classmethod
    def _gate_resolved_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``gate.resolved`` through a strict allow-list (PRD-C2, SDR §5).

        Keeps ``v`` / ``gate_id`` / ``outcome`` and the optional ``write_policy``
        (``ask_first`` / ``allow_always``). Nothing else survives.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.GATE_ID,
            _LedgerKeys.Field.OUTCOME,
            _LedgerKeys.Field.WRITE_POLICY,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _write_staged_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``write.staged`` through a strict allow-list (PRD-D1, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``surface_id`` / ``target{connector,op}`` /
        ``proposal_ref`` — the single-artifact shape (``rows`` / ``agent_holds``
        are D3). ``target`` is rebuilt from its own allow-list so no extra keys
        ride through.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.PROPOSAL_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        target = cls._op_ref(payload.get(_LedgerKeys.Field.TARGET))
        if target is not None:
            safe_payload[_LedgerKeys.Field.TARGET] = target
        # PRD-D3 — a row-set stage carries the ``rows`` count + the ``agent_holds``
        # (each rebuilt from its own ``{row_key, reason}`` allow-list; reasons are
        # rendered UI text, so length-capped + kept as plain strings).
        rows = payload.get(_LedgerKeys.Field.ROWS)
        if isinstance(rows, int) and not isinstance(rows, bool):
            safe_payload[_LedgerKeys.Field.ROWS] = rows
        holds = payload.get(_LedgerKeys.Field.AGENT_HOLDS)
        if isinstance(holds, (list, tuple)):
            safe_payload[_LedgerKeys.Field.AGENT_HOLDS] = [
                hold
                for hold in (cls._agent_hold(raw) for raw in holds)
                if hold is not None
            ]
        if _LedgerKeys.Field.ROLLOUT in payload:
            rollout = cls._rollout_mark(payload.get(_LedgerKeys.Field.ROLLOUT))
            # Preserve an invalid-present marker as an empty object. The stage
            # fold treats that as deny-not-legacy; silently omitting it would
            # create the very rollback bypass this projection protects.
            safe_payload[_LedgerKeys.Field.ROLLOUT] = rollout or {}
        return safe_payload

    @staticmethod
    def _rollout_mark(value: object) -> JsonObject | None:
        """Strictly rebuild the durable E2 governed-lane mark.

        The caller preserves a malformed-present mark as an empty sentinel, so
        replay denies it rather than treating it as legacy. This helper itself
        only accepts the closed capability enum, never an arbitrary string.
        """

        if not isinstance(value, dict):
            return None
        capabilities = value.get(_LedgerKeys.Field.CAPABILITIES)
        if not isinstance(capabilities, (list, tuple)) or not capabilities:
            return None
        from agent_runtime.rollout import RolloutCapability  # noqa: PLC0415

        try:
            parsed = tuple(RolloutCapability(item) for item in capabilities)
        except (TypeError, ValueError):
            return None
        if len(parsed) != len(set(parsed)):
            return None
        return {_LedgerKeys.Field.CAPABILITIES: [item.value for item in parsed]}

    @classmethod
    def _agent_hold(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, reason}`` agent-hold from its own allow-list."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        reason = cls._text(value.get(_LedgerKeys.Field.REASON))
        if row_key is None or reason is None:
            return None
        return {
            _LedgerKeys.Field.ROW_KEY: row_key,
            _LedgerKeys.Field.REASON: reason[:_ROWSET_TEXT_MAX],
        }

    @classmethod
    def _revision_added_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``revision.added`` through a strict allow-list (PRD-D1, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``rev`` / ``author`` / ``diff_ref`` plus the
        additive ``proposal_ref`` (this rev's snapshot) and ``authorship_spans``
        (the server-computed "edited by you" ranges). Each span is rebuilt from
        its own allow-list — only int ``start``/``end`` and a known ``author``
        survive, so nothing extra rides the ledger row.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.AUTHOR,
            _LedgerKeys.Field.DIFF_REF,
            _LedgerKeys.Field.PROPOSAL_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        rev = payload.get(_LedgerKeys.Field.REV)
        if isinstance(rev, int) and not isinstance(rev, bool):
            safe_payload[_LedgerKeys.Field.REV] = rev
        spans = payload.get(_LedgerKeys.Field.AUTHORSHIP_SPANS)
        if isinstance(spans, (list, tuple)):
            safe_payload[_LedgerKeys.Field.AUTHORSHIP_SPANS] = [
                span
                for span in (cls._authorship_span(raw) for raw in spans)
                if span is not None
            ]
        # PRD-D3 — the additive inline ``rowset`` (full row content). Each row is
        # rebuilt from its own allow-list so nothing extra rides the ledger row.
        rowset = cls._rowset(payload.get(_LedgerKeys.Field.ROWSET))
        if rowset is not None:
            safe_payload[_LedgerKeys.Field.ROWSET] = rowset
        return safe_payload

    @classmethod
    def _rowset(cls, value: object) -> JsonObject | None:
        """Rebuild ``{rows: [StagedRow…]}`` from a strict per-field allow-list."""

        if not isinstance(value, dict):
            return None
        raw_rows = value.get(_LedgerKeys.Field.ROWS)
        if not isinstance(raw_rows, (list, tuple)):
            return None
        rows = [row for row in (cls._staged_row(raw) for raw in raw_rows) if row]
        return {_LedgerKeys.Field.ROWS: rows}

    @classmethod
    def _staged_row(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, title, target_args, changes}`` staged row."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        title = cls._text(value.get(_LedgerKeys.Field.TITLE))
        if row_key is None or title is None:
            return None
        row: JsonObject = {
            _LedgerKeys.Field.ROW_KEY: row_key[:_ROWSET_TEXT_MAX],
            _LedgerKeys.Field.TITLE: title[:_ROWSET_TEXT_MAX],
        }
        target_args = value.get(_LedgerKeys.Field.TARGET_ARGS)
        if isinstance(target_args, dict):
            # Connector args are the server-held WYSIWYG unit — passed through as a
            # JSON object (keys coerced to str); the client never re-sends them.
            row[_LedgerKeys.Field.TARGET_ARGS] = {
                str(key): val for key, val in target_args.items()
            }
        changes = value.get(_LedgerKeys.Field.CHANGES)
        if isinstance(changes, (list, tuple)):
            row[_LedgerKeys.Field.CHANGES] = [
                change
                for change in (cls._row_change(raw) for raw in changes)
                if change is not None
            ]
        return row

    @classmethod
    def _row_change(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{field, old?, new?}`` field diff from its allow-list."""

        if not isinstance(value, dict):
            return None
        field_name = cls._text(value.get(_LedgerKeys.Field.FIELD))
        if field_name is None:
            return None
        change: JsonObject = {_LedgerKeys.Field.FIELD: field_name[:_ROWSET_TEXT_MAX]}
        if _LedgerKeys.Field.OLD in value:
            change[_LedgerKeys.Field.OLD] = value.get(_LedgerKeys.Field.OLD)
        if _LedgerKeys.Field.NEW in value:
            change[_LedgerKeys.Field.NEW] = value.get(_LedgerKeys.Field.NEW)
        return change

    @classmethod
    def _authorship_span(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{start, end, author}`` span from its own allow-list."""

        if not isinstance(value, dict):
            return None
        start = value.get(_LedgerKeys.Field.START)
        end = value.get(_LedgerKeys.Field.END)
        author = cls._text(value.get(_LedgerKeys.Field.AUTHOR))
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and author in ("agent", "user")
        ):
            return {
                _LedgerKeys.Field.START: start,
                _LedgerKeys.Field.END: end,
                _LedgerKeys.Field.AUTHOR: author,
            }
        return None

    @classmethod
    def _decision_recorded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``decision.recorded`` through a strict allow-list (PRD-D1).

        Keeps ``v`` / ``stage_id`` / ``decision`` / ``actor`` and the
        ``scope{rev}`` (single artifact — ``row_keys`` is D3). Nothing else
        survives; the ``scope`` object is rebuilt from its own ``rev`` key.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.DECISION,
            _LedgerKeys.Field.ACTOR,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        scope = payload.get(_LedgerKeys.Field.SCOPE)
        if isinstance(scope, dict):
            rev = scope.get(_LedgerKeys.Field.REV)
            row_keys = scope.get(_LedgerKeys.Field.ROW_KEYS)
            if isinstance(rev, int) and not isinstance(rev, bool):
                safe_payload[_LedgerKeys.Field.SCOPE] = {_LedgerKeys.Field.REV: rev}
            elif isinstance(row_keys, (list, tuple)):
                # PRD-D3 — a row-scoped decision. Only string row keys survive.
                safe_payload[_LedgerKeys.Field.SCOPE] = {
                    _LedgerKeys.Field.ROW_KEYS: [
                        key[:_ROWSET_TEXT_MAX]
                        for key in row_keys
                        if isinstance(key, str) and key
                    ]
                }
        # PRD-D3 — ``apply: true`` marks the apply-scoped approve (freezes the set).
        if payload.get(_LedgerKeys.Field.APPLY) is True:
            safe_payload[_LedgerKeys.Field.APPLY] = True
        return safe_payload

    @classmethod
    def _write_applied_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``write.applied`` through a strict allow-list (PRD-D2, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``rev`` / ``result`` plus the additive
        ``connector_receipt_ref`` (an opaque ``commit://`` ref), ``failure``
        (rebuilt from its own ``{code, detail}`` allow-list — ``failed`` only)
        and ``decided_by`` (``{actor, decision_seq}`` — the receipt row). The
        single-artifact shape only (``row_keys`` / ``partial`` are D3; they never
        emit here). Nothing else survives — the connector result is NEVER echoed
        raw into the event; only the ref rides.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.RESULT,
            _LedgerKeys.Field.CONNECTOR_RECEIPT_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        rev = payload.get(_LedgerKeys.Field.REV)
        if isinstance(rev, int) and not isinstance(rev, bool):
            safe_payload[_LedgerKeys.Field.REV] = rev
        failure = cls._write_applied_failure(payload.get(_LedgerKeys.Field.FAILURE))
        if failure is not None:
            safe_payload[_LedgerKeys.Field.FAILURE] = failure
        decided_by = cls._write_applied_decided_by(
            payload.get(_LedgerKeys.Field.DECIDED_BY)
        )
        if decided_by is not None:
            safe_payload[_LedgerKeys.Field.DECIDED_BY] = decided_by
        # PRD-D3 — the applied row set + per-row outcomes (partial-apply). Each
        # ``row_results`` entry is rebuilt from its own ``{row_key, outcome, detail?}``
        # allow-list; nothing else survives.
        row_keys = payload.get(_LedgerKeys.Field.ROW_KEYS)
        if isinstance(row_keys, (list, tuple)):
            safe_payload[_LedgerKeys.Field.ROW_KEYS] = [
                key[:_ROWSET_TEXT_MAX]
                for key in row_keys
                if isinstance(key, str) and key
            ]
        row_results = payload.get(_LedgerKeys.Field.ROW_RESULTS)
        if isinstance(row_results, (list, tuple)):
            safe_payload[_LedgerKeys.Field.ROW_RESULTS] = [
                entry
                for entry in (cls._row_result(raw) for raw in row_results)
                if entry is not None
            ]
        return safe_payload

    @classmethod
    def _row_result(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, outcome, detail?}`` row result from its allow-list."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        outcome = cls._text(value.get(_LedgerKeys.Field.OUTCOME))
        if row_key is None or outcome not in (
            _LedgerValues.ROW_OUTCOME_APPLIED,
            _LedgerValues.ROW_OUTCOME_FAILED,
        ):
            return None
        result: JsonObject = {
            _LedgerKeys.Field.ROW_KEY: row_key[:_ROWSET_TEXT_MAX],
            _LedgerKeys.Field.OUTCOME: outcome,
        }
        detail = cls._text(value.get(_LedgerKeys.Field.DETAIL))
        if detail is not None:
            result[_LedgerKeys.Field.DETAIL] = detail[:_ROWSET_TEXT_MAX]
        return result

    @classmethod
    def _write_applied_failure(cls, value: object) -> JsonObject | None:
        """Rebuild ``{code, detail?}`` from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        code = cls._text(value.get(_LedgerKeys.Field.CODE))
        if code is None:
            return None
        failure: JsonObject = {_LedgerKeys.Field.CODE: code}
        detail = cls._text(value.get(_LedgerKeys.Field.DETAIL))
        if detail is not None:
            failure[_LedgerKeys.Field.DETAIL] = detail
        return failure

    @classmethod
    def _write_applied_decided_by(cls, value: object) -> JsonObject | None:
        """Rebuild ``{actor, decision_seq}`` from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        actor = cls._text(value.get(_LedgerKeys.Field.ACTOR))
        decision_seq = value.get(_LedgerKeys.Field.DECISION_SEQ)
        if actor is None or not (
            isinstance(decision_seq, int) and not isinstance(decision_seq, bool)
        ):
            return None
        return {
            _LedgerKeys.Field.ACTOR: actor,
            _LedgerKeys.Field.DECISION_SEQ: decision_seq,
        }

    @classmethod
    def _receipt_emitted_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``receipt.emitted`` through a strict allow-list (PRD-E1, SDR §5).

        Keeps only ``v`` / ``surface_id`` / ``fold_ref`` — nothing else rides.
        ``fold_ref`` contains ``"ref"`` so ``_redaction_state_for`` marks the row
        ``OFFLOADED`` (it IS a reference — the receipt is re-derivable by folding
        the run's events, never a stored blob).
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.FOLD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _artifact_ledger_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Validate and canonicalize a reference-only artifact ledger payload."""

        try:
            validated = WorkLedgerVocabulary.validate_payload(
                event_type.value,
                payload,
            )
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning(
                "Rejected malformed artifact ledger payload event_type=%s",
                event_type.value,
            )
            return {}
        return validated.model_dump(mode="json", by_alias=True)

    @classmethod
    def _effect_ledger_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Validate and canonicalize a reference-only universal-effect row.

        ``WorkLedgerVocabulary`` is a strict Pydantic allow-list: unknown
        fields, raw proposal bytes, physical paths, and malformed opaque refs
        are rejected before the event can enter replay or SSE. ``exclude_none``
        preserves additive v:1 compatibility rather than manufacturing fields
        absent from a historical row.
        """

        try:
            validated = WorkLedgerVocabulary.validate_payload(
                event_type.value,
                payload,
            )
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning(
                "Rejected malformed effect ledger payload event_type=%s",
                event_type.value,
            )
            return {}
        return validated.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def _quality_control_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate the complete, flat, reference-only snapshot journal row."""

        try:
            validated = QualityControlBoundPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed quality.control_bound.v1 payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _quality_decision_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate the complete, flat, reference-only decision journal row."""

        try:
            validated = QualityDecisionPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed quality.decision.v1 payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _tool_policy_journal_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate one strict discriminated F4 record and reject extra data."""

        try:
            validated = TaskPolicyJournalPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed tool_policy.journal.v1 payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _prompt_assembled_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate one body-free assembly record and reject extra data."""

        try:
            validated = PromptAssembledPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed prompt.assembled.v1 payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _prompt_cache_observed_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate one provider-authoritative cache record."""

        try:
            validated = PromptCacheObservedPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed prompt.cache.observed.v1 payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _model_invocation_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate one body/secret-free F10.3 invocation record."""

        try:
            validated = ModelInvocationJournalPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed model invocation journal payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _context_occupancy_payload(cls, payload: JsonObject) -> JsonObject:
        """Validate one measured occupancy snapshot and reject anything else.

        Validate-and-re-dump rather than a hand-written key allow-list, because
        the snapshot contract is already strict (``extra="forbid"``, bounded
        strings, closed enums) and §6.5's no-content rule is enforced by those
        bounds. A second, hand-maintained allow-list here would be a copy of the
        contract that drifts from it, and drift on this particular surface means
        either dropping a real field or letting an unbounded one through.

        A malformed payload projects to ``{}`` and is logged, matching every
        sibling journal projector: an occupancy event is observability, and
        rejecting the payload must not cost the run the event's ordering slot.
        """

        try:
            validated = ContextOccupancyPayload.model_validate(payload)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "Rejected malformed context_occupancy payload"
            )
            return {}
        return validated.model_dump(mode="json")

    @classmethod
    def _copy_payload_version(
        cls, payload: JsonObject, safe_payload: JsonObject
    ) -> None:
        """Copy the ``v`` payload-version integer through when it is a real int."""

        version = payload.get(_LedgerKeys.Field.V)
        if isinstance(version, int) and not isinstance(version, bool):
            safe_payload[_LedgerKeys.Field.V] = version

    @classmethod
    def _op_ref(cls, value: object) -> JsonObject | None:
        """Rebuild a ``{connector, op}`` ref from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        connector = cls._text(value.get(_LedgerKeys.Field.CONNECTOR))
        op = cls._text(value.get(_LedgerKeys.Field.OP))
        if connector is None or op is None:
            return None
        return {_LedgerKeys.Field.CONNECTOR: connector, _LedgerKeys.Field.OP: op}

    @classmethod
    def _approval_requested_payload(cls, payload: JsonObject) -> JsonObject:
        approval_kind = cls._text(payload.get(Keys.Field.APPROVAL_KIND))
        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            return cls._ask_a_question_requested_payload(payload)
        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            # P1-A re-scoped — SUGGEST_EDIT child rows surface the parent
            # link + the editing user; both are short opaque ids.
            Keys.Field.CHAIN_PARENT_APPROVAL_ID,
            "edited_by_user_id",
            Keys.Field.BATCH_ID,
            Keys.Field.SERVER_ID,
            Keys.Field.SERVER_NAME,
            "display_name",
            Keys.Field.TOOL_NAME,
            "risk_level",
            # Filesystem approvals: the folder being asked about and whether it
            # is a read or a write. Both are already implied by `message`, but
            # a card that has to parse prose to find its own subject cannot
            # style, truncate or localise it. Absent from this allow-list they
            # arrived as None and the card fell back to the sentence — the same
            # silent-strip that made `workspace_grant` undeliverable, where a
            # correct producer and a correct parser had the field deleted
            # between them.
            "path",
            "operation",
            Keys.Payload.MESSAGE,
            Keys.Field.REASON,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        # PR #43 — batch_index is a typed int, not a string; preserve it
        # through the projection so the FE receives the typed shape.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
        read_only = payload.get("read_only")
        if isinstance(read_only, bool):
            safe_payload["read_only"] = read_only
        arguments = payload.get("arguments")
        if isinstance(arguments, dict):
            safe_payload["arguments"] = arguments
        # P1-A re-scoped — SUGGEST_EDIT carries the approver's proposed
        # tool-call arguments. Same shape as ``arguments``: arbitrary
        # JSON object the FE renders as a diff vs the original.
        edited_payload = payload.get("edited_payload")
        if isinstance(edited_payload, dict):
            safe_payload["edited_payload"] = edited_payload
        grant_options = payload.get("grant_options")
        if isinstance(grant_options, list | tuple):
            safe_payload["grant_options"] = [
                option for option in grant_options if isinstance(option, str)
            ]
        # Consent-card shape (rows / preview / params). Re-validated here rather
        # than passed through as a dict: this block reaches the client and drives
        # what the user reads before consenting, so it must satisfy the same
        # contract on the way out as it did on the way in. A malformed block is
        # dropped and the card falls back to its params frame — never rendered
        # half-built.
        # A host-folder ask. The client keys its Grant card on this block's
        # PRESENCE (``WORKSPACE_GRANT_PAYLOAD_KEY`` in packages/chat-surface),
        # and its parser requires ``path`` — so without this projection the
        # block is stripped, no card renders, and the run parks with a generic
        # approval the user cannot answer with a folder.
        workspace_grant = cls._workspace_grant_payload(
            payload.get(_Fields.WORKSPACE_GRANT)
        )
        if workspace_grant is not None:
            safe_payload[_Fields.WORKSPACE_GRANT] = workspace_grant
        # The folder ``grant_options: [..., "allow_always"]` refers to. Projected
        # under the same key-by-key re-validation as the block above, and for the
        # same reason: it is the SUBJECT of a durable decision, so a client that
        # cannot read it here cannot name what it is about to attach — and
        # "allow_always" with nothing to scope it is exactly the silent widening
        # this card must never allow. Advertising the option without shipping its
        # scope would be that bug, and this allow-list is where it would happen.
        grant_scope = cls._workspace_grant_payload(payload.get(_Fields.GRANT_SCOPE))
        if grant_scope is not None:
            safe_payload[_Fields.GRANT_SCOPE] = grant_scope
        presentation = payload.get(_Fields.PRESENTATION)
        if isinstance(presentation, dict):
            # Lazy import for the same circularity reason documented at the top
            # of this module: ``schemas.approvals`` pulls in the surfaces domain,
            # which loads back through this package during init.
            from runtime_api.schemas.approvals import ApprovalPresentation

            try:
                safe_payload[_Fields.PRESENTATION] = (
                    ApprovalPresentation.model_validate(presentation).model_dump(
                        mode="json"
                    )
                )
            except ValidationError:
                pass
        return safe_payload

    @classmethod
    def _workspace_grant_payload(cls, value: object) -> JsonObject | None:
        """Project the ``workspace_grant`` block, or ``None`` to drop it.

        Re-validated key by key rather than passed through as a dict, for the
        reason the ``presentation`` block is: this reaches the client and drives
        what the user reads before granting a folder. ``path`` is required and
        bounded — it is the one host-absolute string on this card, and it is the
        SUBJECT of the ask, never a value any read is served from. A block
        without it is dropped, so a half-built card is never rendered.
        """

        if not isinstance(value, dict):
            return None
        path = cls._text(value.get(_Fields.PATH))
        if path is None:
            return None
        block: JsonObject = {_Fields.PATH: path[:_WORKSPACE_GRANT_PATH_MAX]}
        for key in (
            _Fields.FOLDER_NAME,
            _Fields.PLATFORM,
            _Fields.MODE,
            Keys.Field.REASON,
        ):
            text_value = cls._text(value.get(key))
            if text_value is not None:
                block[key] = text_value[:_WORKSPACE_GRANT_TEXT_MAX]
        return block

    @classmethod
    def _approval_forwarded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``approval_forwarded`` payloads through a strict allow-list.

        PR 1.4 — emitted in the same transaction as ``APPROVAL_RESOLVED``
        (status=forwarded) for the parent and ``APPROVAL_REQUESTED`` for the
        child. The reducer keys on ``chain_parent_approval_id`` to transform
        the original in-thread card into a "Waiting on @marcus" pill.
        """

        safe_payload: JsonObject = {}
        for text_key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.CHAIN_PARENT_APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Field.FORWARDED_BY_USER_ID,
            Keys.Field.FORWARDED_TO_USER_ID,
            Keys.Field.FORWARDED_AT,
            Keys.Field.ACTION_SUMMARY,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _ask_a_question_requested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ask-a-question payloads, preserving question text and structured options.

        The standard approval allow-list strips these fields, so this approval kind
        gets its own projection path.
        """

        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Field.BATCH_ID,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
            "header",
            "question",
            "hint",
            # The RISK AXIS. A parked write borrows this wire shape to reuse the
            # resume plumbing, so dropping these two here is what made the
            # client's whole irreversible lane unreachable: the card withholds
            # one-click Approve for a destructive op, and no payload it could
            # ever receive said "destructive". `op_class` is the PDP's own
            # verdict (`McpToolActionClass`: read | write | destructive) and
            # `risk_level` distinguishes a write that reaches the user's real
            # files ("high") from one a connector can undo ("medium").
            #
            # Both are already public on the sibling `approval_requested` path,
            # so this widens no contract — it stops one lane from being the
            # exception. Neither is free text: both are producer-side enums, and
            # `_text` keeps a hostile payload from smuggling anything larger.
            "op_class",
            "risk_level",
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        # PR #43 — typed batch_index preserved through projection.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
        options = payload.get("options")
        if isinstance(options, list | tuple):
            safe_payload["options"] = cls._safe_question_options(options)
        for flag_key in ("multi_select", "allow_free_text"):
            flag = payload.get(flag_key)
            if isinstance(flag, bool):
                safe_payload[flag_key] = flag
        display_title = cls._gate_display_title(payload)
        if display_title is not None:
            safe_payload[_LedgerKeys.Field.DISPLAY_TITLE] = display_title
            safe_payload[_LedgerKeys.Field.CONNECTOR] = cls._text(
                payload.get(Keys.Field.SERVER_NAME)
            )
        return safe_payload

    @classmethod
    def _gate_display_title(cls, payload: JsonObject) -> str | None:
        """A parked WRITE's human line, lifted out of the additive gate block.

        A write gate rides the ``ask_a_question`` wire shape, so it lands in this
        projection — where every key above describes a QUESTION and none of them
        describes an effect. The card fell through its whole title chain to the
        generic "Approve this action" over a call that was about to file a real
        Linear issue.

        Only the one line is lifted, not the block: the rest of the gate (scopes,
        op class, auth state) is the ledger's business and has no card to render
        it. ``GatePurposeBuilder`` already caps the line's length and strips
        newlines, markdown and URLs, so the sanitised primary argument it carries
        is safe to show — that is the whole reason the interactive purpose exists
        separately from the argument-free ledger one.
        """

        gate = payload.get("gate")
        if not isinstance(gate, dict):
            return None
        return cls._text(gate.get(_LedgerKeys.Field.PURPOSE))

    @classmethod
    def _safe_question_options(cls, options: list | tuple) -> list[JsonObject]:
        """Coerce option dicts and bare strings into a sanitised list.

        Bare strings are upgraded to ``{label: ...}`` for backwards compatibility
        with callers that haven't adopted the structured shape yet.
        """

        sanitized: list[JsonObject] = []
        for option in options:
            if isinstance(option, str):
                label = cls._text(option)
                if label is not None:
                    sanitized.append({"label": label})
                continue
            if not isinstance(option, dict):
                continue
            label = cls._text(option.get("label"))
            if label is None:
                continue
            entry: JsonObject = {"label": label}
            description = cls._text(option.get("description"))
            if description is not None:
                entry["description"] = description
            recommended = option.get("recommended")
            if isinstance(recommended, bool):
                entry["recommended"] = recommended
            sanitized.append(entry)
        return sanitized

    @classmethod
    def _visibility_for(
        cls,
        *,
        source: StreamEventSource,
        payload: JsonObject,
    ) -> RuntimeEventVisibility:
        configured = cls._text(payload.get(Keys.Field.VISIBILITY))
        if configured is not None:
            try:
                return RuntimeEventVisibility(configured)
            except ValueError:
                return RuntimeEventVisibility.USER
        if source is StreamEventSource.SUMMARIZATION:
            return RuntimeEventVisibility.INTERNAL
        return RuntimeEventVisibility.USER

    @classmethod
    def _redaction_state_for(
        cls,
        *,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeEventRedactionState:
        configured = cls._text(payload.get(Keys.Field.REDACTION_STATE)) or cls._text(
            metadata.get(Keys.Field.REDACTION_STATE)
        )
        if configured is not None:
            try:
                return RuntimeEventRedactionState(configured)
            except ValueError:
                return RuntimeEventRedactionState.REDACTED
        if cls._contains_payload_ref(payload):
            return RuntimeEventRedactionState.OFFLOADED
        if "[truncated]" in str(payload):
            return RuntimeEventRedactionState.TRUNCATED
        return RuntimeEventRedactionState.REDACTED

    @classmethod
    def _contains_payload_ref(cls, payload: JsonObject) -> bool:
        return any("ref" in key.lower() for key in payload)

    @classmethod
    def _status_text(cls, payload: JsonObject) -> str | None:
        value = cls._text(payload.get(Keys.Field.STATUS))
        if value is None:
            return None
        return value.lower()

    @classmethod
    def _vocabulary_text(cls, value: object, allowed: frozenset[str]) -> str | None:
        """Text, but only when the contract's own vocabulary declares the value.

        ``_text`` forwards any non-empty string, which is right for a title and
        wrong for an enum-valued field: it is one guarantee short of "this word
        means what the contract says it means".
        """

        text = cls._text(value)
        return text if text is not None and text in allowed else None

    @classmethod
    def _text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


class AssistantUsageMetrics(RuntimeContract):
    """Exact provider token usage counts without secret-like field names."""

    input: NonNegativeInt | None = None
    output: NonNegativeInt | None = None
    total: NonNegativeInt | None = None
    cached_input: NonNegativeInt | None = None
    output_per_second: float | None = Field(default=None, ge=0)


class AssistantSubagentUsageRollup(RuntimeContract):
    """Aggregate token usage for one subagent task (B2).

    Sum of every ``MODEL_CALL_COMPLETED`` row attributed to a single
    ``task_id`` between SUBAGENT_STARTED and SUBAGENT_COMPLETED. ``call_count``
    is the number of distinct LLM calls. Optional payload on
    ``SUBAGENT_COMPLETED`` events; absent when the worker can't correlate
    calls to the task (e.g. provider didn't return a stable message id).
    """

    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    call_count: NonNegativeInt = 0


class AssistantPerformanceMetrics(RuntimeContract):
    """Assistant response timing and exact provider usage metadata."""

    started_at: datetime
    completed_at: datetime
    duration_ms: NonNegativeInt
    chunk_count: NonNegativeInt = 0
    first_chunk_at: datetime | None = None
    first_chunk_ms: NonNegativeInt | None = None
    usage: AssistantUsageMetrics | None = None


class RuntimeEventPresentationPreviewRow(RuntimeContract):
    """Small user-facing row rendered in an activity card result preview."""

    title: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=240)
    url: str | None = Field(default=None, max_length=500)
    badge: str | None = Field(default=None, max_length=40)

    @field_validator(
        _Fields.TITLE, _Fields.SUBTITLE, _Fields.URL, _Fields.BADGE, mode="before"
    )
    @classmethod
    def _plain_text(cls, value: object, info: ValidationInfo) -> str | None:
        max_lengths = {
            _Fields.TITLE: 120,
            _Fields.SUBTITLE: 240,
            _Fields.URL: 500,
            _Fields.BADGE: 40,
        }
        return RuntimeEventPresentation.safe_text(
            value,
            max_length=max_lengths[info.field_name],
        )


class RuntimeEventPresentation(RuntimeContract):
    """Validated LLM-generated card presentation metadata."""

    title: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=240)
    status_label: Literal[
        "Running", "Waiting for permission", "Done", "Failed", "Not available"
    ]
    kind: Literal["progress", "result", "approval", "auth", "error"]
    group_key: str | None = Field(default=None, max_length=160)
    primary_entity: str | None = Field(default=None, max_length=80)
    action_label: str | None = Field(default=None, max_length=60)
    result_preview: tuple[RuntimeEventPresentationPreviewRow, ...] = ()
    debug_label: str | None = Field(default="Tool details", max_length=40)
    #: The typed failure code behind this card, so a client can key a remedy to
    #: the actual cause instead of guessing from prose.
    code: str | None = Field(default=None, max_length=80)
    #: Whether repeating the operation could change the outcome. A client draws
    #: a remedy ONLY when this is true — an action the system cannot honour is
    #: worse than none. ``None`` on non-failure cards.
    retryable: bool | None = None

    @field_validator(
        _Fields.TITLE,
        _Fields.SUMMARY,
        _Fields.GROUP_KEY,
        _Fields.PRIMARY_ENTITY,
        _Fields.ACTION_LABEL,
        _Fields.DEBUG_LABEL,
        mode="before",
    )
    @classmethod
    def _safe_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        max_lengths = {
            _Fields.TITLE: 80,
            _Fields.SUMMARY: 240,
            _Fields.GROUP_KEY: 160,
            _Fields.PRIMARY_ENTITY: 80,
            _Fields.ACTION_LABEL: 60,
            _Fields.DEBUG_LABEL: 40,
        }
        return cls.safe_text(value, max_length=max_lengths[info.field_name])

    @staticmethod
    def safe_text(value: object, *, max_length: int) -> str | None:
        """Strip HTML angle brackets, collapse whitespace, and truncate to ``max_length``."""
        if not isinstance(value, str):
            return None
        text = " ".join(value.replace("<", "").replace(">", "").split())
        if not text:
            return None
        return text[:max_length]


class _RuntimeEventBase(RuntimeContract):
    """Shared fields and validators for event envelopes and drafts."""

    run_id: str
    conversation_id: str
    source: StreamEventSource
    event_type: RuntimeApiEventType
    trace_id: str
    parent_event_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    parent_task_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    display_title: str | None = None
    summary: str | None = None
    status: str | None = None
    activity_kind: RuntimeActivityKind | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
    presentation: RuntimeEventPresentation | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator(
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.PARENT_EVENT_ID,
        Keys.Field.SPAN_ID,
        Keys.Field.PARENT_SPAN_ID,
        Keys.Field.PARENT_TASK_ID,
        Keys.Field.TASK_ID,
        Keys.Field.SUBAGENT_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(
        cls, value: object, info: ValidationInfo
    ) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, info.field_name)

    @field_validator(Keys.Field.PAYLOAD, Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)

    @classmethod
    def _build_from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        stream_event: StreamEvent,
    ) -> dict[str, object]:
        """Return the common constructor kwargs from a normalized stream event."""

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(
            stream_event
        )
        payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=stream_event.payload,
        )
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=stream_event.source,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
        )
        return dict(
            run_id=run_id,
            conversation_id=conversation_id,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            presentation=RuntimeEventPresentationProjector.presentation_metadata(
                stream_event.metadata
            ),
            **presentation,
        )


class RuntimeEventEnvelope(_RuntimeEventBase):
    """Ordered transport event envelope shared by replay and streaming."""

    event_protocol_version: PositiveInt = Values.EVENT_PROTOCOL_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    sequence_no: PositiveInt
    activity_kind: RuntimeActivityKind
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(Keys.Field.EVENT_ID, mode="before")
    @classmethod
    def _normalize_event_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        sequence_no: int,
        stream_event: StreamEvent,
    ) -> "RuntimeEventEnvelope":
        """Wrap an existing normalized runtime event in the API envelope."""

        kwargs = cls._build_from_stream_event(
            run_id=run_id,
            conversation_id=conversation_id,
            stream_event=stream_event,
        )
        return cls(
            event_id=stream_event.event_id,
            sequence_no=sequence_no,
            created_at=stream_event.timestamp,
            **kwargs,
        )


class RuntimeEventReplayResponse(RuntimeContract):
    """Replay response for persisted ordered events."""

    run_id: str
    events: tuple[RuntimeEventEnvelope, ...]
    latest_sequence_no: NonNegativeInt
    run_status: AgentRunStatus
    has_more: bool = False


class RuntimeEventDraft(_RuntimeEventBase):
    """Event data before the event store assigns per-run sequence number.

    Carries ``org_id`` so the persistence adapter can scope its tenant
    connection BEFORE the canonical ``agent_runs`` row is read. The field
    lives on the draft only — :class:`RuntimeEventEnvelope` (the wire shape
    SSE/replay returns to clients) deliberately omits ``org_id`` so tenant
    identifiers are not exposed in user-visible payloads.
    """

    org_id: str
    # Optional producer-assigned identity for durable outbox publication.
    # Ordinary model/tool stream events leave this unset and retain the
    # adapter-generated UUID behavior.  A retry with the same stable id and
    # identical body returns the original envelope instead of appending twice.
    event_id: str | None = None
    # When an outbox command represents an earlier domain mutation, preserve
    # that mutation time on the ledger.  Unset keeps the historical append-time
    # behavior for every existing producer.
    created_at: datetime | None = None

    @field_validator(Keys.Field.ORG_ID, mode="before")
    @classmethod
    def _normalize_org_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.EVENT_ID, mode="before")
    @classmethod
    def _normalize_optional_event_id(
        cls, value: object, info: ValidationInfo
    ) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    def matches_envelope(self, envelope: RuntimeEventEnvelope) -> bool:
        """Return whether ``envelope`` is the idempotent result of this draft."""

        expected_activity = (
            self.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=self.event_type,
                source=self.source,
            )
        )
        return (
            self.event_id is not None
            and envelope.event_id == self.event_id
            and envelope.run_id == self.run_id
            and envelope.conversation_id == self.conversation_id
            and envelope.source == self.source
            and envelope.event_type == self.event_type
            and envelope.trace_id == self.trace_id
            and envelope.parent_event_id == self.parent_event_id
            and envelope.span_id == self.span_id
            and envelope.parent_span_id == self.parent_span_id
            and envelope.parent_task_id == self.parent_task_id
            and envelope.task_id == self.task_id
            and envelope.subagent_id == self.subagent_id
            and envelope.display_title == self.display_title
            and envelope.summary == self.summary
            and envelope.status == self.status
            and envelope.activity_kind == expected_activity
            and envelope.visibility == self.visibility
            and envelope.redaction_state == self.redaction_state
            and envelope.presentation == self.presentation
            and envelope.payload == self.payload
            and envelope.metadata == self.metadata
            and (self.created_at is None or envelope.created_at == self.created_at)
        )

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        org_id: str,
        stream_event: StreamEvent,
    ) -> "RuntimeEventDraft":
        """Create an appendable API event draft from a normalized runtime event."""

        kwargs = cls._build_from_stream_event(
            run_id=run_id,
            conversation_id=conversation_id,
            stream_event=stream_event,
        )
        return cls(org_id=org_id, **kwargs)
