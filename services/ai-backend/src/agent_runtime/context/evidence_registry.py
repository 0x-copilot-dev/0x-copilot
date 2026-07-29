"""One registry and one bounded reader for every kind of evidence recall.

Step 9 work items 7 and 8 ask for two things that are really one thing: source,
artifact, and prior-result resolution generalized into a single
:class:`EvidenceResolverRegistry` that reauthorizes at call time, and a single
bounded ``read_evidence`` contract the model may use to hydrate an opaque
evidence reference.  They are one thing because the registry is only safe if
nothing can reach a resolver except through the bounded reader, and the reader
is only useful if every source domain answers the same closed contract.

What this module owns
---------------------

* the closed evidence vocabulary -- kind, material lifecycle state, refusal
  reason, read outcome -- and the total, conservative table that maps a
  lifecycle state onto the shared primitive's authority vocabulary;
* :class:`EvidenceResolverPort`, the two-question protocol each source domain
  implements (*what is current now* and *give me at most N characters of it*);
* :class:`EvidenceGrant` / :class:`EvidenceGrantIndex`, the run's own record of
  which opaque tokens it made visible; and
* :class:`EvidenceResolverRegistry`, the stateless call-time path from one
  model-supplied token to either a bounded exact span or a typed refusal.

What this module does not own
-----------------------------

It writes no staleness semantics.  Binding integrity, feature scoping, scope
dimensions, subject/run fencing, revocation, and revision equality all belong to
:class:`~agent_runtime.control_plane.revision_binding.RevisionBindingRevalidator`
-- the Step RB primitive -- and F5 is its third adopter, not its sixth private
reimplementation.  It also introduces no authority: a resolver may only refuse
material the existing source-domain boundary would already have refused.

Three properties are structural rather than conventional
--------------------------------------------------------

**No decision is ever cached.**  The registry holds a resolver directory, a
revalidator, and limits -- no per-reference state of any kind, and no clock.
Every read re-asks the authority and re-asks the resolver, so §6.1's rule that
the run snapshot is not an authorization cache cannot be violated by omission.
A bounded "revalidated recently enough" window is exactly the shape §6.1
forbids, and is deliberately absent.

**A read is fenced on both sides.**  The shared primitive answers *was this
reference current a moment ago*; it has no vocabulary for *were these exact
bytes still current when they were produced*.  The registry therefore requires
the resolver to report the revision it actually read at, re-derives the bound
form, and refuses unless it equals the revision the authority confirmed.  A
source deleted, revoked, or edited between the decision and the read produces a
refusal, never stale material.

**A model can name a reference but never mint one.**  The digest on a bound
reference proves it was not edited after minting; it does not prove minting was
authorized, and it is computed from public inputs.  The model-facing surface is
therefore a bounded opaque *token*, resolved against the run's own grant index.
An unheld token refuses before any authority or resolver is consulted, so
guessing a locator buys nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import re
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal, Protocol, Self, runtime_checkable

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    BoundRevision,
    RevalidationDecision,
    RevalidationPolicy,
    RevalidationReason,
    RevisionAuthorityResult,
    RevisionAuthorityState,
    RevisionBindingRevalidator,
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionResolutionHandle,
    RevisionRevalidatorPort,
    RevisionScopeDimension,
    RevisionUseContext,
    Sha256Hex,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.redactor import Sensitive, SensitiveCategory
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_MAX_LOCATOR_LENGTH = 256
_MAX_REVISION_LENGTH = 512
_PRINTABLE_ASCII_PATTERN = r"^[!-~]+$"

EvidenceLocator = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=_MAX_LOCATOR_LENGTH,
        pattern=_PRINTABLE_ASCII_PATTERN,
    ),
]
"""A source domain's own bounded identifier for one piece of evidence.

Bounded printable ASCII, so a locator can never smuggle a body.  It is held by
the run's grant index and forwarded to that domain's own resolver; it is never
placed in a bound reference, a decision, a refusal, or a persisted fact.
"""

EvidenceRevisionValue = Annotated[
    str,
    StringConstraints(min_length=1, max_length=_MAX_REVISION_LENGTH),
]
"""A source domain's own revision token, in whatever shape that domain emits.

Deliberately wider than the primitive's ``OpaqueRefValue``: providers emit
ETags, content digests, sequence identifiers, and timestamps, and a value with
inner whitespace is representable here but not as a bound revision.  The
registry digests it, which is total, keeps the boundary body-free, and -- being
injective -- preserves the only relation the primitive is allowed to use.
"""


class EvidenceRegistryError(RuntimeError):
    """Base class for typed evidence failures with safe public messages."""


class EvidenceResolverAlreadyRegistered(EvidenceRegistryError):
    """Two resolvers claimed the same evidence kind."""

    MESSAGE_TEMPLATE: ClassVar[str] = (
        "an evidence resolver is already registered for kind={kind}"
    )

    def __init__(self, *, kind: "EvidenceKind") -> None:
        self.kind = kind
        super().__init__(self.MESSAGE_TEMPLATE.format(kind=kind.value))


class EvidenceGrantIndexFull(EvidenceRegistryError):
    """A run tried to make more evidence visible than the index admits."""

    MESSAGE_TEMPLATE: ClassVar[str] = (
        "this run may hold at most {limit} visible evidence references"
    )

    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        super().__init__(self.MESSAGE_TEMPLATE.format(limit=limit))


class EvidenceNotResolved(EvidenceRegistryError):
    """A caller required resolved evidence from a refusal."""

    MESSAGE_TEMPLATE: ClassVar[str] = "evidence was not resolved (reason={reason})"

    def __init__(self, *, reason: "EvidenceRefusalReason") -> None:
        self.reason = reason
        super().__init__(self.MESSAGE_TEMPLATE.format(reason=reason.value))


class EvidenceBooleanCoercion(TypeError):
    """A caller tried to coerce an evidence read result to a boolean."""

    MESSAGE: ClassVar[str] = (
        "evidence read results are not boolean; check is_resolved or call "
        "require_resolved so a refusal cannot read as retrieved material"
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class EvidenceSelectorUnsupported(EvidenceRegistryError):
    """A resolver cannot honour the selector this request carries.

    Raised by a resolver, converted by the registry into a typed refusal.  It
    exists so an unsupported selector cannot be silently widened into "return
    the whole thing" -- the conservative outcome is structural.
    """

    MESSAGE: ClassVar[str] = "this evidence source cannot honour the requested selector"

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class EvidenceKind(StrEnum):
    """Closed routing metadata for an evidence reference -- never authority.

    The kind selects which registered resolver answers.  It decides nothing
    about whether the material may be read: that is re-derived at use time from
    the shared primitive and the source domain's own lifecycle.
    """

    SOURCE = "source"
    ARTIFACT = "artifact"
    PRIOR_RESULT = "prior_result"
    CONVERSATION = "conversation"
    MEMORY = "memory"


class EvidenceMaterialState(StrEnum):
    """Closed lifecycle state a source domain may report for one locator.

    This is the vocabulary the shared primitive deliberately does not have.
    ``deleted``, ``retention_expired``, and ``access_revoked`` are all refusals
    there, but they are the *same* refusal there, and F5 must tell them apart:
    a model that asked for retention-expired material should not be told the
    reference was never issued, and an audit fact that cannot distinguish
    deletion from revocation cannot answer a retention question.

    The distinction never widens anything.  Admission is decided by the shared
    revalidator; this state only explains, and only ever refuses further.
    """

    AVAILABLE = "available"
    DELETED = "deleted"
    RETENTION_EXPIRED = "retention_expired"
    ACCESS_REVOKED = "access_revoked"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    @property
    def authority_state(self) -> RevisionAuthorityState:
        """Return the primitive's authority state this lifecycle maps onto."""

        return EvidenceLifecycleTables.AUTHORITY_STATE[self]

    @property
    def refusal_reason(self) -> "EvidenceRefusalReason | None":
        """Return the refusal this state produces, or ``None`` when readable."""

        return EvidenceLifecycleTables.REFUSAL_REASON[self]

    @property
    def is_readable(self) -> bool:
        """Return whether this state admits producing material at all."""

        return self is EvidenceMaterialState.AVAILABLE


