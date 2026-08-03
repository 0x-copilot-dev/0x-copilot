"""Reason-coded, body-free contracts for one model call's context plan.

This module is the one place F5 states a context-planning *shape*: what may be
offered to a model call (:class:`ContextCandidate`), the form it may take
(:class:`ContextRepresentation`), what compressing it must preserve
(:class:`CompressionManifest`), and the durable record of the decision
(:class:`ContextPlan`).  Executable policy is deliberately elsewhere --
candidate providers, the allocator, the evidence resolver registry, and the
``read_evidence`` tool are sibling lanes -- so each of them states its contract
once and reads it from here rather than restating it.

Four properties are structural here rather than conventional.

**Reason codes and digests, never bodies.**  Every field is a closed vocabulary
member, a lowercase SHA-256 digest, a bounded whitespace-free identifier, a
count, or a timezone-aware timestamp.  There is no field through which a
conversation turn, a tool result, a memory, a summary, a selector expression, or
a prompt could enter -- the PRD sketch's ``inline_content`` is *absent on
purpose*.  The assembled prompt carries content; a plan carries only the digest
of what the content was.  Because bounded printable-ASCII cannot hold prose, the
property is enforced by the accepted character set rather than by review, and
:mod:`tests.unit.agent_runtime.context.test_context_contracts` proves it by
injecting a seeded secret into every field of every contract rather than by
reading field names.

**Lossiness is declared, never inferred.**  :class:`ContextLossiness` has no
default anywhere.  A representation that retains the whole source must say
``none``; one that retains exact fragments must say ``extractive``; one whose
text a model wrote must say ``abstractive``; one that retains no text at all
must say ``elided``.  The PRD sketches three members: the fourth exists because
``reference`` and ``omitted`` are modes in the same closed vocabulary and
neither retains content, so forcing them to claim one of the three would make
lossiness a lie exactly where this contract claims it is explicit.

**Authority can only narrow.**  A candidate whose trusted lifecycle does not
admit it to model context is not *representable* in a plan: the decision must be
an omission, and the omission reason is the single one
:meth:`ContextOmissionReason.for_lifecycle` derives.  A protected candidate can
never be omitted for a budget or relevance reason, class-1 material can only be
carried whole, and :meth:`ContextCandidateKind.max_priority_class` stops
retrieved material from claiming the safety tier.  Every one of those is
re-checked on parse, so no construction path -- builder, adapter, test, or a
later lane -- can route around them.

**The plan is reconstructable, not merely reproducible.**  A durable plan record
stores both a decision and every input it was made from, and re-plans on every
parse.  A :class:`ContextPlan` stores its candidates, its limits, and its
revisions beside its decisions, and :class:`ContextPlanReconstruction` re-derives
the decision ordering, the token arithmetic, the input digest, and the plan
digest from them.  A reordered, retotalled, misattributed, or truncated plan
therefore fails at parse time -- on the original append *and* on every replay
after a restart.  The allocation itself belongs to the sibling allocator lane;
when it lands it binds here by equality, because a plan this contract cannot
reproduce is one it refuses to hold.

Three vocabularies are imported rather than restated.  ``Sha256Hex``,
``ControlToken``, and ``OpaqueRefValue`` come from the shared revision-binding
primitive, whose docstring already names F5 evidence refs as one of its
adopters, so a context ref and a capability ref cannot drift into two accepted
widths.  :class:`~agent_runtime.answer_verification.EvidenceAccessState` and
:class:`~agent_runtime.answer_verification.EvidenceTrustClass` come from F12,
which owns the same two ideas -- a resolver-owned access result and a
resolver-owned trust class that model output may never supply.  Material
selected here becomes the evidence an answer is verified against, and one
vocabulary end to end is what lets that be an equality check.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from agent_runtime.answer_verification import (
    EvidenceAccessState,
    EvidenceTrustClass,
)
from agent_runtime.control_plane.revision_binding import (
    ControlToken,
    OpaqueRefValue,
    Sha256Hex,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class ContextPlanningError(ValueError):
    """Base typed, model-safe failure of an F5 context-planning contract.

    Derived from :class:`ValueError` so a failure raised inside a Pydantic
    validator surfaces as a ``ValidationError`` carrying the safe message,
    while a direct call site still sees the typed class.
    """


class ContextRepresentationRejected(ContextPlanningError):
    """A representation did not state what its form requires it to state."""

    class Messages:
        """Safe public messages for representation refusals."""

        LOSSINESS_NOT_ADMITTED: ClassVar[str] = (
            "this representation mode does not admit the declared lossiness"
        )
        FULL_MUST_BE_THE_SOURCE: ClassVar[str] = (
            "a full representation must digest to its own source"
        )
        COMPRESSED_MUST_BE_DIFFERENT: ClassVar[str] = (
            "a compressed representation cannot digest to its whole source"
        )
        MISSING_CONTENT_DIGEST: ClassVar[str] = (
            "every admitted representation must digest the bytes it contributes"
        )
        OMITTED_MUST_BE_EMPTY: ClassVar[str] = (
            "an omitted representation cannot carry tokens, refs, spans, "
            "or a compression manifest"
        )
        REFERENCE_REQUIRES_REF: ClassVar[str] = (
            "a reference representation must carry the ref it defers to"
        )
        MISSING_MANIFEST: ClassVar[str] = (
            "a compressed representation must carry its compression manifest"
        )
        UNEXPECTED_MANIFEST: ClassVar[str] = (
            "an uncompressed representation cannot carry a compression manifest"
        )
        MANIFEST_NOT_SOURCE_LINKED: ClassVar[str] = (
            "a compression manifest must preserve the exact source digest, "
            "spans, lossiness, and output digest of its representation"
        )
        EXTRACT_REQUIRES_SPANS: ClassVar[str] = (
            "an extractive representation must record the spans it extracted"
        )
        GENERATED_AT_REQUIRED: ClassVar[str] = (
            "a compressed representation must record when it was generated"
        )


class CompressionManifestRejected(ContextPlanningError):
    """A compression record did not preserve what compression must preserve."""

    class Messages:
        """Safe public messages for compression-manifest refusals."""

        NOT_A_COMPRESSION: ClassVar[str] = (
            "a compression manifest describes extractive or abstractive loss only"
        )
        SUMMARIZER_REQUIRED: ClassVar[str] = (
            "abstractive compression must record its summarizer model, "
            "prompt revision, and summarizer revision"
        )
        SPANS_REQUIRED: ClassVar[str] = (
            "extractive compression must record the spans it retained"
        )
        NOT_SMALLER: ClassVar[str] = (
            "compression output cannot exceed its source in tokens"
        )
        CACHE_KEY_MISMATCH: ClassVar[str] = (
            "compression cache key does not match its source digest, target "
            "size, policy revision, and summarizer revision"
        )
        NAIVE_TIMESTAMP: ClassVar[str] = "compression timestamps must be timezone-aware"


class ContextSpanRejected(ContextPlanningError):
    """A source span did not locate anything, or located it two ways."""

    class Messages:
        """Safe public messages for span refusals."""

        RANGE_REQUIRED: ClassVar[str] = (
            "a ranged locator requires both a start and an end offset"
        )
        RANGE_NOT_ORDERED: ClassVar[str] = "a span end must be greater than its start"
        LOCATOR_REF_REQUIRED: ClassVar[str] = (
            "an opaque locator requires the ref its source domain issued"
        )
        WHOLE_SOURCE_IS_UNLOCATED: ClassVar[str] = (
            "a whole-source span cannot carry offsets or a locator ref"
        )


class ContextAuthorityWidened(ContextPlanningError):
    """A plan tried to admit, promote, or protect more than it was granted."""

    class Messages:
        """Safe public messages for authority refusals."""

        INADMISSIBLE_SOURCE: ClassVar[str] = (
            "a source the runtime cannot currently admit must be omitted"
        )
        WRONG_OMISSION_REASON: ClassVar[str] = (
            "an inadmissible source must be omitted for its own lifecycle reason"
        )
        PROTECTED_EVICTED: ClassVar[str] = (
            "protected context can be omitted only when it cannot be admitted"
        )
        IMMUTABLE_TRUNCATED: ClassVar[str] = (
            "immutable safety, authority, and protocol context cannot be "
            "carried in a reduced form"
        )
        PRIORITY_PROMOTED: ClassVar[str] = (
            "this candidate kind cannot claim that priority class"
        )
        UNKNOWN_KIND_PROMOTED: ClassVar[str] = (
            "a candidate of unknown kind must take the lowest priority class"
        )
        PROTECTION_CLAIMED: ClassVar[str] = (
            "only a protected priority class may be included as protected"
        )
        COMPRESSION_SCOPE_WIDENED: ClassVar[str] = (
            "a compression cannot be authorized more widely than the candidate "
            "it represents"
        )


class ContextCandidateRejected(ContextPlanningError):
    """A candidate offered something it cannot offer."""

    class Messages:
        """Safe public messages for candidate refusals."""

        OPTIONS_NOT_CANONICAL: ClassVar[str] = (
            "representation options must be unique and ordered by fidelity"
        )
        OMISSION_IS_NOT_AN_OPTION: ClassVar[str] = (
            "omission is a planner decision, never an offered representation"
        )
        OPTION_EXCEEDS_SOURCE: ClassVar[str] = (
            "a representation option cannot cost more than its whole source"
        )
        FULL_OPTION_IS_THE_SOURCE: ClassVar[str] = (
            "a full representation option must cost exactly its whole source"
        )
        UNOFFERED_REPRESENTATION: ClassVar[str] = (
            "a decision cannot admit a representation the candidate never offered"
        )
        SOURCE_MISMATCH: ClassVar[str] = (
            "a decision must represent its own candidate's source"
        )


class ContextDecisionRejected(ContextPlanningError):
    """A decision was neither an inclusion nor an omission."""

    class Messages:
        """Safe public messages for decision refusals."""

        AMBIGUOUS_OUTCOME: ClassVar[str] = (
            "a decision states exactly one inclusion or omission reason"
        )
        REASON_CONTRADICTS_MODE: ClassVar[str] = (
            "an omission reason requires the omitted representation mode"
        )


class ContextPlanReconstructionFailed(ContextPlanningError):
    """A persisted plan is not the plan its own inputs describe."""

    class Messages:
        """Safe public messages for reconstruction refusals."""

        DECISIONS_NOT_ORDERED: ClassVar[str] = (
            "plan decisions are not the deterministic order of their candidates"
        )
        DUPLICATE_CANDIDATE: ClassVar[str] = (
            "a plan decides each candidate exactly once"
        )
        CANDIDATE_MISMATCH: ClassVar[str] = (
            "a decision must carry the plan's own record of its candidate"
        )
        ALLOCATION_MISMATCH: ClassVar[str] = (
            "allocated tokens are not the sum of the admitted representations"
        )
        BUDGET_EXCEEDED: ClassVar[str] = (
            "a plan cannot allocate past its model context limit"
        )
        INPUT_DIGEST_MISMATCH: ClassVar[str] = (
            "plan input digest does not match the candidates, limits, and "
            "revisions the plan was decided from"
        )
        PLAN_DIGEST_MISMATCH: ClassVar[str] = (
            "plan digest does not match its inputs and decisions"
        )
        NAIVE_TIMESTAMP: ClassVar[str] = "plan timestamps must be timezone-aware"


class ContextBounds:
    """The one set of structural ceilings every F5 context contract shares.

    These are hard limits, not a configured policy: a resolved policy may only
    choose a value *within* them, and the contracts refuse to represent a wider
    one -- so a bound the policy can express is always a bound a plan can carry.
    ``MAX_CANDIDATES`` is the PRD's initial planning cap, and it is what keeps
    the ``O(C log C)` ordering derivation below bounded on every parse.
    """

    MAX_CANDIDATES: ClassVar[int] = 500
    MAX_SPANS: ClassVar[int] = 64
    MAX_REPRESENTATION_OPTIONS: ClassVar[int] = 4
    MAX_RELEVANCE_SCORE: ClassVar[int] = 1_000
    UNKNOWN_RELEVANCE_SCORE: ClassVar[int] = 0


class ContextDigests:
    """The one derivation every digest in this module goes through.

    Deliberately unkeyed, exactly like a catalog revision: a plan digest must be
    recomputable from the plan alone by anyone holding the plan, which is what
    makes "the same inputs produce the same plan" an equality check instead of
    two implementations that could drift.
    """

    @staticmethod
    def of(value: object) -> str:
        """Return the canonical SHA-256 identity of one structured value."""

        return canonical_json_sha256(value)


class ContextScopeDimension(StrEnum):
    """Closed set of dimensions a context authorization scope may narrow to."""

    SUBJECT = "subject"
    RUN = "run"
    CONVERSATION = "conversation"
    PROJECT = "project"


class ContextCandidateKind(StrEnum):
    """Closed set of runtime providers a context candidate can come from.

    The kind is *routing and provenance*, never authority.  Its one authority
    relevance is :meth:`max_priority_class`: material a connector, a memory
    write, or a previous turn produced can never claim the tier that holds
    immutable safety and operation protocol, which is the PRD guardrail
    "never place untrusted retrieved content in the system-policy tier" made
    structural rather than reviewed.
    """

    SYSTEM_POLICY = "system_policy"
    CAPABILITY_SCHEMA = "capability_schema"
    CURRENT_REQUEST = "current_request"
    APPROVAL_STATE = "approval_state"
    TASK_PLAN_STATE = "task_plan_state"
    SKILL = "skill"
    CITATION = "citation"
    ARTIFACT = "artifact"
    WORKSPACE_REF = "workspace_ref"
    TOOL_OBSERVATION = "tool_observation"
    CONVERSATION_TURN = "conversation_turn"
    CONTINUITY_SUMMARY = "continuity_summary"
    MEMORY = "memory"
    UNKNOWN = "unknown"

    @classmethod
    def conservative(cls) -> "ContextCandidateKind":
        """Return the kind an unrecognized provider record resolves to."""

        return cls.UNKNOWN

    def max_priority_class(self) -> "ContextPriorityClass":
        """Return the most protected class this kind may ever claim.

        A kind this table does not know resolves to
        :meth:`ContextPriorityClass.conservative`, so a widened vocabulary
        cannot silently acquire the right to claim the safety tier.
        """

        return ContextVocabularyTables.PRIORITY_CEILING_BY_KIND.get(
            self,
            ContextPriorityClass.conservative(),
        )


class ContextPriorityClass(StrEnum):
    """Closed eviction ordering over variable context, most protected first.

    Declaration order *is* the priority order, so a member added at the top
    becomes the new most-protected tier and a member added at the bottom can
    never silently become one.  The three protected classes are the PRD's
    non-negotiable material: immutable safety and operation protocol, the
    current request with its explicit constraints, and live approval state.
    They can leave a plan only when the runtime cannot admit their source at
    all -- never to make room.

    :meth:`conservative` returns the *last* member rather than the first.  For
    a narrowing vocabulary the safe default is the tightest member; for a
    priority vocabulary it is the most evictable one, because promoting
    unclassified material into a protected tier is the failure this ordering
    exists to prevent.
    """

    SAFETY_AUTHORITY_PROTOCOL = "safety_authority_protocol"
    CURRENT_INTENT_CONSTRAINTS = "current_intent_constraints"
    APPROVAL_GATE_STATE = "approval_gate_state"
    ACTIVE_PLAN_OPERATIONS = "active_plan_operations"
    SELECTED_SKILLS_EVIDENCE = "selected_skills_evidence"
    RECENT_CONVERSATION = "recent_conversation"
    RECALLED_MEMORY = "recalled_memory"
    LOW_RELEVANCE_HISTORY = "low_relevance_history"

    @property
    def priority_rank(self) -> int:
        """Return the eviction rank, where ``0`` is the most protected class."""

        return list(type(self)).index(self)

    @property
    def protected(self) -> bool:
        """Return whether this class may never be evicted to free budget."""

        return (
            self.priority_rank <= ContextPriorityClass.APPROVAL_GATE_STATE.priority_rank
        )

    @property
    def immutable(self) -> bool:
        """Return whether this class must be carried whole or not at all."""

        return self is ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL

    @classmethod
    def conservative(cls) -> "ContextPriorityClass":
        """Return the class unclassified material resolves to."""

        return list(cls)[-1]

    def no_more_protected_than(self, ceiling: "ContextPriorityClass") -> bool:
        """Return whether this class sits at or below ``ceiling``."""

        return self.priority_rank >= ceiling.priority_rank


class ContextRepresentationMode(StrEnum):
    """Closed set of forms one candidate can take in a model call.

    Declaration order is the PRD's preference order: prefer whole bounded
    content, then an exact excerpt, then a source-linked summary, then a compact
    reference, and only then omission.  :attr:`fidelity_rank` reads that order,
    so the canonical ordering of a candidate's offered options cannot drift away
    from the order the planner is told to prefer.
    """

    FULL = "full"
    EXCERPT = "excerpt"
    SUMMARY = "summary"
    REFERENCE = "reference"
    OMITTED = "omitted"

    @property
    def fidelity_rank(self) -> int:
        """Return the preference rank, where ``0`` retains the most."""

        return list(type(self)).index(self)

    @property
    def admitted(self) -> bool:
        """Return whether this mode contributes anything to the model call."""

        return self is not ContextRepresentationMode.OMITTED

    @property
    def compressed(self) -> bool:
        """Return whether this mode requires a compression manifest."""

        return self in {
            ContextRepresentationMode.EXCERPT,
            ContextRepresentationMode.SUMMARY,
        }

    def admits_lossiness(self, lossiness: "ContextLossiness") -> bool:
        """Return whether this mode may declare ``lossiness``.

        Every mode admits exactly the lossiness its form implies, so the pair
        can never disagree: the only way to represent "nothing was lost" is to
        carry the whole source, and the only way to carry the whole source is to
        declare that nothing was lost.
        """

        return lossiness in ContextVocabularyTables.LOSSINESS_BY_MODE.get(
            self,
            frozenset(),
        )


class ContextLossiness(StrEnum):
    """Closed declaration of what survived into a representation.

    Ordered by how much of the source survives verbatim.  ``ELIDED`` extends the
    PRD's three-member sketch because ``reference`` and ``omitted`` retain no
    text at all, and making them claim ``none``, ``extractive``, or
    ``abstractive`` would state something false about material that is not
    there.

    :meth:`conservative` returns ``ABSTRACTIVE`` rather than ``ELIDED``: an
    undeclared lossiness means "text is present and its fidelity is unknown",
    and the safe reading of unknown fidelity is that a model wrote it.
    ``ELIDED`` is a stronger claim, not a safer one.
    """

    NONE = "none"
    EXTRACTIVE = "extractive"
    ABSTRACTIVE = "abstractive"
    ELIDED = "elided"

    @property
    def verbatim(self) -> bool:
        """Return whether every retained character came from the source."""

        return self in {ContextLossiness.NONE, ContextLossiness.EXTRACTIVE}

    @property
    def compressed(self) -> bool:
        """Return whether a compression manifest describes this loss."""

        return self in {ContextLossiness.EXTRACTIVE, ContextLossiness.ABSTRACTIVE}

    @classmethod
    def conservative(cls) -> "ContextLossiness":
        """Return the lossiness an undeclared representation resolves to."""

        return cls.ABSTRACTIVE


class ContextSpanLocator(StrEnum):
    """Closed set of ways a retained fragment points back at its source.

    ``SELECTOR`` and ``RECORD_KEY`` deliberately carry no expression: a CSS
    path, an XPath, or a JSON pointer is content-shaped and could smuggle the
    very text this module refuses to hold.  Those locators travel as the opaque
    ref their own source domain issued, exactly as F12 carries ``locator_ref``.
    """

    CHARACTER_RANGE = "character_range"
    BYTE_RANGE = "byte_range"
    LINE_RANGE = "line_range"
    SELECTOR = "selector"
    RECORD_KEY = "record_key"
    WHOLE_SOURCE = "whole_source"

    @property
    def ranged(self) -> bool:
        """Return whether this locator is defined by numeric offsets."""

        return self in {
            ContextSpanLocator.CHARACTER_RANGE,
            ContextSpanLocator.BYTE_RANGE,
            ContextSpanLocator.LINE_RANGE,
        }

    @property
    def opaque(self) -> bool:
        """Return whether this locator is defined by a source-issued ref."""

        return self in {
            ContextSpanLocator.SELECTOR,
            ContextSpanLocator.RECORD_KEY,
        }


class ContextInclusionReason(StrEnum):
    """Closed reason one candidate was admitted to a model call.

    ``PROTECTED_CLASS`` is the only member that asserts authority rather than
    judgement, and :class:`ContextCandidateDecision` refuses it for any class
    :attr:`ContextPriorityClass.protected` does not cover -- so "this was
    included because it could not be dropped" cannot be claimed for material
    that could.
    """

    PROTECTED_CLASS = "protected_class"
    CURRENT_INTENT = "current_intent"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE_PLAN = "active_plan"
    EXPLICIT_PIN = "explicit_pin"
    HIGH_RELEVANCE = "high_relevance"
    RECENCY_WINDOW = "recency_window"
    CONTINUITY = "continuity"
    RETRIEVABLE_REFERENCE = "retrievable_reference"


class ContextOmissionReason(StrEnum):
    """Closed reason one candidate did not reach a model call.

    Members split into two families that the plan treats very differently.  A
    *budget* reason says the runtime chose not to spend context on admissible
    material; an *admissibility* reason says the runtime was not allowed to, or
    could not establish that it was.  Protected context may only ever carry the
    second family, and an inadmissible source may only ever carry the exact
    member :meth:`for_lifecycle` derives -- so "we dropped your explicit
    constraint to save tokens" and "we silently reclassified a revoked source as
    low-relevance" are both unrepresentable rather than merely discouraged.
    """

    BUDGET_EXHAUSTED = "budget_exhausted"
    CLASS_SHARE_EXHAUSTED = "class_share_exhausted"
    LOW_RELEVANCE = "low_relevance"
    SUPERSEDED = "superseded"
    DUPLICATE = "duplicate"
    UNAUTHORIZED = "unauthorized"
    REVOKED = "revoked"
    RETENTION_EXPIRED = "retention_expired"
    SOURCE_UNAVAILABLE = "source_unavailable"
    COMPRESSION_FORBIDDEN = "compression_forbidden"
    ADMISSIBILITY_NOT_ESTABLISHED = "admissibility_not_established"

    @property
    def budgetary(self) -> bool:
        """Return whether this reason says admissible material was dropped."""

        return self in ContextVocabularyTables.BUDGETARY_OMISSIONS

    @classmethod
    def conservative(cls) -> "ContextOmissionReason":
        """Return the reason an unexplained omission resolves to.

        Unknown means "we could not establish that this may be used", never
        "this was not worth the tokens": the first is a safe thing to record
        about material that might have been protected, the second is a claim
        nobody made.
        """

        return cls.ADMISSIBILITY_NOT_ESTABLISHED

    @classmethod
    def for_access_state(cls, state: EvidenceAccessState) -> "ContextOmissionReason":
        """Return the one omission reason a trusted access state produces.

        Deriving it rather than letting a caller pick is what makes the
        inadmissibility check in :class:`ContextCandidateDecision` an equality
        test.  An access state this mapping does not know resolves to
        :meth:`conservative`, so a widened upstream vocabulary fails closed
        instead of falling through to "admissible".
        """

        return ContextVocabularyTables.OMISSION_BY_ACCESS_STATE.get(
            state,
            cls.conservative(),
        )


class ContextVocabularyTables:
    """The immutable tables the closed context vocabularies resolve through.

    Stated as data rather than as chains of conditionals so each policy is
    legible at one glance and extending a vocabulary is a single entry.  They
    are :class:`~types.MappingProxyType` rather than plain dictionaries because
    two of them govern authority -- which class a kind may claim, and which
    omission an access state forces -- and a mutable module attribute that
    decides authority is a mutable authority.

    Every reader looks these up with a conservative fallback rather than a
    direct subscript, so a member added to a vocabulary without a table entry
    fails closed on the safe side instead of raising at an arbitrary call site.
    """

    PRIORITY_CEILING_BY_KIND: ClassVar[
        Mapping[ContextCandidateKind, ContextPriorityClass]
    ] = MappingProxyType(
        {
            ContextCandidateKind.SYSTEM_POLICY: (
                ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL
            ),
            ContextCandidateKind.CAPABILITY_SCHEMA: (
                ContextPriorityClass.SAFETY_AUTHORITY_PROTOCOL
            ),
            ContextCandidateKind.CURRENT_REQUEST: (
                ContextPriorityClass.CURRENT_INTENT_CONSTRAINTS
            ),
            ContextCandidateKind.APPROVAL_STATE: (
                ContextPriorityClass.APPROVAL_GATE_STATE
            ),
            ContextCandidateKind.TASK_PLAN_STATE: (
                ContextPriorityClass.ACTIVE_PLAN_OPERATIONS
            ),
            ContextCandidateKind.SKILL: ContextPriorityClass.SELECTED_SKILLS_EVIDENCE,
            ContextCandidateKind.CITATION: (
                ContextPriorityClass.SELECTED_SKILLS_EVIDENCE
            ),
            ContextCandidateKind.ARTIFACT: (
                ContextPriorityClass.SELECTED_SKILLS_EVIDENCE
            ),
            ContextCandidateKind.WORKSPACE_REF: (
                ContextPriorityClass.SELECTED_SKILLS_EVIDENCE
            ),
            ContextCandidateKind.TOOL_OBSERVATION: (
                ContextPriorityClass.SELECTED_SKILLS_EVIDENCE
            ),
            ContextCandidateKind.CONVERSATION_TURN: (
                ContextPriorityClass.RECENT_CONVERSATION
            ),
            ContextCandidateKind.CONTINUITY_SUMMARY: (
                ContextPriorityClass.RECENT_CONVERSATION
            ),
            ContextCandidateKind.MEMORY: ContextPriorityClass.RECALLED_MEMORY,
            ContextCandidateKind.UNKNOWN: ContextPriorityClass.LOW_RELEVANCE_HISTORY,
        }
    )
    """The most protected class each candidate kind may claim."""

    LOSSINESS_BY_MODE: ClassVar[
        Mapping[ContextRepresentationMode, frozenset[ContextLossiness]]
    ] = MappingProxyType(
        {
            ContextRepresentationMode.FULL: frozenset({ContextLossiness.NONE}),
            ContextRepresentationMode.EXCERPT: frozenset({ContextLossiness.EXTRACTIVE}),
            ContextRepresentationMode.SUMMARY: frozenset(
                {ContextLossiness.EXTRACTIVE, ContextLossiness.ABSTRACTIVE}
            ),
            ContextRepresentationMode.REFERENCE: frozenset({ContextLossiness.ELIDED}),
            ContextRepresentationMode.OMITTED: frozenset({ContextLossiness.ELIDED}),
        }
    )
    """The loss each representation form is allowed to declare."""

    BUDGETARY_OMISSIONS: ClassVar[frozenset[ContextOmissionReason]] = frozenset(
        {
            ContextOmissionReason.BUDGET_EXHAUSTED,
            ContextOmissionReason.CLASS_SHARE_EXHAUSTED,
            ContextOmissionReason.LOW_RELEVANCE,
            ContextOmissionReason.SUPERSEDED,
            ContextOmissionReason.DUPLICATE,
        }
    )
    """Reasons that say admissible material was deliberately not spent on."""

    OMISSION_BY_ACCESS_STATE: ClassVar[
        Mapping[EvidenceAccessState, ContextOmissionReason]
    ] = MappingProxyType(
        {
            EvidenceAccessState.UNAUTHORIZED: ContextOmissionReason.UNAUTHORIZED,
            EvidenceAccessState.REVOKED: ContextOmissionReason.REVOKED,
            EvidenceAccessState.EXPIRED: ContextOmissionReason.RETENTION_EXPIRED,
            EvidenceAccessState.NOT_FOUND: ContextOmissionReason.SOURCE_UNAVAILABLE,
            EvidenceAccessState.UNAVAILABLE: ContextOmissionReason.SOURCE_UNAVAILABLE,
        }
    )
    """The one omission each inadmissible access state forces.

    ``AUTHORIZED`` is deliberately absent: an authorized source has no omission
    reason of its own, and a lookup miss resolves conservatively rather than to
    a member that would read as a decision nobody made.
    """


class ContextSourceSpan(RuntimeContract):
    """One retained fragment's pointer back into its source.

    Carries offsets and an opaque source-issued ref only.  There is no field for
    the fragment itself, and none for the expression that selected it, so a span
    can say *where* without ever saying *what*.
    """

    span_id: ControlToken
    locator: ContextSpanLocator
    start: NonNegativeInt | None = None
    end: PositiveInt | None = None
    locator_ref: OpaqueRefValue | None = None

    @model_validator(mode="after")
    def _span_locates_exactly_one_way(self) -> Self:
        if self.locator.ranged:
            if self.start is None or self.end is None:
                raise ContextSpanRejected(ContextSpanRejected.Messages.RANGE_REQUIRED)
            if self.end <= self.start:
                raise ContextSpanRejected(
                    ContextSpanRejected.Messages.RANGE_NOT_ORDERED
                )
            return self
        if self.locator.opaque:
            if self.locator_ref is None:
                raise ContextSpanRejected(
                    ContextSpanRejected.Messages.LOCATOR_REF_REQUIRED
                )
            return self
        if self.start is not None or self.end is not None or self.locator_ref:
            raise ContextSpanRejected(
                ContextSpanRejected.Messages.WHOLE_SOURCE_IS_UNLOCATED
            )
        return self


class ContextAuthorizationScope(RuntimeContract):
    """The closed scope a candidate and any compression of it were authorized in.

    ``subject_fingerprint`` is mandatory, so an unscoped candidate is not
    representable and cross-subject reuse of a summary is structurally
    impossible.  The optional dimensions narrow further; ``None`` means the
    scope was never bound to that dimension, never that the dimension is
    satisfied.  This mirrors the shared revision-binding scope on purpose: the
    two describe the same idea, and a context scope that admitted a shape a
    bound ref refuses would be a gap between plan time and read time.
    """

    subject_fingerprint: Sha256Hex
    run_id: ControlToken | None = None
    conversation_id: ControlToken | None = None
    project_id: ControlToken | None = None

    @property
    def dimensions(self) -> frozenset[ContextScopeDimension]:
        """Return every dimension this scope is actually bound to."""

        bound = {ContextScopeDimension.SUBJECT}
        if self.run_id is not None:
            bound.add(ContextScopeDimension.RUN)
        if self.conversation_id is not None:
            bound.add(ContextScopeDimension.CONVERSATION)
        if self.project_id is not None:
            bound.add(ContextScopeDimension.PROJECT)
        return frozenset(bound)

    def narrows_to(self, other: "ContextAuthorizationScope") -> bool:
        """Return whether this scope is at least as narrow as ``other``.

        Narrower means: the same subject, every dimension ``other`` binds bound
        here to the same value, and no dimension ``other`` binds left unbound.
        Binding *more* dimensions is narrowing and is allowed; binding fewer, or
        binding one differently, is widening and is not.
        """

        if self.subject_fingerprint != other.subject_fingerprint:
            return False
        if not other.dimensions <= self.dimensions:
            return False
        return (
            (other.run_id is None or other.run_id == self.run_id)
            and (
                other.conversation_id is None
                or other.conversation_id == self.conversation_id
            )
            and (other.project_id is None or other.project_id == self.project_id)
        )


class ContextSourceLifecycle(RuntimeContract):
    """Trusted lifecycle facts about one candidate's source.

    Every field is resolver-owned.  Model output, tool payloads, MCP
    descriptors, and memory content must never populate this contract: a forged
    lifecycle would widen nothing structurally, but it would defeat the
    admissibility check the plan performs on the caller's behalf.

    Legal hold is recorded because it changes *deletion*, not *admission*: held
    material is retained past its retention window but is not thereby made
    readable, so the property below never lets a hold rescue an expired or
    revoked source.
    """

    access_state: EvidenceAccessState
    trust_label: EvidenceTrustClass
    observed_at: datetime
    retention_until: datetime | None = None
    legal_hold: bool = False

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> Self:
        for moment in (self.observed_at, self.retention_until):
            if moment is not None and (
                moment.tzinfo is None or moment.utcoffset() is None
            ):
                raise CompressionManifestRejected(
                    CompressionManifestRejected.Messages.NAIVE_TIMESTAMP
                )
        return self

    def inadmissible_reason(self, at: datetime) -> ContextOmissionReason | None:
        """Return the omission reason this source forces, or ``None``.

        ``at`` is the plan's own recorded instant, not a live clock, so replay
        of a persisted plan reaches the same answer the original planning run
        did.
        """

        if self.access_state is not EvidenceAccessState.AUTHORIZED:
            return ContextOmissionReason.for_access_state(self.access_state)
        if self.retention_until is not None and self.retention_until <= at:
            return ContextOmissionReason.RETENTION_EXPIRED
        return None


class CompressionSummarizerIdentity(RuntimeContract):
    """Exactly which generator produced a compressed representation.

    All three fields are mandatory together.  A summary that can name its model
    but not the prompt revision it ran under is not reproducible, and a
    reproducible-looking record that is not is worse than an absent one.
    """

    model_id: ControlToken
    prompt_revision: ControlToken
    summarizer_revision: ControlToken


class CompressionManifest(RuntimeContract):
    """What compressing one source preserved about the source.

    A manifest exists only for a representation that lost something.  It pins
    the exact source digest, the spans that survived, the declared lossiness,
    the generator identity when a model wrote the text, and the scope the
    compression was authorized in -- which is the whole of "compression remains
    source-linked and lossiness is explicit", carried as digests and refs rather
    than as any of the material involved.

    ``cache_key`` is the PRD's cache identity -- source digest, target size,
    policy revision, summarizer revision -- and it is *re-derived on every
    parse* rather than trusted, so a manifest cannot be filed under a key that
    would let a different source, size, or generator revision serve it.
    """

    class Keys:
        """Field names in the derived cache identity."""

        SOURCE_DIGEST: ClassVar[str] = "source_digest"
        TARGET_TOKENS: ClassVar[str] = "target_tokens"
        POLICY_REVISION: ClassVar[str] = "policy_revision"
        SUMMARIZER_REVISION: ClassVar[str] = "summarizer_revision"

    manifest_id: ControlToken
    source_ref: OpaqueRefValue
    source_digest: Sha256Hex
    source_tokens: NonNegativeInt
    output_digest: Sha256Hex
    output_tokens: NonNegativeInt
    target_tokens: PositiveInt
    lossiness: ContextLossiness
    source_spans: tuple[ContextSourceSpan, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_SPANS,
    )
    summarizer: CompressionSummarizerIdentity | None = None
    authorization_scope: ContextAuthorizationScope
    policy_revision: ControlToken
    generated_at: datetime
    cache_key: Sha256Hex

    @property
    def may_originate_citation(self) -> bool:
        """Return whether a claim drawn from this text can cite its source.

        Only a retained span makes that possible.  An abstractive summary with
        no span mapping is compressed evidence a reader can weigh, never a
        citation a reader can follow, and the difference is decided here rather
        than by whoever renders the answer.
        """

        return bool(self.source_spans)

    @classmethod
    def derive_cache_key(
        cls,
        *,
        source_digest: str,
        target_tokens: int,
        policy_revision: str,
        summarizer_revision: str | None,
    ) -> str:
        """Return the one cache identity a compression may be stored under."""

        return ContextDigests.of(
            {
                cls.Keys.SOURCE_DIGEST: source_digest,
                cls.Keys.TARGET_TOKENS: target_tokens,
                cls.Keys.POLICY_REVISION: policy_revision,
                cls.Keys.SUMMARIZER_REVISION: summarizer_revision,
            }
        )

    @model_validator(mode="after")
    def _compression_is_source_linked(self) -> Self:
        if not self.lossiness.compressed:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.NOT_A_COMPRESSION
            )
        if self.lossiness is ContextLossiness.ABSTRACTIVE and self.summarizer is None:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.SUMMARIZER_REQUIRED
            )
        if self.lossiness is ContextLossiness.EXTRACTIVE and not self.source_spans:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.SPANS_REQUIRED
            )
        if self.output_tokens > self.source_tokens:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.NOT_SMALLER
            )
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.NAIVE_TIMESTAMP
            )
        expected = self.derive_cache_key(
            source_digest=self.source_digest,
            target_tokens=self.target_tokens,
            policy_revision=self.policy_revision,
            summarizer_revision=(
                None if self.summarizer is None else self.summarizer.summarizer_revision
            ),
        )
        if self.cache_key != expected:
            raise CompressionManifestRejected(
                CompressionManifestRejected.Messages.CACHE_KEY_MISMATCH
            )
        return self


class ContextRepresentationOption(RuntimeContract):
    """One form a candidate declares it *could* be admitted in.

    Omission is absent from this vocabulary on purpose: dropping a candidate is
    a planner decision that is always available, never something a provider
    offers, so an "omitted option" cannot be constructed and a plan cannot
    excuse an omission by pointing at one.
    """

    mode: ContextRepresentationMode
    token_count: NonNegativeInt
    lossiness: ContextLossiness
    content_ref: OpaqueRefValue | None = None

    @model_validator(mode="after")
    def _option_is_offerable(self) -> Self:
        if not self.mode.admitted:
            raise ContextCandidateRejected(
                ContextCandidateRejected.Messages.OMISSION_IS_NOT_AN_OPTION
            )
        if not self.mode.admits_lossiness(self.lossiness):
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.LOSSINESS_NOT_ADMITTED
            )
        return self

    def matches(self, representation: "ContextRepresentation") -> bool:
        """Return whether ``representation`` is exactly this offered form."""

        return (
            self.mode is representation.mode
            and self.token_count == representation.token_count
            and self.lossiness is representation.lossiness
        )


class ContextRepresentation(RuntimeContract):
    """The form one candidate actually took in one model call.

    ``source_digest`` is what the material was; ``content_digest`` is what
    reached the model.  Both are digests, and there is no third field holding
    either set of bytes -- the PRD sketch's ``inline_content`` is deliberately
    not modelled here, because a plan that could carry context is a plan that
    would eventually be read as one.

    The two digests are also the fidelity proof: a ``full`` representation must
    digest to its own source, and a compressed one must not.  "Nothing was lost"
    is therefore a checkable claim rather than a declared one.
    """

    mode: ContextRepresentationMode
    token_count: NonNegativeInt
    lossiness: ContextLossiness
    source_digest: Sha256Hex
    content_digest: Sha256Hex | None = None
    content_ref: OpaqueRefValue | None = None
    source_spans: tuple[ContextSourceSpan, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_SPANS,
    )
    compression: CompressionManifest | None = None
    generated_at: datetime | None = None

    @classmethod
    def omitted(cls, *, source_digest: str) -> Self:
        """Return the one representation an omitted candidate may carry."""

        return cls(
            mode=ContextRepresentationMode.OMITTED,
            token_count=0,
            lossiness=ContextLossiness.ELIDED,
            source_digest=source_digest,
        )

    @property
    def may_originate_citation(self) -> bool:
        """Return whether a claim drawn from this form can cite its source."""

        if not self.mode.admitted:
            return False
        if self.lossiness is ContextLossiness.NONE:
            return True
        if self.compression is None:
            return False
        return self.compression.may_originate_citation

    @model_validator(mode="after")
    def _representation_declares_its_own_loss(self) -> Self:
        self._check_mode_and_lossiness_agree()
        self._check_admitted_shape()
        self._check_compression_is_bound()
        return self

    def _check_mode_and_lossiness_agree(self) -> None:
        if not self.mode.admits_lossiness(self.lossiness):
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.LOSSINESS_NOT_ADMITTED
            )

    def _check_admitted_shape(self) -> None:
        if not self.mode.admitted:
            if (
                self.token_count
                or self.content_digest is not None
                or self.content_ref is not None
                or self.source_spans
                or self.compression is not None
                or self.generated_at is not None
            ):
                raise ContextRepresentationRejected(
                    ContextRepresentationRejected.Messages.OMITTED_MUST_BE_EMPTY
                )
            return
        if self.content_digest is None:
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.MISSING_CONTENT_DIGEST
            )
        if (
            self.mode is ContextRepresentationMode.REFERENCE
            and self.content_ref is None
        ):
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.REFERENCE_REQUIRES_REF
            )
        if self.mode is ContextRepresentationMode.FULL:
            if self.content_digest != self.source_digest:
                raise ContextRepresentationRejected(
                    ContextRepresentationRejected.Messages.FULL_MUST_BE_THE_SOURCE
                )
        elif self.content_digest == self.source_digest:
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.COMPRESSED_MUST_BE_DIFFERENT
            )
        if self.lossiness is ContextLossiness.EXTRACTIVE and not self.source_spans:
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.EXTRACT_REQUIRES_SPANS
            )

    def _check_compression_is_bound(self) -> None:
        if not self.mode.compressed:
            if self.compression is not None:
                raise ContextRepresentationRejected(
                    ContextRepresentationRejected.Messages.UNEXPECTED_MANIFEST
                )
            return
        if self.compression is None:
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.MISSING_MANIFEST
            )
        if self.generated_at is None:
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.GENERATED_AT_REQUIRED
            )
        if (
            self.compression.source_digest != self.source_digest
            or self.compression.output_digest != self.content_digest
            or self.compression.lossiness is not self.lossiness
            or self.compression.source_spans != self.source_spans
        ):
            raise ContextRepresentationRejected(
                ContextRepresentationRejected.Messages.MANIFEST_NOT_SOURCE_LINKED
            )


class ContextCandidate(RuntimeContract):
    """One piece of material a model call could be given, and what it costs.

    A candidate is *variable* context.  Content accounted in
    :attr:`ContextPlanLimits.fixed_tokens` has no candidate and no decision; a
    candidate's cost is always in :attr:`ContextPlan.allocated_tokens`, so the
    two can never double-count the same tokens.

    ``relevance_score`` is a bounded integer rather than a float because it
    participates in the plan digest, and it is optional because relevance is not
    always computed.  Absence resolves to
    :attr:`ContextBounds.UNKNOWN_RELEVANCE_SCORE` in the deterministic ordering:
    unknown relevance sorts last within its class, never first.
    """

    candidate_id: ControlToken
    kind: ContextCandidateKind
    source_ref: OpaqueRefValue
    source_digest: Sha256Hex
    scope: ContextAuthorizationScope
    lifecycle: ContextSourceLifecycle
    priority_class: ContextPriorityClass
    original_tokens: NonNegativeInt
    relevance_score: int | None = Field(
        default=None,
        ge=0,
        le=ContextBounds.MAX_RELEVANCE_SCORE,
    )
    representation_options: tuple[ContextRepresentationOption, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_REPRESENTATION_OPTIONS,
    )

    @property
    def effective_relevance(self) -> int:
        """Return the relevance the deterministic ordering uses."""

        if self.relevance_score is None:
            return ContextBounds.UNKNOWN_RELEVANCE_SCORE
        return self.relevance_score

    def offers(self, representation: ContextRepresentation) -> bool:
        """Return whether this candidate declared ``representation`` as an option."""

        return any(
            option.matches(representation) for option in self.representation_options
        )

    @model_validator(mode="after")
    def _candidate_cannot_promote_itself(self) -> Self:
        if self.kind is ContextCandidateKind.UNKNOWN and (
            self.priority_class is not ContextPriorityClass.conservative()
        ):
            raise ContextAuthorityWidened(
                ContextAuthorityWidened.Messages.UNKNOWN_KIND_PROMOTED
            )
        if not self.priority_class.no_more_protected_than(
            self.kind.max_priority_class()
        ):
            raise ContextAuthorityWidened(
                ContextAuthorityWidened.Messages.PRIORITY_PROMOTED
            )
        self._check_options_are_canonical()
        return self

    def _check_options_are_canonical(self) -> None:
        modes = tuple(option.mode for option in self.representation_options)
        ranks = tuple(mode.fidelity_rank for mode in modes)
        if len(set(modes)) != len(modes) or list(ranks) != sorted(ranks):
            raise ContextCandidateRejected(
                ContextCandidateRejected.Messages.OPTIONS_NOT_CANONICAL
            )
        for option in self.representation_options:
            if option.token_count > self.original_tokens:
                raise ContextCandidateRejected(
                    ContextCandidateRejected.Messages.OPTION_EXCEEDS_SOURCE
                )
            if (
                option.mode is ContextRepresentationMode.FULL
                and option.token_count != self.original_tokens
            ):
                raise ContextCandidateRejected(
                    ContextCandidateRejected.Messages.FULL_OPTION_IS_THE_SOURCE
                )


class ContextCandidateDecision(RuntimeContract):
    """Why one candidate did or did not reach the model, and in what form.

    The decision carries its own candidate, so a reader never has to consult
    another record to learn what the decision was made about -- and so the
    authority checks below can be re-run on every parse rather than only at the
    moment the planner ran.
    """

    candidate: ContextCandidate
    representation: ContextRepresentation
    inclusion_reason: ContextInclusionReason | None = None
    omission_reason: ContextOmissionReason | None = None

    @property
    def included(self) -> bool:
        """Return whether this candidate contributed to the model call."""

        return self.omission_reason is None

    @property
    def token_count(self) -> int:
        """Return the tokens this decision contributes to the allocation."""

        return self.representation.token_count

    def authority_violation(self, at: datetime) -> str | None:
        """Return the safe message for an authority breach as of ``at``.

        Stated as one method taking the instant rather than as a validator, so
        the decision can check itself against the moment its lifecycle was
        observed while :class:`ContextPlan` re-checks the very same rules
        against the moment the plan was created.  Two instants, one rule -- a
        source whose retention lapses between observation and planning is
        therefore caught by the plan rather than by nobody.
        """

        forced = self.candidate.lifecycle.inadmissible_reason(at)
        if forced is not None:
            if self.included:
                return ContextAuthorityWidened.Messages.INADMISSIBLE_SOURCE
            if self.omission_reason is not forced:
                return ContextAuthorityWidened.Messages.WRONG_OMISSION_REASON
            return None
        if not self.candidate.priority_class.protected:
            return None
        if self.omission_reason is not None and self.omission_reason.budgetary:
            return ContextAuthorityWidened.Messages.PROTECTED_EVICTED
        if (
            self.candidate.priority_class.immutable
            and self.representation.mode is not ContextRepresentationMode.FULL
        ):
            return ContextAuthorityWidened.Messages.IMMUTABLE_TRUNCATED
        return None

    @model_validator(mode="after")
    def _decision_is_one_reasoned_outcome(self) -> Self:
        self._check_outcome_is_unambiguous()
        self._check_representation_is_the_candidates()
        violation = self.authority_violation(self.candidate.lifecycle.observed_at)
        if violation is not None:
            raise ContextAuthorityWidened(violation)
        return self

    def _check_outcome_is_unambiguous(self) -> None:
        if (self.inclusion_reason is None) == (self.omission_reason is None):
            raise ContextDecisionRejected(
                ContextDecisionRejected.Messages.AMBIGUOUS_OUTCOME
            )
        if self.included != self.representation.mode.admitted:
            raise ContextDecisionRejected(
                ContextDecisionRejected.Messages.REASON_CONTRADICTS_MODE
            )
        if (
            self.inclusion_reason is ContextInclusionReason.PROTECTED_CLASS
            and not self.candidate.priority_class.protected
        ):
            raise ContextAuthorityWidened(
                ContextAuthorityWidened.Messages.PROTECTION_CLAIMED
            )

    def _check_representation_is_the_candidates(self) -> None:
        if self.representation.source_digest != self.candidate.source_digest:
            raise ContextCandidateRejected(
                ContextCandidateRejected.Messages.SOURCE_MISMATCH
            )
        if not self.included:
            return
        if not self.candidate.offers(self.representation):
            raise ContextCandidateRejected(
                ContextCandidateRejected.Messages.UNOFFERED_REPRESENTATION
            )
        compression = self.representation.compression
        if compression is not None and not compression.authorization_scope.narrows_to(
            self.candidate.scope
        ):
            raise ContextAuthorityWidened(
                ContextAuthorityWidened.Messages.COMPRESSION_SCOPE_WIDENED
            )


class ContextPlanLimits(RuntimeContract):
    """The provider and policy ceilings one plan was allocated inside.

    Kept as one contract rather than four fields on the plan because they are
    also one of the three digest inputs: an allocation made under a different
    limit is a different plan, and folding the limits into the input digest is
    what makes that true structurally.
    """

    model_context_limit: PositiveInt
    reserved_output_tokens: NonNegativeInt
    fixed_tokens: NonNegativeInt
    safety_margin_tokens: NonNegativeInt = 0

    @property
    def available_tokens(self) -> int:
        """Return the budget variable context may be allocated from.

        Negative when the fixed content alone does not fit, which the plan
        refuses rather than resolving by truncating policy or tool schemas.
        """

        return (
            self.model_context_limit
            - self.reserved_output_tokens
            - self.fixed_tokens
            - self.safety_margin_tokens
        )


class ContextPlanRevisions(RuntimeContract):
    """The immutable revisions one plan is deterministic with respect to.

    Planning is reproducible for a candidate set *and* these three revisions.
    They are stored on the plan and folded into its input digest, so a plan
    produced by a different allocator build, a different policy, or a different
    tokenizer is a different plan rather than an unexplained difference between
    two runs that look the same.
    """

    policy_revision: ControlToken
    planner_revision: ControlToken
    tokenizer_revision: ControlToken


class ContextPlanReconstruction:
    """Every part of a context plan that is derived rather than asserted.

    This class is the reconstruction half of the lane's determinism property,
    and it is deliberately the *only* place these derivations exist.  The
    allocator lane that decides which candidates to admit does not get its own
    ordering, its own totals, or its own digest: it produces decisions, and
    :class:`ContextPlan` re-derives everything else from the inputs it stored.
    A plan whose ordering, arithmetic, or digests this class cannot reproduce is
    a plan the contract refuses to hold -- on first append and on every replay.
    """

    class Keys:
        """Field names in the two derived digests."""

        CANDIDATES: ClassVar[str] = "candidates"
        LIMITS: ClassVar[str] = "limits"
        REVISIONS: ClassVar[str] = "revisions"
        INPUT_DIGEST: ClassVar[str] = "input_digest"
        DECISIONS: ClassVar[str] = "decisions"

    @staticmethod
    def ordering_key(candidate: ContextCandidate) -> tuple[int, int, str]:
        """Return the total order one candidate takes in every plan.

        Most protected class first, then highest relevance, then candidate id.
        The last component is what makes the order *total*: two candidates that
        tie on class and relevance still have exactly one admissible ordering,
        so two providers enumerating the same set in different orders cannot
        mint two different plan digests for the same decision.
        """

        return (
            candidate.priority_class.priority_rank,
            -candidate.effective_relevance,
            candidate.candidate_id,
        )

    @classmethod
    def ordered(
        cls,
        candidates: tuple[ContextCandidate, ...],
    ) -> tuple[ContextCandidate, ...]:
        """Return ``candidates`` in the one order a plan may record them in."""

        return tuple(sorted(candidates, key=cls.ordering_key))

    @classmethod
    def input_digest(
        cls,
        *,
        candidates: tuple[ContextCandidate, ...],
        limits: ContextPlanLimits,
        revisions: ContextPlanRevisions,
    ) -> str:
        """Return the identity of everything a plan was decided from."""

        return ContextDigests.of(
            {
                cls.Keys.CANDIDATES: [
                    candidate.model_dump(mode="json")
                    for candidate in cls.ordered(candidates)
                ],
                cls.Keys.LIMITS: limits.model_dump(mode="json"),
                cls.Keys.REVISIONS: revisions.model_dump(mode="json"),
            }
        )

    @classmethod
    def plan_digest(
        cls,
        *,
        input_digest: str,
        decisions: tuple[ContextCandidateDecision, ...],
    ) -> str:
        """Return the identity of one plan's inputs together with its decisions."""

        return ContextDigests.of(
            {
                cls.Keys.INPUT_DIGEST: input_digest,
                cls.Keys.DECISIONS: [
                    decision.model_dump(mode="json") for decision in decisions
                ],
            }
        )

    @staticmethod
    def allocated_tokens(decisions: tuple[ContextCandidateDecision, ...]) -> int:
        """Return the tokens the admitted representations actually cost."""

        return sum(decision.token_count for decision in decisions if decision.included)


class ContextPlan(RuntimeContract):
    """The durable, body-free record of one model call's context.

    The plan stores its decisions *and* every input those decisions were made
    from -- the candidates with their scopes, lifecycles, costs, and offered
    forms; the limits the allocation happened inside; the policy, planner, and
    tokenizer revisions it is deterministic with respect to.  Storing both is
    what lets the record be checked rather than trusted: the validator below
    re-derives the decision ordering, the allocation total, the budget fit, the
    input digest, and the plan digest, and refuses the plan unless every one
    reproduces exactly.  A reordered, retotalled, misattributed, or truncated
    plan therefore fails at parse time, including on every replay after a
    restart.

    Nothing in it is context.  Every field is an identity, a closed vocabulary
    member, a digest, a count, or a timestamp, so "persist inclusion/omission
    reason codes and digests, not context bodies" is a property of the type
    rather than of the code that fills it in.
    """

    plan_id: ControlToken
    run_id: ControlToken
    model_call_id: ControlToken
    limits: ContextPlanLimits
    revisions: ContextPlanRevisions
    candidates: tuple[ContextCandidate, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    candidate_decisions: tuple[ContextCandidateDecision, ...] = Field(
        default_factory=tuple,
        max_length=ContextBounds.MAX_CANDIDATES,
    )
    allocated_tokens: NonNegativeInt
    input_digest: Sha256Hex
    plan_digest: Sha256Hex
    created_at: datetime

    @property
    def model_context_limit(self) -> int:
        """Return the provider context ceiling this plan was built for."""

        return self.limits.model_context_limit

    @property
    def reserved_output_tokens(self) -> int:
        """Return the output budget this plan withheld from allocation."""

        return self.limits.reserved_output_tokens

    @property
    def fixed_tokens(self) -> int:
        """Return the tokens spent on content that had no candidate decision."""

        return self.limits.fixed_tokens

    @property
    def policy_revision(self) -> str:
        """Return the policy revision this plan is deterministic against."""

        return self.revisions.policy_revision

    @property
    def omitted_decisions(self) -> tuple[ContextCandidateDecision, ...]:
        """Return every candidate the plan did not admit, in plan order."""

        return tuple(
            decision for decision in self.candidate_decisions if not decision.included
        )

    def reconstructs(self, other: "ContextPlan") -> bool:
        """Return whether two plans are the same decision over the same inputs.

        This is the equality check the allocator lane binds to.  Re-planning the
        stored inputs and comparing digests proves determinism without either
        side reimplementing the other's derivations -- the plan identity, the
        run, and the clock are excluded because they identify an *occasion*,
        not a decision.
        """

        return (
            self.input_digest == other.input_digest
            and self.plan_digest == other.plan_digest
        )

    @model_validator(mode="after")
    def _plan_reproduces_its_own_decision(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.NAIVE_TIMESTAMP
            )
        self._check_decisions_cover_ordered_candidates()
        self._check_authority_holds_at_plan_time()
        self._check_allocation_fits()
        self._check_digests_reproduce()
        return self

    def _check_authority_holds_at_plan_time(self) -> None:
        for decision in self.candidate_decisions:
            violation = decision.authority_violation(self.created_at)
            if violation is not None:
                raise ContextAuthorityWidened(violation)

    def _check_decisions_cover_ordered_candidates(self) -> None:
        decided = tuple(
            decision.candidate.candidate_id for decision in self.candidate_decisions
        )
        if len(set(decided)) != len(decided):
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.DUPLICATE_CANDIDATE
            )
        expected = tuple(
            candidate.candidate_id
            for candidate in ContextPlanReconstruction.ordered(self.candidates)
        )
        if decided != expected:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.DECISIONS_NOT_ORDERED
            )
        by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        for decision in self.candidate_decisions:
            if by_id[decision.candidate.candidate_id] != decision.candidate:
                raise ContextPlanReconstructionFailed(
                    ContextPlanReconstructionFailed.Messages.CANDIDATE_MISMATCH
                )

    def _check_allocation_fits(self) -> None:
        expected = ContextPlanReconstruction.allocated_tokens(self.candidate_decisions)
        if self.allocated_tokens != expected:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.ALLOCATION_MISMATCH
            )
        if self.allocated_tokens > self.limits.available_tokens:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.BUDGET_EXCEEDED
            )

    def _check_digests_reproduce(self) -> None:
        expected_input = ContextPlanReconstruction.input_digest(
            candidates=self.candidates,
            limits=self.limits,
            revisions=self.revisions,
        )
        if self.input_digest != expected_input:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.INPUT_DIGEST_MISMATCH
            )
        expected_plan = ContextPlanReconstruction.plan_digest(
            input_digest=expected_input,
            decisions=self.candidate_decisions,
        )
        if self.plan_digest != expected_plan:
            raise ContextPlanReconstructionFailed(
                ContextPlanReconstructionFailed.Messages.PLAN_DIGEST_MISMATCH
            )


__all__ = (
    "CompressionManifest",
    "CompressionManifestRejected",
    "CompressionSummarizerIdentity",
    "ContextAuthorityWidened",
    "ContextAuthorizationScope",
    "ContextBounds",
    "ContextCandidate",
    "ContextCandidateDecision",
    "ContextCandidateKind",
    "ContextCandidateRejected",
    "ContextDecisionRejected",
    "ContextDigests",
    "ContextInclusionReason",
    "ContextLossiness",
    "ContextOmissionReason",
    "ContextPlan",
    "ContextPlanLimits",
    "ContextPlanReconstruction",
    "ContextPlanReconstructionFailed",
    "ContextPlanRevisions",
    "ContextPlanningError",
    "ContextPriorityClass",
    "ContextRepresentation",
    "ContextRepresentationMode",
    "ContextRepresentationOption",
    "ContextRepresentationRejected",
    "ContextScopeDimension",
    "ContextSourceLifecycle",
    "ContextSourceSpan",
    "ContextSpanLocator",
    "ContextSpanRejected",
)
