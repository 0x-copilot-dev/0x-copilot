"""Payload-key, value, and message constants for v2 ledger emission (PRD-A3 D7).

Per ``services/ai-backend/CLAUDE.md`` no repeated key/value/message string is
inlined. Event-type *values* are NOT redefined here — they come from the A1
vocabulary (``LedgerEventType`` in ``ledger_models``); this module owns only the
payload-field keys, the A3-frozen constant values, and the emit-time summaries.
"""

from __future__ import annotations


class Keys:
    """Field-name constants for the four A3 ledger payloads (SDR §5, verbatim)."""

    class Field:
        """Payload keys. Values are the wire keys — do not rename (frozen)."""

        V = "v"
        CALL_ID = "call_id"
        CONNECTOR = "connector"
        OP = "op"
        CLASS = "class"
        BASIS = "basis"
        LATENCY_MS = "latency_ms"
        PAYLOAD_REF = "payload_ref"
        SURFACE_ID = "surface_id"
        KIND = "kind"
        SOURCE = "source"
        TITLE = "title"
        TIER = "tier"
        SPEC_REF = "spec_ref"
        GEN = "gen"
        MODEL = "model"
        # ``gen.ms`` — generation duration, populated by the B3 ViewDeriver
        # (A3 emitted ``gen.model`` only).
        MS = "ms"
        # ``view.preference`` keys (PRD-B3).
        KEEP = "keep"
        ACTOR = "actor"
        # PRD-C2 gate.opened / gate.resolved payload keys (SDR §5, verbatim).
        GATE_ID = "gate_id"
        PURPOSE = "purpose"
        SCOPES = "scopes"
        AUTH_STATE = "auth_state"
        OUTCOME = "outcome"
        WRITE_POLICY = "write_policy"
        # PRD-D1 staged-write payload keys (write.staged / revision.added /
        # decision.recorded — SDR §5, verbatim; ``proposal_ref`` /
        # ``authorship_spans`` are the additive keys noted under §5).
        STAGE_ID = "stage_id"
        TARGET = "target"
        PROPOSAL_REF = "proposal_ref"
        REV = "rev"
        AUTHOR = "author"
        DIFF_REF = "diff_ref"
        AUTHORSHIP_SPANS = "authorship_spans"
        DECISION = "decision"
        SCOPE = "scope"
        START = "start"
        END = "end"
        # PRD-D3 bulk row-set payload keys (write.staged rows/agent_holds;
        # revision.added additive ``rowset``; decision.recorded ``scope.row_keys``
        # + ``apply``; write.applied ``row_keys`` + additive ``row_results``).
        # SDR §5 (verbatim + the additive keys noted under §5).
        ROWS = "rows"
        AGENT_HOLDS = "agent_holds"
        ROW_KEY = "row_key"
        ROW_KEYS = "row_keys"
        REASON = "reason"
        ROWSET = "rowset"
        TARGET_ARGS = "target_args"
        CHANGES = "changes"
        FIELD = "field"
        OLD = "old"
        NEW = "new"
        APPLY = "apply"
        ROW_RESULTS = "row_results"
        # ``row_results[].outcome`` reuses the existing ``OUTCOME`` key above.
        # PRD-D2 write.applied payload keys (SDR §5, verbatim + additive
        # ``failure`` / ``decided_by`` noted under §5).
        RESULT = "result"
        CONNECTOR_RECEIPT_REF = "connector_receipt_ref"
        FAILURE = "failure"
        CODE = "code"
        DETAIL = "detail"
        DECIDED_BY = "decided_by"
        DECISION_SEQ = "decision_seq"
        # PRD-E1 receipt.emitted payload key (SDR §5, verbatim). ``surface_id`` /
        # ``v`` reuse the existing keys above; ``fold_ref`` is the receipt's
        # re-derivation pointer.
        FOLD_REF = "fold_ref"