class EvidenceRefusalReason(StrEnum):
    """Stable low-cardinality reason codes for every non-resolving read."""

    UNKNOWN_REFERENCE = "unknown_reference"
    UNREGISTERED_KIND = "unregistered_kind"
    NOT_CURRENT = "not_current"
    MATERIAL_DELETED = "material_deleted"
    MATERIAL_RETENTION_EXPIRED = "material_retention_expired"
    MATERIAL_ACCESS_REVOKED = "material_access_revoked"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    MATERIAL_UNKNOWN = "material_unknown"
    MATERIAL_SUPERSEDED = "material_superseded"
    SELECTOR_UNSUPPORTED = "selector_unsupported"
    READ_LIMIT_EXCEEDED = "read_limit_exceeded"
    BATCH_LIMIT_EXHAUSTED = "batch_limit_exhausted"
    RESOLVER_ERROR = "resolver_error"
    RESOLVER_CONTRACT_VIOLATION = "resolver_contract_violation"


class EvidenceLifecycleTables:
    """Closed lifecycle mappings, so no adapter can invent a pairing.

    Both tables are total over :class:`EvidenceMaterialState` and both are
    conservative: exactly one state maps to ``active`` and exactly one state
    carries no refusal, and they are the same state.  Everything a source
    domain can say other than "available" refuses, whether or not this module
    ever learns what it meant.
    """

    AUTHORITY_STATE: ClassVar[
        Mapping[EvidenceMaterialState, RevisionAuthorityState]
    ] = MappingProxyType(
        {
            EvidenceMaterialState.AVAILABLE: RevisionAuthorityState.ACTIVE,
            # Deleted and retention-expired material is not "revoked": the
            # subject's access never changed, the material stopped existing.
            # ``unknown`` is the primitive's closed way of saying a reference no
            # longer resolves, and it refuses out of scope.
            EvidenceMaterialState.DELETED: RevisionAuthorityState.UNKNOWN,
            EvidenceMaterialState.RETENTION_EXPIRED: RevisionAuthorityState.UNKNOWN,
            EvidenceMaterialState.ACCESS_REVOKED: RevisionAuthorityState.REVOKED,
            EvidenceMaterialState.UNAVAILABLE: RevisionAuthorityState.UNAVAILABLE,
            EvidenceMaterialState.UNKNOWN: RevisionAuthorityState.UNKNOWN,
        }
    )

    REFUSAL_REASON: ClassVar[
        Mapping[EvidenceMaterialState, EvidenceRefusalReason | None]
    ] = MappingProxyType(
        {
            EvidenceMaterialState.AVAILABLE: None,
            EvidenceMaterialState.DELETED: EvidenceRefusalReason.MATERIAL_DELETED,
            EvidenceMaterialState.RETENTION_EXPIRED: (
                EvidenceRefusalReason.MATERIAL_RETENTION_EXPIRED
            ),
            EvidenceMaterialState.ACCESS_REVOKED: (
                EvidenceRefusalReason.MATERIAL_ACCESS_REVOKED
            ),
            EvidenceMaterialState.UNAVAILABLE: (
                EvidenceRefusalReason.MATERIAL_UNAVAILABLE
            ),
            EvidenceMaterialState.UNKNOWN: EvidenceRefusalReason.MATERIAL_UNKNOWN,
        }
    )


class EvidenceReadOutcome(StrEnum):
    """Closed outcome of one bounded evidence read."""

    RESOLVED = "resolved"
    REFUSED = "refused"

    @property
    def yields_material(self) -> bool:
        """Return whether this outcome carries readable material."""

        return self is EvidenceReadOutcome.RESOLVED

    def __bool__(self) -> bool:
        raise EvidenceBooleanCoercion


class EvidenceRefIdentity:
    """Derives every opaque control identity F5 binds evidence to.

    The model-facing token is ``evidence-<kind>-<sha256(locator)>``.  Three
    properties come out of that shape:

    * the kind is *inside* the bound body, so routing is covered by the binding
      digest and a reference cannot be re-aimed at another domain's resolver;
    * the locator never appears in a bound reference, a decision, a refusal, or
      a persisted fact, so no host path, URL, or record id is logged because a
      piece of evidence was read; and
    * the encoding is injective -- the kind comes from a closed enum whose
      members contain no ``-``, and the digest is fixed-width -- so no locator
      can be crafted to parse as another kind.

    The subject fingerprint is a canonical-JSON digest over a closed, labelled
    key set for the same separator-injection reason F8 gives, and the ``KIND``
    labels keep an F5 fingerprint from ever equalling an F3/F8/F9/F11 one.
    """

    FEATURE: ClassVar[AgentQualityFeature] = AgentQualityFeature.F5_CONTEXT_BUDGETING
    SUBJECT_KIND: ClassVar[str] = "context.evidence.subject"
    LOCATOR_KIND: ClassVar[str] = "context.evidence.locator"
    REVISION_KIND: ClassVar[str] = "context.evidence.revision"
    SCHEMA_VERSION: ClassVar[int] = 1
    TOKEN_PREFIX: ClassVar[str] = "evidence-"
    TOKEN_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^evidence-(?P<kind>[a-z_]+)-(?P<locator_digest>[0-9a-f]{64})$"
    )

    @classmethod
    def subject_fingerprint(
        cls,
        *,
        kind: EvidenceKind,
        locator: str,
        principal_fingerprint: str,
    ) -> str:
        """Return the stable fingerprint of one evidence subject.

        The subject is the *item*, not the user: one run legitimately holds
        thousands of distinct evidence references under one principal, and
        binding to the principal alone would let any held reference be replayed
        for any other item of the same kind.
        """

        return canonical_json_sha256(
            {
                "kind": cls.SUBJECT_KIND,
                "schema_version": cls.SCHEMA_VERSION,
                "principal_fingerprint": principal_fingerprint,
                "evidence_kind": kind.value,
                "locator_digest": cls.locator_digest(locator),
            }
        )

    @classmethod
    def locator_digest(cls, locator: str) -> str:
        """Return the one-way digest of a source domain's own locator."""

        return canonical_json_sha256(
            {
                "kind": cls.LOCATOR_KIND,
                "schema_version": cls.SCHEMA_VERSION,
                "locator": locator,
            }
        )

    @classmethod
    def bound_revision(cls, value: str) -> BoundRevision:
        """Return the opaque bound form of one source-domain revision."""

        return BoundRevision(
            value=canonical_json_sha256(
                {
                    "kind": cls.REVISION_KIND,
                    "schema_version": cls.SCHEMA_VERSION,
                    "revision": value,
                }
            )
        )

    @classmethod
    def token(cls, *, kind: EvidenceKind, locator: str) -> str:
        """Return the bounded opaque token the model is allowed to name."""

        return f"{cls.TOKEN_PREFIX}{kind.value}-{cls.locator_digest(locator)}"

    @classmethod
    def kind_of(cls, token: str) -> EvidenceKind | None:
        """Return the kind a well-formed token routes to, or ``None``."""

        match = cls.TOKEN_PATTERN.match(token)
        if match is None:
            return None
        try:
            return EvidenceKind(match.group("kind"))
        except ValueError:
            return None

    @classmethod
    def mint(
        cls,
        *,
        scope: RevisionBoundScope,
        kind: EvidenceKind,
        locator: str,
        revision: str,
    ) -> RevisionBoundRef:
        """Bind one evidence item to ``scope`` at ``revision`` reproducibly."""

        return RevisionBoundRef.mint(
            feature=cls.FEATURE,
            opaque_ref=cls.token(kind=kind, locator=locator),
            scope=scope,
            revision=cls.bound_revision(revision),
        )


class EvidenceLifecycleProbe:
    """One source-domain lifecycle question, asked at most once.

    The probe exists because the shared primitive answers with a closed
    four-state authority vocabulary, and F5 needs to keep the state the source
    domain actually reported -- deleted, retention-expired, revoked -- next to
    the primitive's decision.  Making the probe an object rather than a value
    keeps two properties that a pre-resolved answer would each cost:

    * the source is asked **only if the primitive reaches its authority**, so a
      tampered, out-of-feature, or out-of-scope reference is still refused
      structurally without touching a store; and
    * the answer the authority was given is the same answer the refusal
      explains, because there is only ever one.

    It is created per read, never retained by the registry or the authority,
    never digested, and never placed in a reference, a decision, or a fact.  It
    cannot widen anything: admission is decided solely by the primitive's
    decision, and :attr:`observed` is read only to explain a refusal.
    """

    def __init__(
        self,
        resolver: "EvidenceResolverPort",
        *,
        scope: RevisionBoundScope,
        locator: str,
    ) -> None:
        self._resolver = resolver
        self._scope = scope
        self._locator = locator
        self._observed: EvidenceLifecycle | None = None

    async def resolve(self) -> "EvidenceLifecycle":
        """Ask the source domain once, converting every failure to a refusal.

        A resolver may wrap store or network failures.  Internal detail never
        reaches the caller, the model, or an event: an unusable source is
        simply unavailable, and unavailable refuses.
        """

        if self._observed is not None:
            return self._observed
        try:
            lifecycle = await self._resolver.current_lifecycle(
                scope=self._scope,
                locator=self._locator,
            )
        except Exception:
            lifecycle = EvidenceLifecycle.for_state(EvidenceMaterialState.UNAVAILABLE)
        if not isinstance(lifecycle, EvidenceLifecycle):
            lifecycle = EvidenceLifecycle.for_state(EvidenceMaterialState.UNAVAILABLE)
        self._observed = lifecycle
        return lifecycle

    @property
    def observed(self) -> "EvidenceLifecycle | None":
        """Return what the source reported, or ``None`` if never asked."""

        return self._observed


@dataclass(frozen=True, slots=True)
class EvidenceResolutionHandle:
    """The RB.3 resolution handle F5 hands its own authority.

    ``subject_fingerprint`` is one-way by construction, so an authority given
    only a bound scope cannot recover which item to probe, and every adopter
    without a handle is pushed into a scope-keyed side registry populated at
    mint time.  This handle carries the two facts the domain needs -- which
    resolver, and which locator -- plus the probe that asks it.

    It is built by the registry from the run's own grant, never from a model
    request, so a model cannot influence which locator the authority resolves
    for.  It is opaque to the shared primitive, never digested, never stored,
    and never placed in a decision or a fact.
    """

    kind: EvidenceKind
    locator: str
    probe: EvidenceLifecycleProbe


class EvidenceSpan(RuntimeContract):
    """A half-open character span of one evidence source."""

    class Messages:
        """Validation messages owned by this contract."""

        EMPTY_SPAN: ClassVar[str] = "an evidence span must end after it starts"

    start_char: NonNegativeInt = 0
    end_char: PositiveInt

    @model_validator(mode="after")
    def _span_is_non_empty(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError(self.Messages.EMPTY_SPAN)
        return self

    @property
    def length(self) -> int:
        """Return the number of characters this span covers."""

        return self.end_char - self.start_char


class EvidenceSelector(RuntimeContract):
    """Which part of a source a read asks for.

    A selector narrows a read; it can never widen one.  ``locator_hint`` is a
    bounded opaque token a source domain may interpret structurally (a section
    id, an anchor).  It reaches a resolver as untrusted input -- it may have
    come from model output -- so a resolver that does not understand it must
    raise :class:`EvidenceSelectorUnsupported` rather than ignore it.
    """

    span: EvidenceSpan | None = None
    locator_hint: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=_MAX_LOCATOR_LENGTH,
                pattern=_PRINTABLE_ASCII_PATTERN,
            ),
        ]
        | None
    ) = None