class Values:
    """Constant payload values A3 freezes (no classifier / no measurement yet)."""

    # ``action.classified`` in A3 always records the honest "we made no policy
    # decision" pair — a real classifier arrives in PRD-C1.
    CLASS_UNKNOWN = "unknown"
    BASIS_DEFAULT = "default"

    # ``view.derived`` tiers/bases mapped from the v1 envelope (D1).
    TIER_GENERIC = "generic"
    TIER_SHAPED = "shaped"
    BASIS_SCHEMA = "schema"
    BASIS_REGISTRY = "registry"
    BASIS_GENERATED = "generated"

    # ``payload_ref`` scheme (D1/D7): ``call:<call_id>`` resolves to the
    # ``tool_result`` event carrying the same ``call_id`` in this run's replay.
    CALL_REF_PREFIX = "call:"

    # ``spec_ref`` scheme (B3): ``spec:<server-slug>/<tool-slug>`` — the same
    # ``<scheme>:<...>`` ref convention A3 uses for ``payload_ref``, keyed on the
    # normalised connector/tool a registry or generated spec binds to.
    SPEC_REF_PREFIX = "spec:"

    # ``view.preference`` constant values (PRD-B3, SDR §5).
    KEEP_GENERIC = "generic"
    KEEP_SHAPED = "shaped"
    ACTOR_USER = "user"

    # Every A3 payload carries ``v: 1`` (SDR §5 — versioned from day one).
    PAYLOAD_V = 1

    # Longest a folded/derived surface title may be (D1).
    TITLE_MAX_LEN = 120

    # PRD-D1 staged-write constant values (SDR §5 enum members, verbatim).
    KIND_MESSAGE = "message"
    AUTHOR_AGENT = "agent"
    AUTHOR_USER = "user"
    DECISION_APPROVE = "approve"
    DECISION_REJECT = "reject"
    DECISION_HOLD = "hold"
    DECISION_RESTORE = "restore"

    # PRD-D2 write.applied constant values (SDR §5 ApplyResult members + the
    # additive failure-code enum defined in PRD-D2; ``partial`` is D3).
    RESULT_APPLIED = "applied"
    RESULT_FAILED = "failed"
    # PRD-D3 partial-apply outcome (ApplyResult.PARTIAL member) + the per-row
    # ``row_results[].outcome`` value set + the ``actor: policy`` allow-always
    # auto-apply + the ``kind: table`` a row-set surface is created with.
    RESULT_PARTIAL = "partial"
    ROW_OUTCOME_APPLIED = "applied"
    ROW_OUTCOME_FAILED = "failed"
    ACTOR_POLICY = "policy"
    KIND_TABLE = "table"
    FAILURE_PRECONDITION_DRIFT = "precondition_drift"
    FAILURE_CONNECTOR_ERROR = "connector_error"
    FAILURE_ATTEMPT_INDETERMINATE = "attempt_indeterminate"
    # ``decided_by.actor`` is pinned to the constant ``"user"`` here (SDR §5).
    DECIDED_BY_ACTOR_USER = "user"
    # ``connector_receipt_ref`` scheme (PRD-D2, NEW): resolves to the persisted
    # raw ``ConnectorCommitResult`` for this commit attempt.
    COMMIT_REF_PREFIX = "commit://"

    # PRD-E1 receipt constant values (SDR §5, NEW — E1 owns them). One receipt
    # surface per run at the stable ``receipt://<run_id>`` id; the receipt has no
    # SaaS connector, so its ``source`` is the constant ``runtime`` / ``receipt``
    # pair. ``fold_ref = ledger://<run_id>@<through_seq>`` re-derives the receipt
    # by folding events with ``sequence_no <= through_seq`` (NFR-5/6).
    RECEIPT_SURFACE_PREFIX = "receipt://"
    RECEIPT_CONNECTOR = "runtime"
    RECEIPT_OP = "receipt"
    RECEIPT_KIND = "receipt"
    RECEIPT_TITLE = "Run receipt"
    FOLD_REF_PREFIX = "ledger://"
    FOLD_REF_SEPARATOR = "@"


class Messages:
    """Emit-time summaries for the ledger events (D3). ``action.classified``
    has no summary (``None``) — it is not a user-facing beat in A3."""

    READ_EXECUTED = "auto-ran (read)"
    SURFACE_CREATED = "Prepared a surface"
    VIEW_DERIVED = "Derived a view"
    VIEW_PREFERENCE = "Set a view preference"

    # PRD-D1 staged-write emit-time summaries.
    WRITE_STAGED = "Staged a write"
    REVISION_ADDED = "Revised the draft"
    DECISION_RECORDED = "Recorded a decision"

    # PRD-D3 bulk row-set emit-time summaries.
    ROWSET_STAGED = "Staged a bulk change"
    ROW_DECISION_RECORDED = "Recorded a row decision"
    ROWSET_APPLIED = "Applied the approved rows"

    # PRD-E1 receipt emit-time summaries (the two events the ReceiptEmitter
    # appends at run termination) + the best-effort log tag.
    RECEIPT_SURFACE_CREATED = "Prepared the run receipt"
    RECEIPT_EMITTED = "Sealed the run receipt"
    # Log tag when the ReceiptEmitter swallows its own exception (E1 §6). A fold
    # or append failure must never block termination.
    RECEIPT_EMIT_RAISED = "[surfaces_v2] receipt.emit_raised"

    # PRD-D2 write.applied emit-time summaries + FR-C3 display microcopy.
    WRITE_APPLIED = "Applied the write"
    # FR-C3 requirement microcopy — the applied confirmation the canvas shows
    # (verbatim; Phase-2 may polish wording).
    APPLIED_TITLE = "Sent — exactly the revision you approved."
    FAILED_TITLE = "Apply refused — nothing was sent."

    # Log tag when an emitter method swallows its own exception (D3/D8).
    EMIT_RAISED = "[surfaces_v2] ledger.emit_raised"

    # Log tag when classification raises at the emission site (PRD-C1). The
    # classifier is pure + total, so this should never fire — but the emit path
    # degrades to ``class=unknown`` / ``basis=default`` (honest fallback) rather
    # than failing the tool call.
    CLASSIFY_RAISED = "[surfaces] action.classify_raised"


class Titles:
    """Fallback surface-title formatting (D1)."""

    SEPARATOR = " · "


__all__ = ["Keys", "Messages", "Titles", "Values"]