class EvidenceReadLimits(RuntimeContract):
    """The hard bounds every read and every batch is evaluated against.

    They are carried as a value rather than left implicit in the registry so a
    caller can assert the bound it is subject to, and so a test can prove that
    the model path cannot receive more than the configured ceiling.
    """

    class Messages:
        """Validation messages owned by this contract."""

        BATCH_BELOW_READ: ClassVar[str] = (
            "an aggregate character budget below the per-read limit would make "
            "the per-read limit unreachable"
        )

    #: Absolute ceilings.  A caller may narrow these; nothing may widen them.
    MAX_CHARS_PER_READ: ClassVar[int] = 64_000
    MAX_REFS_PER_BATCH: ClassVar[int] = 32
    MAX_TOTAL_CHARS: ClassVar[int] = 192_000

    max_chars_per_read: Annotated[int, Field(ge=1, le=MAX_CHARS_PER_READ)] = (
        MAX_CHARS_PER_READ
    )
    max_refs_per_batch: Annotated[int, Field(ge=1, le=MAX_REFS_PER_BATCH)] = (
        MAX_REFS_PER_BATCH
    )
    max_total_chars: Annotated[int, Field(ge=1, le=MAX_TOTAL_CHARS)] = MAX_TOTAL_CHARS

    @model_validator(mode="after")
    def _aggregate_covers_one_read(self) -> Self:
        if self.max_total_chars < self.max_chars_per_read:
            raise ValueError(self.Messages.BATCH_BELOW_READ)
        return self


class EvidenceReadRequest(RuntimeContract):
    """One model-facing ``read_evidence`` request.

    ``token`` is the only thing the model supplies that identifies evidence,
    and it is a bounded opaque value with no interior structure the model can
    exploit: naming a token this run does not hold refuses before any authority
    or resolver is consulted.
    """

    schema_version: Literal[1] = 1
    token: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=_PRINTABLE_ASCII_PATTERN,
        ),
    ]
    selector: EvidenceSelector | None = None
    max_chars: Annotated[
        int,
        Field(ge=1, le=EvidenceReadLimits.MAX_CHARS_PER_READ),
    ] = EvidenceReadLimits.MAX_CHARS_PER_READ


class EvidenceReadBatch(RuntimeContract):
    """An ordered batch of evidence reads evaluated under one aggregate cap."""

    schema_version: Literal[1] = 1
    requests: Annotated[
        tuple[EvidenceReadRequest, ...],
        Field(min_length=1, max_length=EvidenceReadLimits.MAX_REFS_PER_BATCH),
    ]


class EvidenceGrant(RuntimeContract):
    """The run's own record that one evidence item was made visible to it.

    A grant is not an authorization: it records that a token was issued and
    which locator it stands for, so the authority can be *asked*.  Every read
    still runs the full revalidation, the lifecycle probe, and the post-read
    revision check, so a grant can only narrow -- an unheld token refuses
    outright -- and can never admit anything on its own.

    The two validators below make a mis-built grant unrepresentable: a grant
    whose locator does not produce its own token could route one item's read to
    another item's material, which is precisely the confusion the opaque token
    exists to prevent.
    """

    class Messages:
        """Validation messages owned by this contract."""

        WRONG_FEATURE: ClassVar[str] = (
            "an evidence grant must carry a reference minted for evidence recall"
        )
        TOKEN_MISMATCH: ClassVar[str] = (
            "an evidence grant's kind and locator must produce its own token"
        )

    schema_version: Literal[1] = 1
    ref: RevisionBoundRef
    kind: EvidenceKind
    locator: EvidenceLocator

    @model_validator(mode="after")
    def _reference_is_an_evidence_reference(self) -> Self:
        if self.ref.feature is not EvidenceRefIdentity.FEATURE:
            raise ValueError(self.Messages.WRONG_FEATURE)
        return self

    @model_validator(mode="after")
    def _token_matches_its_own_identity(self) -> Self:
        expected = EvidenceRefIdentity.token(kind=self.kind, locator=self.locator)
        if self.ref.opaque_ref != expected:
            raise ValueError(self.Messages.TOKEN_MISMATCH)
        return self

    @classmethod
    def issue(
        cls,
        *,
        scope: RevisionBoundScope,
        kind: EvidenceKind,
        locator: str,
        revision: str,
    ) -> Self:
        """Mint a reference and the grant that records it, together.

        Minting through one path is what keeps the reference and the grant from
        drifting: a call site cannot bind one locator and record another.
        """

        return cls(
            ref=EvidenceRefIdentity.mint(
                scope=scope,
                kind=kind,
                locator=locator,
                revision=revision,
            ),
            kind=kind,
            locator=locator,
        )

    @property
    def token(self) -> str:
        """Return the bounded opaque token the model may name."""

        return self.ref.opaque_ref

    def resolution_handle(
        self,
        resolver: "EvidenceResolverPort",
    ) -> EvidenceResolutionHandle:
        """Return the RB.3 handle this grant hands the domain authority.

        The probe is built here, from the grant's own verified locator, so the
        item the authority resolves for is the item the token was minted for
        and nothing a model supplied can redirect it.
        """

        return EvidenceResolutionHandle(
            kind=self.kind,
            locator=self.locator,
            probe=EvidenceLifecycleProbe(
                resolver,
                scope=self.ref.scope,
                locator=self.locator,
            ),
        )


class EvidenceGrantIndex:
    """The bounded set of evidence tokens one run has made visible.

    It is deliberately not a cache of decisions: it stores what was issued, not
    what was allowed, and holding a grant is never sufficient to read.  It is
    bounded so a run that discovers thousands of sources cannot grow it without
    limit, and re-issuing the same token replaces the entry rather than
    accumulating, because the same item may legitimately be re-minted at a new
    revision within one run.
    """

    MAX_GRANTS: ClassVar[int] = 4_096

    def __init__(self, *, max_grants: int | None = None) -> None:
        self._max_grants = min(max_grants or self.MAX_GRANTS, self.MAX_GRANTS)
        self._grants: dict[str, EvidenceGrant] = {}

    def issue(self, grant: EvidenceGrant) -> EvidenceGrant:
        """Record ``grant`` as visible to this run and return it."""

        if grant.token not in self._grants and len(self._grants) >= self._max_grants:
            raise EvidenceGrantIndexFull(limit=self._max_grants)
        self._grants[grant.token] = grant
        return grant

    def lookup(self, token: str) -> EvidenceGrant | None:
        """Return the grant for ``token``, or ``None`` if this run has none."""

        return self._grants.get(token)

    def revoke(self, token: str) -> None:
        """Stop treating ``token`` as visible to this run.

        Withdrawing visibility is not how deletion or revocation is enforced --
        the source domain's lifecycle is -- but a run that knows a reference is
        gone should stop naming it, and refusing earlier is always narrower.
        """

        self._grants.pop(token, None)

    @property
    def size(self) -> int:
        """Return how many distinct tokens this run currently holds."""

        return len(self._grants)


class EvidenceLifecycle(RuntimeContract):
    """What a source domain says about one locator *now*.

    The revision is mandatory when -- and only when -- the material is
    available, exactly as the primitive's own authority result requires.  A
    domain that has no revision for readable material therefore cannot report
    it as available: it reports ``unknown`` and the read refuses.  That is the
    deliberate answer to material with no natural revision.  Fabricating a
    constant so a probe can succeed would make every such reference look
    permanently fresh, which is the failure this whole path exists to prevent.
    """

    class Messages:
        """Validation messages owned by this contract."""

        AVAILABLE_REQUIRES_REVISION: ClassVar[str] = (
            "available evidence must carry the revision it is currently at"
        )
        REVISION_NOT_PERMITTED: ClassVar[str] = (
            "only available evidence may carry a revision"
        )

    state: EvidenceMaterialState
    revision: EvidenceRevisionValue | None = None

    @model_validator(mode="after")
    def _revision_matches_state(self) -> Self:
        if self.state.is_readable:
            if self.revision is None:
                raise ValueError(self.Messages.AVAILABLE_REQUIRES_REVISION)
            return self
        if self.revision is not None:
            raise ValueError(self.Messages.REVISION_NOT_PERMITTED)
        return self

    @classmethod
    def available(cls, *, revision: str) -> Self:
        """Return a readable lifecycle pinned to ``revision``."""

        return cls(state=EvidenceMaterialState.AVAILABLE, revision=revision)

    @classmethod
    def for_state(cls, state: EvidenceMaterialState) -> Self:
        """Return a non-readable lifecycle, which never admits a read."""

        return cls(state=state)


class EvidenceMaterial(RuntimeContract):
    """The bounded material one resolver produced, with its own lifecycle.

    The lifecycle and revision are carried *with* the bytes on purpose: the
    shared primitive decided currency before the resolver ran, and this is the
    only place that can state what was true when the bytes were actually read.
    """

    class Messages:
        """Validation messages owned by this contract."""

        AVAILABLE_REQUIRES_CONTENT: ClassVar[str] = (
            "available evidence must carry its revision and its content"
        )
        CONTENT_NOT_PERMITTED: ClassVar[str] = (
            "only available evidence may carry content, a span, or a revision"
        )
        INCOMPLETE_REQUIRES_CONTENT: ClassVar[str] = (
            "evidence reported as truncated must carry the characters it kept"
        )

    state: EvidenceMaterialState
    revision: EvidenceRevisionValue | None = None
    content: (
        Annotated[
            str,
            Field(max_length=EvidenceReadLimits.MAX_CHARS_PER_READ),
            Sensitive(SensitiveCategory.MODEL_OUTPUT),
        ]
        | None
    ) = None
    span: EvidenceSpan | None = None
    is_complete: bool = True

    @model_validator(mode="after")
    def _content_matches_state(self) -> Self:
        if self.state.is_readable:
            if self.revision is None or self.content is None:
                raise ValueError(self.Messages.AVAILABLE_REQUIRES_CONTENT)
            if not self.is_complete and not self.content:
                raise ValueError(self.Messages.INCOMPLETE_REQUIRES_CONTENT)
            return self
        if self.revision is not None or self.content is not None or self.span:
            raise ValueError(self.Messages.CONTENT_NOT_PERMITTED)
        return self

    @classmethod
    def available(
        cls,
        *,
        revision: str,
        content: str,
        span: EvidenceSpan | None = None,
        is_complete: bool = True,
    ) -> Self:
        """Return readable material as it was at ``revision``."""

        return cls(
            state=EvidenceMaterialState.AVAILABLE,
            revision=revision,
            content=content,
            span=span,
            is_complete=is_complete,
        )

    @classmethod
    def for_state(cls, state: EvidenceMaterialState) -> Self:
        """Return a non-readable answer, which carries no bytes at all."""

        return cls(state=state)


class ResolvedEvidence(RuntimeContract):
    """Bounded material admitted to the model boundary, with its provenance."""

    class Messages:
        """Validation messages owned by this contract."""

        CONTENT_EXCEEDS_LIMIT: ClassVar[str] = (
            "resolved evidence exceeds the character limit it was read under"
        )
        CHARS_MISMATCH: ClassVar[str] = (
            "resolved evidence must report the number of characters it carries"
        )

    schema_version: Literal[1] = 1
    kind: EvidenceKind
    ref_binding_digest: Sha256Hex
    confirmed_revision: BoundRevision
    content: Annotated[
        str,
        Field(max_length=EvidenceReadLimits.MAX_CHARS_PER_READ),
        Sensitive(SensitiveCategory.MODEL_OUTPUT),
    ]
    content_chars: NonNegativeInt
    content_digest: Sha256Hex
    content_limit_chars: PositiveInt
    span: EvidenceSpan | None = None
    is_complete: bool = True

    @model_validator(mode="after")
    def _content_is_within_its_limit(self) -> Self:
        if self.content_chars != len(self.content):
            raise ValueError(self.Messages.CHARS_MISMATCH)
        if self.content_chars > self.content_limit_chars:
            raise ValueError(self.Messages.CONTENT_EXCEEDS_LIMIT)
        return self


class EvidenceRefusal(RuntimeContract):
    """Why one evidence read produced nothing.  Carries no material.

    ``revalidation_reason`` and ``material_state`` are the two halves of the
    explanation the shared primitive alone cannot give: the first is its own
    closed reason code, the second is the source domain's lifecycle.  Both are
    optional because a refusal may happen before either is consulted.
    """

    class Messages:
        """Validation messages owned by this contract."""

        NOT_CURRENT_REQUIRES_REASON: ClassVar[str] = (
            "a refusal caused by revalidation must carry its revalidation reason"
        )
        REVALIDATION_REASON_NOT_PERMITTED: ClassVar[str] = (
            "only a revalidation refusal may carry a revalidation reason"
        )

    schema_version: Literal[1] = 1
    reason: EvidenceRefusalReason
    kind: EvidenceKind | None = None
    ref_binding_digest: Sha256Hex | None = None
    revalidation_reason: RevalidationReason | None = None
    material_state: EvidenceMaterialState | None = None

    @model_validator(mode="after")
    def _revalidation_reason_is_closed(self) -> Self:
        if self.reason is EvidenceRefusalReason.NOT_CURRENT:
            if self.revalidation_reason is None:
                raise ValueError(self.Messages.NOT_CURRENT_REQUIRES_REASON)
            return self
        if self.revalidation_reason is not None:
            raise ValueError(self.Messages.REVALIDATION_REASON_NOT_PERMITTED)
        return self


class EvidenceReadResult(RuntimeContract):
    """The closed result of one bounded evidence read.

    Like the shared primitive's decision it cannot be coerced to a boolean, so
    a refusal can never be mistaken for retrieved material at a careless call
    site.  Use :attr:`is_resolved` or :meth:`require_resolved`.
    """

    class Messages:
        """Validation messages owned by this contract."""

        RESOLVED_REQUIRES_EVIDENCE: ClassVar[str] = (
            "a resolved read must carry exactly the evidence it resolved"
        )
        REFUSED_REQUIRES_REFUSAL: ClassVar[str] = (
            "a refused read must carry exactly the refusal that caused it"
        )

    schema_version: Literal[1] = 1
    outcome: EvidenceReadOutcome
    resolved: ResolvedEvidence | None = None
    refusal: EvidenceRefusal | None = None

    @model_validator(mode="after")
    def _exactly_one_body(self) -> Self:
        if self.outcome.yields_material:
            if self.resolved is None or self.refusal is not None:
                raise ValueError(self.Messages.RESOLVED_REQUIRES_EVIDENCE)
            return self
        if self.refusal is None or self.resolved is not None:
            raise ValueError(self.Messages.REFUSED_REQUIRES_REFUSAL)
        return self

    @classmethod
    def resolved_as(cls, evidence: ResolvedEvidence) -> Self:
        """Return a resolved result carrying ``evidence``."""

        return cls(outcome=EvidenceReadOutcome.RESOLVED, resolved=evidence)

    @classmethod
    def refused_as(cls, refusal: EvidenceRefusal) -> Self:
        """Return a refused result carrying ``refusal``."""

        return cls(outcome=EvidenceReadOutcome.REFUSED, refusal=refusal)

    @property
    def is_resolved(self) -> bool:
        """Return whether this read produced readable material."""

        return self.outcome.yields_material

    @property
    def content_chars(self) -> int:
        """Return how many characters this read admitted to the model."""

        return self.resolved.content_chars if self.resolved is not None else 0

    def require_resolved(self) -> ResolvedEvidence:
        """Return the resolved evidence or raise the typed refusal error."""

        if self.resolved is None:
            reason = (
                self.refusal.reason
                if self.refusal is not None
                else EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION
            )
            raise EvidenceNotResolved(reason=reason)
        return self.resolved

    def __bool__(self) -> bool:
        raise EvidenceBooleanCoercion


class EvidenceReadBatchResult(RuntimeContract):
    """One result per request, in request order, under one aggregate cap."""

    schema_version: Literal[1] = 1
    results: tuple[EvidenceReadResult, ...]

    @property
    def total_chars(self) -> int:
        """Return the total characters this batch admitted to the model."""

        return sum(result.content_chars for result in self.results)

    @property
    def resolved_count(self) -> int:
        """Return how many reads produced material."""

        return sum(1 for result in self.results if result.is_resolved)

    @property
    def refused_count(self) -> int:
        """Return how many reads refused."""

        return len(self.results) - self.resolved_count


class EvidenceReadFact(RuntimeContract):
    """The raw-free fact one evidence read contributes to the run journal.

    It carries identifiers, digests, counts, and reason codes only.  The
    content digest is what lets a later groundedness or citation check tie an
    answer to the exact bytes that were read, without the journal ever holding
    those bytes.
    """

    class Messages:
        """Validation messages owned by this contract."""

        RESOLVED_REQUIRES_DIGEST: ClassVar[str] = (
            "a resolved evidence fact must carry the digest of what it admitted"
        )
        REFUSED_REQUIRES_REASON: ClassVar[str] = (
            "a refused evidence fact must carry its refusal reason"
        )

    schema_version: Literal[1] = 1
    feature: AgentQualityFeature = AgentQualityFeature.F5_CONTEXT_BUDGETING
    outcome: EvidenceReadOutcome
    kind: EvidenceKind | None = None
    ref_binding_digest: Sha256Hex | None = None
    content_digest: Sha256Hex | None = None
    content_chars: NonNegativeInt = 0
    is_complete: bool | None = None
    reason: EvidenceRefusalReason | None = None
    revalidation_reason: RevalidationReason | None = None
    material_state: EvidenceMaterialState | None = None

    @model_validator(mode="after")
    def _fact_matches_its_outcome(self) -> Self:
        if self.outcome.yields_material:
            if self.content_digest is None or self.reason is not None:
                raise ValueError(self.Messages.RESOLVED_REQUIRES_DIGEST)
            return self
        if self.reason is None or self.content_digest is not None:
            raise ValueError(self.Messages.REFUSED_REQUIRES_REASON)
        return self

    @classmethod
    def from_result(cls, result: EvidenceReadResult) -> Self:
        """Project one read result into its body-free journal fact."""

        if result.resolved is not None:
            evidence = result.resolved
            return cls(
                outcome=result.outcome,
                kind=evidence.kind,
                ref_binding_digest=evidence.ref_binding_digest,
                content_digest=evidence.content_digest,
                content_chars=evidence.content_chars,
                is_complete=evidence.is_complete,
            )
        refusal = result.refusal
        if refusal is None:  # pragma: no cover - unrepresentable by validation
            raise EvidenceNotResolved(
                reason=EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION
            )
        return cls(
            outcome=result.outcome,
            kind=refusal.kind,
            ref_binding_digest=refusal.ref_binding_digest,
            reason=refusal.reason,
            revalidation_reason=refusal.revalidation_reason,
            material_state=refusal.material_state,
        )


@runtime_checkable
class EvidenceResolverPort(Protocol):
    """The two questions every source domain answers for its own evidence.

    Implementations reauthorize the current subject and apply their own
    retention and deletion state.  They never compare revisions, never widen a
    scope, and never decide whether a reference is current: that belongs to the
    shared revalidator.

    ``current_lifecycle`` must be answerable without producing the material, so
    the authority probe cannot become a second unbounded read.
    """

    @property
    def kind(self) -> EvidenceKind:
        """Return the single evidence kind this resolver answers for."""

    async def current_lifecycle(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
    ) -> EvidenceLifecycle: ...

    async def read_material(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
        selector: EvidenceSelector | None,
        max_chars: int,
    ) -> EvidenceMaterial: ...


class EvidenceResolverDirectory:
    """The closed kind-to-resolver routing table, fixed at construction.

    Registration is closed on purpose: a directory that could gain a resolver
    mid-run would let the set of readable evidence kinds widen inside a run,
    which is exactly the authority widening §6.1 forbids.
    """

    def __init__(self, resolvers: Sequence[EvidenceResolverPort] = ()) -> None:
        registered: dict[EvidenceKind, EvidenceResolverPort] = {}
        for resolver in resolvers:
            kind = resolver.kind
            if kind in registered:
                raise EvidenceResolverAlreadyRegistered(kind=kind)
            registered[kind] = resolver
        self._resolvers: Mapping[EvidenceKind, EvidenceResolverPort] = MappingProxyType(
            registered
        )

    def lookup(self, kind: EvidenceKind) -> EvidenceResolverPort | None:
        """Return the resolver registered for ``kind``, or ``None``."""

        return self._resolvers.get(kind)

    @property
    def kinds(self) -> frozenset[EvidenceKind]:
        """Return every kind this directory can route."""

        return frozenset(self._resolvers)


class EvidenceRevisionAuthority:
    """What the source domains say is current *now* for one bound scope.

    It answers and never compares: revision equality, scope narrowing, and
    binding integrity all belong to the shared revalidator.  It introduces no
    authority of its own either -- the only revision it reports is one the
    registered source domain resolved for a locator that domain already
    governs, so it is a projection of existing source authority rather than a
    second one.

    It holds no state whatsoever, not even a reachability flag.  A source that
    cannot be reached is a lifecycle answer, not a property of this class, so
    there is nothing here that a test could set and production could inherit.
    Without a handle it cannot know which item to ask about -- the subject
    fingerprint is one-way -- and answers ``unknown`` rather than guessing, so
    a call presenting no handle refuses instead of resolving to something
    stale.
    """

    FEATURE: ClassVar[AgentQualityFeature] = AgentQualityFeature.F5_CONTEXT_BUDGETING

    async def current_revision(
        self,
        *,
        feature: AgentQualityFeature,
        scope: RevisionBoundScope,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevisionAuthorityResult:
        """Return what is authoritative now for ``scope``, or why it is not."""

        if feature is not self.FEATURE:
            # Unreachable through the revalidator, which refuses a feature
            # mismatch first.  Answering ``unknown`` keeps this adapter
            # fail-closed if it is ever consulted directly.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        if not isinstance(resolution_handle, EvidenceResolutionHandle):
            # No handle, or one belonging to another domain: this authority has
            # nothing to resolve from and must not guess.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        lifecycle = await resolution_handle.probe.resolve()
        if not lifecycle.state.is_readable or lifecycle.revision is None:
            return RevisionAuthorityResult(state=lifecycle.state.authority_state)
        return RevisionAuthorityResult(
            state=RevisionAuthorityState.ACTIVE,
            current_revision=EvidenceRefIdentity.bound_revision(lifecycle.revision),
        )


class EvidenceResolverRegistry:
    """The one call-time path from an opaque evidence token to bounded bytes.

    The registry is stateless by construction -- a directory, a revalidator,
    and limits -- so there is nowhere for an authorization decision to be
    cached even accidentally.  Every read walks the same order, narrowest and
    cheapest first:

    1. is this token one this run holds at all;
    2. is there a registered resolver for the kind bound into it;
    3. does the shared primitive still report the reference as current;
    4. does the source domain still report the material as readable;
    5. is what it read still at the revision the authority confirmed; and
    6. is what it read within the character bound it was asked for.

    Steps 3 and 5 are two different questions.  The primitive answers *was the
    reference current*; only the resolver's own report can answer *were these
    exact bytes still current when they were produced*.  Refusing on either is
    what makes deletion during a read a refusal rather than stale material.
    """

    POLICY: ClassVar[RevalidationPolicy] = RevalidationPolicy(
        # Evidence is always read inside a run, so binding the run is free and
        # refusing a reference that omits it is strictly narrower than allowing
        # a run-less evidence reference to be replayed anywhere.
        feature=AgentQualityFeature.F5_CONTEXT_BUDGETING,
        required_dimensions=frozenset(
            {RevisionScopeDimension.SUBJECT, RevisionScopeDimension.RUN}
        ),
    )

    def __init__(
        self,
        resolvers: Sequence[EvidenceResolverPort] = (),
        *,
        limits: EvidenceReadLimits | None = None,
        revalidator: RevisionRevalidatorPort | None = None,
    ) -> None:
        self._directory = EvidenceResolverDirectory(resolvers)
        self._authority = EvidenceRevisionAuthority()
        self._revalidator = revalidator or RevisionBindingRevalidator(self._authority)
        self._limits = limits or EvidenceReadLimits()

    @property
    def limits(self) -> EvidenceReadLimits:
        """Return the bounds every read through this registry obeys."""

        return self._limits

    @property
    def directory(self) -> EvidenceResolverDirectory:
        """Return the closed routing table this registry dispatches through."""

        return self._directory

    @property
    def authority(self) -> EvidenceRevisionAuthority:
        """Return the projection the shared revalidator resolves against."""

        return self._authority

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        """Return the shared revalidator this registry binds through."""

        return self._revalidator

    async def read_evidence(
        self,
        batch: EvidenceReadBatch,
        *,
        runtime_context: RevisionUseContext,
        grants: EvidenceGrantIndex,
    ) -> EvidenceReadBatchResult:
        """Read a bounded batch of evidence, one result per request in order.

        The aggregate character budget is consumed in request order, so the
        outcome of a batch is a deterministic function of its requests and the
        source state -- no ordering is decided by timing.

        A request whose own declared bound cannot fit in what remains is
        refused *before* the source is consulted, rather than being attempted
        under a silently narrowed budget.  Two things follow, and both matter:
        the refusal names the batch as the cause instead of blaming a resolver
        for exceeding a limit it was never told about, and a batch cannot turn
        one oversized request into a source read whose result is then thrown
        away.  A caller that wants more reads in one batch declares tighter
        per-read bounds, which is the correct incentive.
        """

        results: list[EvidenceReadResult] = []
        remaining = self._limits.max_total_chars
        for position, request in enumerate(batch.requests):
            budget = min(request.max_chars, self._limits.max_chars_per_read)
            if position >= self._limits.max_refs_per_batch or budget > remaining:
                results.append(
                    self._refuse(EvidenceRefusalReason.BATCH_LIMIT_EXHAUSTED)
                )
                continue
            result = await self.read_one(
                request,
                runtime_context=runtime_context,
                grants=grants,
                max_chars=budget,
            )
            remaining -= result.content_chars
            results.append(result)
        return EvidenceReadBatchResult(results=tuple(results))

    async def read_one(
        self,
        request: EvidenceReadRequest,
        *,
        runtime_context: RevisionUseContext,
        grants: EvidenceGrantIndex,
        max_chars: int | None = None,
    ) -> EvidenceReadResult:
        """Resolve one evidence token into bounded material or a refusal."""

        budget = min(
            max_chars if max_chars is not None else request.max_chars,
            request.max_chars,
            self._limits.max_chars_per_read,
        )
        grant = grants.lookup(request.token)
        if grant is None:
            # A token this run never issued refuses before any authority or
            # resolver is consulted, and without echoing which kind it parsed
            # as -- guessing a locator must reveal nothing.
            return self._refuse(EvidenceRefusalReason.UNKNOWN_REFERENCE)
        resolver = self._directory.lookup(grant.kind)
        if resolver is None:
            return self._refuse(
                EvidenceRefusalReason.UNREGISTERED_KIND,
                grant=grant,
            )
        handle = grant.resolution_handle(resolver)
        decision = await self._revalidate(grant, runtime_context, handle)
        if not decision.is_current or decision.current_revision is None:
            observed = handle.probe.observed
            return self._refuse(
                EvidenceRefusalReason.NOT_CURRENT,
                grant=grant,
                revalidation_reason=decision.reason,
                # What the source actually said, kept alongside the primitive's
                # closed reason code.  Deletion, retention expiry, and an
                # unissued reference are one outcome there and three different
                # answers to a retention question here.
                material_state=observed.state if observed is not None else None,
            )
        material = await self._read_material(
            resolver,
            grant=grant,
            selector=request.selector,
            max_chars=budget,
        )
        if isinstance(material, EvidenceRefusalReason):
            return self._refuse(material, grant=grant)
        return self._admit(
            material,
            grant=grant,
            confirmed_revision=decision.current_revision,
            budget=budget,
        )

    async def _revalidate(
        self,
        grant: EvidenceGrant,
        runtime_context: RevisionUseContext,
        handle: EvidenceResolutionHandle,
    ) -> RevalidationDecision:
        """Re-resolve one grant through the shared primitive at use time.

        Nothing about this call is memoized across calls.  The handle and its
        probe are built per read, so reading the same reference twice asks the
        source twice: a decision that survived its own call is exactly the
        authorization cache §6.1 forbids.
        """

        return await self._revalidator.revalidate_at_use(
            grant.ref,
            runtime_context,
            self.POLICY,
            resolution_handle=handle,
        )

    async def _read_material(
        self,
        resolver: EvidenceResolverPort,
        *,
        grant: EvidenceGrant,
        selector: EvidenceSelector | None,
        max_chars: int,
    ) -> EvidenceMaterial | EvidenceRefusalReason:
        """Ask one resolver for at most ``max_chars``, converting failures.

        A resolver that raises, returns the wrong type, or returns more than it
        was asked for produces a refusal.  The type check is what bounds the
        model path structurally: an oversized string cannot pass
        :class:`EvidenceMaterial` validation, so it can never be admitted even
        if a resolver ignores its budget.
        """

        try:
            material = await resolver.read_material(
                scope=grant.ref.scope,
                locator=grant.locator,
                selector=selector,
                max_chars=max_chars,
            )
        except EvidenceSelectorUnsupported:
            return EvidenceRefusalReason.SELECTOR_UNSUPPORTED
        except Exception:
            return EvidenceRefusalReason.RESOLVER_ERROR
        if not isinstance(material, EvidenceMaterial):
            return EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION
        return material

    def _admit(
        self,
        material: EvidenceMaterial,
        *,
        grant: EvidenceGrant,
        confirmed_revision: BoundRevision,
        budget: int,
    ) -> EvidenceReadResult:
        """Apply the post-read fences and build the admitted result."""

        if not material.state.is_readable or material.content is None:
            reason = (
                material.state.refusal_reason
                or EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION
            )
            return self._refuse(reason, grant=grant, material_state=material.state)
        if material.revision is None:  # pragma: no cover - blocked by validation
            return self._refuse(
                EvidenceRefusalReason.RESOLVER_CONTRACT_VIOLATION,
                grant=grant,
            )
        read_revision = EvidenceRefIdentity.bound_revision(material.revision)
        if read_revision != confirmed_revision:
            # The reference was current when the authority answered and the
            # bytes came back at a different revision: the material moved,
            # was replaced, or was deleted and re-created during the read.
            return self._refuse(
                EvidenceRefusalReason.MATERIAL_SUPERSEDED,
                grant=grant,
                material_state=material.state,
            )
        if len(material.content) > budget:
            return self._refuse(
                EvidenceRefusalReason.READ_LIMIT_EXCEEDED,
                grant=grant,
                material_state=material.state,
            )
        return EvidenceReadResult.resolved_as(
            ResolvedEvidence(
                kind=grant.kind,
                ref_binding_digest=grant.ref.binding_digest,
                confirmed_revision=confirmed_revision,
                content=material.content,
                content_chars=len(material.content),
                content_digest=sha256(material.content.encode("utf-8")).hexdigest(),
                content_limit_chars=budget,
                span=material.span,
                is_complete=material.is_complete,
            )
        )

    def _refuse(
        self,
        reason: EvidenceRefusalReason,
        *,
        grant: EvidenceGrant | None = None,
        revalidation_reason: RevalidationReason | None = None,
        material_state: EvidenceMaterialState | None = None,
    ) -> EvidenceReadResult:
        """Build one body-free refusal with the narrowest safe explanation."""

        return EvidenceReadResult.refused_as(
            EvidenceRefusal(
                reason=reason,
                kind=grant.kind if grant is not None else None,
                ref_binding_digest=(
                    grant.ref.binding_digest if grant is not None else None
                ),
                revalidation_reason=revalidation_reason,
                material_state=material_state,
            )
        )


__all__ = [
    "EvidenceBooleanCoercion",
    "EvidenceGrant",
    "EvidenceGrantIndex",
    "EvidenceGrantIndexFull",
    "EvidenceKind",
    "EvidenceLifecycle",
    "EvidenceLifecycleProbe",
    "EvidenceLifecycleTables",
    "EvidenceLocator",
    "EvidenceMaterial",
    "EvidenceMaterialState",
    "EvidenceNotResolved",
    "EvidenceReadBatch",
    "EvidenceReadBatchResult",
    "EvidenceReadFact",
    "EvidenceReadLimits",
    "EvidenceReadOutcome",
    "EvidenceReadRequest",
    "EvidenceReadResult",
    "EvidenceRefIdentity",
    "EvidenceRefusal",
    "EvidenceRefusalReason",
    "EvidenceRegistryError",
    "EvidenceResolutionHandle",
    "EvidenceResolverAlreadyRegistered",
    "EvidenceResolverDirectory",
    "EvidenceResolverPort",
    "EvidenceResolverRegistry",
    "EvidenceRevisionAuthority",
    "EvidenceRevisionValue",
    "EvidenceSelector",
    "EvidenceSelectorUnsupported",
    "EvidenceSpan",
    "ResolvedEvidence",
]
