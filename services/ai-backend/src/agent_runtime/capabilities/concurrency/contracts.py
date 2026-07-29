"""Pure domain contracts for deterministic, conservative capability concurrency.

This is the single vocabulary the whole F6 domain shares: the descriptor
precedence resolver, the batch planner, the scoped permit table, and the serial
kill switches all narrow *these* types. They were built as three isolated lanes
and each defined its own local copy of the same idea; those copies are collapsed
here so a change to the authority model is one edit, not three.

Every concurrency-relevant vocabulary is a :class:`NarrowableEnum` whose members
are declared **narrowest first**. Declaration order *is* the authority rank, so
the conservative member is the structural default rather than a documented
convention, and the only composition operator exposed anywhere in F6 is
:meth:`NarrowableEnum.narrowest` — a minimum over that rank. There is no operator
that can widen a resolved policy, which is what makes the precedence chain in
:mod:`agent_runtime.capabilities.concurrency.descriptor_policy` structurally safe
rather than merely careful.

Two things deliberately did **not** collapse:

- :class:`agent_runtime.capabilities.concurrency.kill_switches.ConcurrencyKillSwitchScope`
  stays its own three-member vocabulary. See its docstring for why smallness is
  the safety property there.
- :class:`ConcurrencyPolicy.max_parallelism` stays a bare optional ``int``,
  because it is the one non-safety field: it is a scheduling bound a capability
  may declare, not authority to overlap. Authority is
  :class:`ConcurrencyAllowance`, and it is what a batch, a segment, and a kill
  switch all speak.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
import hashlib
import hmac
import re
from typing import ClassVar, Final, Self

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.concurrency.errors import (
    ConcurrencyRejectionReason,
    ResourceKeyRenderRejected,
    ResourceKeyTemplateRejected,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)


class ConcurrencyBounds:
    """The one parallelism ceiling every F6 contract is bound by.

    Before this class existed the pair ``(1, 16)`` was restated at five
    independent sites — the declared policy bound, the batch ceiling, the
    segment ceiling, the permit capacity, and the requested permit width — so
    raising the ceiling meant finding all five. Every site now imports these two
    names, and changing the ceiling is exactly one edit.

    ``SERIAL_PARALLELISM`` is not merely the lower bound: it is the value every
    conservative fallback in F6 resolves to, so an unknown, unparseable, or
    unreadable input lands on it by construction.
    """

    SERIAL_PARALLELISM: Final[int] = 1
    MAX_PARALLELISM: Final[int] = 16


class NarrowableEnum(StrEnum):
    """Closed vocabulary ordered from most conservative to least conservative.

    Members must be declared narrowest first. A member added at the top of the
    declaration becomes the new conservative floor automatically; a member added
    at the bottom can never silently become the default.
    """

    @property
    def rank(self) -> int:
        """Return the authority rank, where ``0`` is the narrowest member."""

        return list(type(self)).index(self)

    @classmethod
    def conservative(cls) -> Self:
        """Return the narrowest member, used for unknown and absent metadata."""

        return next(iter(cls))

    @classmethod
    def narrowest(cls, *values: Self) -> Self:
        """Return the narrowest supplied member.

        This is the only composition operator for concurrency vocabularies. It
        cannot return a value wider than any of its inputs.
        """

        if not values:
            raise ValueError("at least one value is required")
        return min(values, key=lambda value: value.rank)


class ConcurrencyMode(NarrowableEnum):
    """Concurrency posture declared by trusted capability metadata."""

    SERIAL = "serial"
    SAME_SUBJECT_SERIAL = "same_subject_serial"
    PARALLEL_SAFE = "parallel_safe"


class SideEffectKind(NarrowableEnum):
    """Side-effect class relevant to concurrent admission.

    ``UNKNOWN`` is narrower than an irreversible write: an undeclared effect
    class cannot be reasoned about at all, so it forbids every overlap.
    """

    UNKNOWN = "unknown"
    IRREVERSIBLE_WRITE = "irreversible_write"
    REVERSIBLE_WRITE = "reversible_write"
    READ = "read"
    NONE = "none"


class IdempotencyKind(NarrowableEnum):
    """Idempotency posture declared by the capability owner."""

    NONE = "none"
    KEYED = "keyed"
    NATURAL = "natural"


class ConcurrencyScope(NarrowableEnum):
    """Scope at which an executor must acquire a permit.

    One vocabulary serves both halves of F6. A capability *declares* the scope
    its rate limit applies at (``ConcurrencyPolicy.rate_limit_scope``), and the
    permit table *bounds* capacity at that same scope
    (:class:`PermitCapacity`). Those were two enums with two orderings until
    they were folded here; a declared scope and an enforced scope are now the
    same value, so a rate limit cannot be declared at a scope the permit table
    has no pool for.

    A broader scope shares one permit pool across more work, so it admits less
    concurrency and is therefore narrower. ``UNKNOWN`` is narrower still: an
    undeclared scope must acquire at the broadest available pool, which
    :meth:`permit_pool` resolves to ``GLOBAL``.

    ``PROFILE`` bounds one deployment profile. Every scope narrower than
    ``PROFILE`` is additionally qualified by the verified subject, so one
    subject can never consume another subject's capacity.

    ``UNKNOWN`` is a declarable posture but never a permit identity: it names no
    pool. :class:`PermitScope` refuses it, so the fail-closed answer is always
    :meth:`permit_pool`'s broadest pool rather than an unbounded one.
    """

    UNKNOWN = "unknown"
    GLOBAL = "global"
    PROFILE = "profile"
    INSTALLATION = "installation"
    USER = "user"
    CONNECTOR = "connector"
    CAPABILITY = "capability"

    def permit_pool(self) -> ConcurrencyScope:
        """Return the scope a permit for this declaration is actually taken at.

        An undeclared scope resolves to ``GLOBAL``: the broadest pool shares one
        permit across the most work and therefore admits the least concurrency,
        which is the conservative reading of "we do not know what this rate
        limit applies to". Every declared scope is its own pool.
        """

        return ConcurrencyScope.GLOBAL if self is ConcurrencyScope.UNKNOWN else self

    @classmethod
    def permit_pool_kinds(cls) -> tuple[ConcurrencyScope, ...]:
        """Return every scope that can identify a permit pool, broadest first."""

        return tuple(scope for scope in cls if scope is not cls.UNKNOWN)


class OrderingRequirement(NarrowableEnum):
    """Result ordering required by a capability."""

    INPUT_ORDER = "input_order"
    COMPLETION_ORDER = "completion_order"
    NONE = "none"


class ApprovalRequirement(NarrowableEnum):
    """Whether one capability's execution can pause for a human decision.

    This dimension exists because the two authorities that decide it never met.
    A ``PRODUCT_CATALOG`` entry declares an effect class and a concurrency mode;
    whether a dispatch of that same capability *parks* is decided elsewhere
    entirely — by the run's tool-use policy, by the connector's live auth state,
    or by a filesystem permission rule — none of which the catalog author can
    see. Nothing linked the two, so a capability declared ``READ`` and
    ``PARALLEL_SAFE`` was admitted to a parallel segment even when every one of
    its siblings would park on a human. That is what this vocabulary closes.

    An approval is not a failure and not a resource conflict: it is a suspend.
    Suspending N members of one admitted cohort at once opens N simultaneous
    human decisions where the serial path opens one, and every one of those
    parks is a durable child transition the coordinator settles as ``FAILED``,
    because a suspend arrives at it as an exception. Neither consequence is
    something a concurrency plan may cause silently, so overlap requires this
    fact to be positively established.

    Ordered narrowest first, like every other F6 vocabulary. ``UNKNOWN`` is the
    floor and therefore the default: a capability nobody has said anything about
    is one whose dispatch might park, and ``NEVER`` — the only member that
    permits overlap — is a claim somebody has to make.
    """

    UNKNOWN = "unknown"
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    NEVER = "never"

    @property
    def may_park(self) -> bool:
        """Return whether a dispatch of this capability might suspend.

        True for everything except ``NEVER``, which is the load-bearing
        asymmetry: this reads "we have not established that it cannot park",
        not "we have established that it can".
        """

        return self is not ApprovalRequirement.NEVER


class ProviderSessionConstraint(NarrowableEnum):
    """Serialization the provider's own session or transport state demands.

    A connector or MCP server with non-thread-safe session state cannot be
    driven concurrently even when every other dimension permits it.
    ``UNKNOWN`` forbids overlap entirely because the session scope itself is
    undeclared.
    """

    UNKNOWN = "unknown"
    INSTALLATION_SERIAL = "installation_serial"
    SESSION_SERIAL = "session_serial"
    SESSION_PARALLEL_SAFE = "session_parallel_safe"


class PolicySource(NarrowableEnum):
    """Trust source for concurrency metadata.

    Rank doubles as precedence. The most authoritative source
    (``PRODUCT_CATALOG``) establishes the policy; every less authoritative
    source is applied afterwards in descending rank order and may only narrow.
    """

    CONSERVATIVE_DEFAULT = "conservative_default"
    TRUSTED_PROVIDER = "trusted_provider"
    USER_APPROVED_OVERRIDE = "user_approved_override"
    PRODUCT_CATALOG = "product_catalog"


class ConcurrencyPolicyField(StrEnum):
    """Closed field vocabulary for content-free policy decision records.

    Values match :class:`ConcurrencyPolicy` attribute names so a record is
    self-describing without carrying any declared value.
    """

    MODE = "mode"
    SIDE_EFFECT = "side_effect"
    IDEMPOTENCY = "idempotency"
    RESOURCE_KEY_TEMPLATE = "resource_key_template"
    MAX_PARALLELISM = "max_parallelism"
    RATE_LIMIT_SCOPE = "rate_limit_scope"
    ORDERING_REQUIREMENT = "ordering_requirement"
    PROVIDER_SESSION_CONSTRAINT = "provider_session_constraint"


class ConcurrencyAllowance(RuntimeContract):
    """Monotonically narrowable authority to overlap capability work.

    This is the general F6 authority value, not a kill-switch detail. A run
    snapshot, an :class:`OperationBatch`, a :class:`BatchSegment`, and a live
    kill switch all express their ceiling as one of these, so a kill switch
    narrows a batch through a single type rather than through two parallel ones
    that could drift apart.

    ``mode`` is the F6 posture and ``max_parallelism`` the ceiling. Only
    ``enforce`` lets F6 own execution; ``off`` and ``shadow`` both use the F6
    safe fallback, which the Step-0 policy map defines as serial. Both bounds
    must be satisfied at once, which is why width alone can never authorize
    overlap.
    """

    mode: FeatureMode = FeatureMode.OFF
    max_parallelism: int = Field(
        default=ConcurrencyBounds.SERIAL_PARALLELISM,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )

    @classmethod
    def serial(cls) -> Self:
        """Return the narrowest possible allowance."""

        return cls(
            mode=FeatureMode.OFF,
            max_parallelism=ConcurrencyBounds.SERIAL_PARALLELISM,
        )

    @classmethod
    def enforcing(cls, max_parallelism: int) -> Self:
        """Return the allowance a bare declared ceiling has always meant.

        A bare integer ceiling states "this width is authorized", which is
        exactly what :class:`OperationBatch` and :class:`BatchSegment` meant by
        an ``int`` before this type existed. Callers holding a real run
        allowance — one already narrowed by the kill-switch gate — must pass it
        directly instead, because this constructor asserts ``enforce``.
        """

        return cls(mode=FeatureMode.ENFORCE, max_parallelism=max_parallelism)

    @classmethod
    def coerce(cls, value: object) -> object:
        """Return ``value`` as an allowance, accepting a bare declared ceiling."""

        if isinstance(value, int) and not isinstance(value, bool):
            return cls.enforcing(value)
        return value

    @property
    def permits_parallel(self) -> bool:
        """Return whether F6 may actually overlap work."""

        return (
            self.mode is FeatureMode.ENFORCE
            and self.max_parallelism > ConcurrencyBounds.SERIAL_PARALLELISM
        )

    @property
    def is_serial(self) -> bool:
        """Return whether work must run one operation at a time."""

        return not self.permits_parallel

    @property
    def effective_max_parallelism(self) -> int:
        """Return the width a scheduler may actually use."""

        if self.permits_parallel:
            return self.max_parallelism
        return ConcurrencyBounds.SERIAL_PARALLELISM

    def narrowed_by(self, other: ConcurrencyAllowance) -> ConcurrencyAllowance:
        """Return the narrowest of two allowances.

        This is the only composition F6 performs on authority. ``min`` and
        :meth:`FeatureMode.least_authoritative` are each idempotent,
        commutative, and associative, so the result cannot depend on evaluation
        order and cannot exceed either input.
        """

        return ConcurrencyAllowance(
            mode=FeatureMode.least_authoritative(self.mode, other.mode),
            max_parallelism=min(self.max_parallelism, other.max_parallelism),
        )

    def narrowed_to_serial(self) -> ConcurrencyAllowance:
        """Return this allowance forced to serial by an emergency control."""

        return self.narrowed_by(ConcurrencyAllowance.serial())


class ResourceKeyDimension(StrEnum):
    """Closed subject dimensions a resource-key template may address."""

    CONNECTOR = "connector"
    INSTALLATION = "installation"
    SESSION = "session"
    ACCOUNT = "account"
    SUBJECT = "subject"
    CONTAINER = "container"
    OBJECT = "object"
    REGION = "region"


class ResourceKeyTemplate(RuntimeContract):
    """Closed template that renders one keyed, digested resource scope key.

    The template stores dimension *names* only. Raw connector paths, object
    identifiers, account identifiers, and user content are supplied at render
    time and leave this module solely as an ``hmac-sha256:<64 hex>`` digest, in
    the exact shape :class:`BatchOperation` accepts.

    Templates form their own narrowing lattice. A template addressing a strict
    subset of another's dimensions produces coarser keys, collides more often,
    and therefore serializes more; it is narrower. Absence is the bottom
    element, because without a key the planner cannot establish independence at
    all and falls back to a serial segment.
    """

    class Limits:
        MAX_DIMENSIONS = 8
        MAX_TEMPLATE_LENGTH = 256
        MAX_VALUE_LENGTH = 512
        MIN_SECRET_BYTES = 32

    class Digest:
        ALGORITHM = "hmac-sha256"
        PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")

    class Keys:
        VERSION = "version"
        TEMPLATE = "template"
        VALUES = "values"

    SCHEMA_VERSION: ClassVar[int] = 1
    _PLACEHOLDER: ClassVar[re.Pattern[str]] = re.compile(r"^\{([a-z_]+)\}$")
    _SEPARATOR: ClassVar[str] = "/"

    dimensions: tuple[ResourceKeyDimension, ...] = Field(
        min_length=1,
        max_length=Limits.MAX_DIMENSIONS,
    )

    @model_validator(mode="after")
    def _dimensions_are_unique(self) -> Self:
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        return self

    @classmethod
    def from_template(cls, template: str) -> Self:
        """Parse ``{connector}/{object}`` into a closed dimension list.

        Raises :class:`ResourceKeyTemplateRejected` for anything else. Use
        :meth:`parse` on the untrusted path, where failure must mean "no key"
        rather than an exception.
        """

        if not isinstance(template, str):
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        normalized = template.strip()
        if not normalized or len(normalized) > cls.Limits.MAX_TEMPLATE_LENGTH:
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        dimensions: list[ResourceKeyDimension] = []
        for part in normalized.split(cls._SEPARATOR):
            match = cls._PLACEHOLDER.fullmatch(part)
            if match is None:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                )
            try:
                dimension = ResourceKeyDimension(match.group(1))
            except ValueError as exc:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                ) from exc
            if dimension in dimensions:
                raise ResourceKeyTemplateRejected(
                    ConcurrencyRejectionReason.MALFORMED_TEMPLATE
                )
            dimensions.append(dimension)
        if len(dimensions) > cls.Limits.MAX_DIMENSIONS:
            raise ResourceKeyTemplateRejected(
                ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            )
        return cls(dimensions=tuple(dimensions))

    @classmethod
    def parse(cls, value: object) -> Self | None:
        """Return a template for trusted-shaped input, or ``None`` for anything else."""

        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        try:
            return cls.from_template(value)
        except ResourceKeyTemplateRejected:
            return None

    @classmethod
    def narrowest(cls, *templates: Self | None) -> Self | None:
        """Return the narrowest template, or ``None`` when none can be proven.

        Absence wins over any template, a strict dimension subset wins over its
        superset, and two templates that cannot be ordered fall to ``None``
        rather than to an invented merge.
        """

        if not templates:
            raise ValueError("at least one template is required")
        narrowed = templates[0]
        for candidate in templates[1:]:
            narrowed = cls._narrow_pair(narrowed, candidate)
        return narrowed

    @classmethod
    def _narrow_pair(cls, left: Self | None, right: Self | None) -> Self | None:
        if left is None or right is None:
            return None
        if left == right:
            return left
        left_dimensions = frozenset(left.dimensions)
        right_dimensions = frozenset(right.dimensions)
        if right_dimensions < left_dimensions:
            return right
        if left_dimensions < right_dimensions:
            return left
        return None

    @property
    def canonical_template(self) -> str:
        """Return the stable ``{a}/{b}`` form used as digest domain separation."""

        return self._SEPARATOR.join(
            f"{{{dimension.value}}}" for dimension in self.dimensions
        )

    def render(
        self,
        *,
        secret: bytes,
        values: Mapping[ResourceKeyDimension, str],
    ) -> str:
        """Return one keyed digest for this template's dimension values.

        The returned digest is the only thing derived from ``values`` that
        leaves this module. Raises :class:`ResourceKeyRenderRejected` when the
        key material is incomplete, unexpected, oversized, or unkeyed.
        """

        if not isinstance(secret, (bytes, bytearray)) or (
            len(secret) < self.Limits.MIN_SECRET_BYTES
        ):
            raise ResourceKeyRenderRejected(
                ConcurrencyRejectionReason.WEAK_DIGEST_SECRET
            )
        supplied = set(values)
        expected = set(self.dimensions)
        if supplied - expected:
            raise ResourceKeyRenderRejected(
                ConcurrencyRejectionReason.UNEXPECTED_DIMENSION_VALUE
            )
        material: dict[str, str] = {}
        for dimension in self.dimensions:
            raw = values.get(dimension)
            if not isinstance(raw, str) or not raw.strip():
                raise ResourceKeyRenderRejected(
                    ConcurrencyRejectionReason.MISSING_DIMENSION_VALUE
                )
            normalized = raw.strip()
            if len(normalized) > self.Limits.MAX_VALUE_LENGTH:
                raise ResourceKeyRenderRejected(
                    ConcurrencyRejectionReason.OVERSIZED_DIMENSION_VALUE
                )
            material[dimension.value] = normalized
        payload = canonical_json_bytes(
            {
                self.Keys.VERSION: self.SCHEMA_VERSION,
                self.Keys.TEMPLATE: self.canonical_template,
                self.Keys.VALUES: material,
            }
        )
        digest = hmac.new(bytes(secret), payload, hashlib.sha256).hexdigest()
        return f"{self.Digest.ALGORITHM}:{digest}"

    def render_or_none(
        self,
        *,
        secret: bytes,
        values: Mapping[ResourceKeyDimension, str],
    ) -> str | None:
        """Return :meth:`render`, or ``None`` when the key cannot be established.

        Schedulers use this variant: an absent key is reported to the planner as
        unknown resources, which produces a serial segment.
        """

        try:
            return self.render(secret=secret, values=values)
        except ResourceKeyRenderRejected:
            return None


class ConcurrencyPolicy(RuntimeContract):
    """Resolved policy for one operation.

    Every default is its vocabulary's conservative floor, so an instance built
    with no arguments encodes no knowledge and therefore cannot authorize
    parallel execution.

    ``max_parallelism`` is the one deliberately non-safety field: ``None`` means
    the capability declares no bound of its own and the enclosing batch or
    permit ceiling applies. Safety is carried by the closed vocabularies, never
    by a scheduling bound.

    :class:`ApprovalRequirement` is deliberately **not** a field here.
    ``ConcurrencyPolicy`` is a published cross-language record — the batch
    journal carries it and ``packages/api-types`` mirrors its field set
    verbatim — so its shape is a wire contract rather than a private one. The
    approval fact therefore lives on the *declaration* and the *resolution*,
    which are resolver-side values, and reaches this record through
    :meth:`narrowed_by_approval`, expressed in ``mode``: the one field that
    already means what an approval requirement implies. The consequence stays
    auditable — the policy reads ``serial`` and the planner's reason is
    ``policy_requires_serial`` — without widening the wire.
    """

    mode: ConcurrencyMode = ConcurrencyMode.conservative()
    side_effect: SideEffectKind = SideEffectKind.conservative()
    idempotency: IdempotencyKind = IdempotencyKind.conservative()
    resource_key_template: ResourceKeyTemplate | None = None
    max_parallelism: int | None = Field(
        default=None,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )
    rate_limit_scope: ConcurrencyScope = ConcurrencyScope.conservative()
    ordering_requirement: OrderingRequirement = OrderingRequirement.conservative()
    provider_session_constraint: ProviderSessionConstraint = (
        ProviderSessionConstraint.conservative()
    )
    policy_source: PolicySource = PolicySource.conservative()

    @field_validator("resource_key_template", mode="before")
    @classmethod
    def _coerce_resource_key_template(cls, value: object) -> object:
        if value is None or isinstance(value, (ResourceKeyTemplate, Mapping)):
            return value
        if isinstance(value, str):
            return ResourceKeyTemplate.from_template(value)
        raise ResourceKeyTemplateRejected(ConcurrencyRejectionReason.MALFORMED_TEMPLATE)

    def narrowed_by_approval(self, requirement: ApprovalRequirement) -> Self:
        """Return this policy with its concurrency posture bound by ``requirement``.

        The single translation from "this capability's dispatch may pause for a
        human" into the concurrency vocabulary, defined once so the resolver and
        the graph seam cannot disagree about what an approval requirement costs.

        It moves exactly one field — ``mode``, to the floor of its own
        vocabulary — and deliberately leaves ``max_parallelism`` alone. This
        module's contract is that safety is carried by the closed vocabularies
        and never by a scheduling bound: pinning the bound to ``1`` here would
        read as a *declared* ceiling of one and would destroy the distinction
        between "bounded at one" and "declares no bound of its own". A serial
        mode already forbids the overlap, and it says why.

        Narrowing-only and idempotent: ``SERIAL`` is the floor, so applying this
        twice, or after any other narrowing, is the same as applying it once,
        and no ``requirement`` can widen a policy through it.
        """

        if not requirement.may_park or self.mode is ConcurrencyMode.SERIAL:
            return self
        return self.model_copy(
            update={ConcurrencyPolicyField.MODE.value: ConcurrencyMode.SERIAL}
        )

    def value_for(self, policy_field: ConcurrencyPolicyField) -> object:
        """Return the resolved value for one closed policy field."""

        return getattr(self, policy_field.value)


class PermitBounds:
    """Hard, content-free bounds shared by every permit contract.

    The parallelism ceiling itself is not restated here — it belongs to
    :class:`ConcurrencyBounds`, which every F6 contract shares.
    """

    MAX_SCOPES_PER_REQUEST: Final[int] = 6
    MAX_WAITERS: Final[int] = 64
    MAX_ACTIVE_LEASES: Final[int] = 128
    MAX_TIMEOUT_SECONDS: Final[float] = 300.0
    IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    DIGEST_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
    SCOPE_KEY_DOMAIN: Final[str] = "agent_runtime.capabilities.concurrency.permit.v1"
    LEASE_ID_PREFIX: Final[str] = "permit_lease_"


class PermitScopeKey(RuntimeContract):
    """Content-free, collision-resistant identity for one permit scope.

    The key exposes the scope kind and a digest only. It is safe to log, meter,
    and persist.
    """

    kind: ConcurrencyScope
    digest: str = Field(pattern=PermitBounds.DIGEST_PATTERN)

    @property
    def token(self) -> str:
        """Return the stable ``kind:digest`` string used for internal tables."""

        return f"{self.kind.value}:{self.digest}"


class PermitScope(RuntimeContract):
    """One typed, pattern-constrained scope a permit may be bounded at.

    Component values are opaque identifiers, never bodies. ``subject_fingerprint``
    must already be a keyed SHA-256 digest produced by the control plane; the
    remaining components must be plain identifiers, which structurally excludes
    URLs, filesystem paths, and free text.

    ``ConcurrencyScope.UNKNOWN`` is refused: it names no pool, so it cannot be a
    permit identity. Callers resolve a declared scope through
    :meth:`ConcurrencyScope.permit_pool` first, which maps the unknown case onto
    the broadest pool rather than onto no pool at all.
    """

    class Keys:
        """Canonical digest payload keys."""

        DOMAIN = "domain"
        KIND = "kind"
        PROFILE_ID = "profile_id"
        SUBJECT_FINGERPRINT = "subject_fingerprint"
        INSTALLATION_ID = "installation_id"
        CONNECTOR_ID = "connector_id"
        CAPABILITY_NAME = "capability_name"

    _COMPONENT_NAMES: ClassVar[tuple[str, ...]] = (
        Keys.PROFILE_ID,
        Keys.SUBJECT_FINGERPRINT,
        Keys.INSTALLATION_ID,
        Keys.CONNECTOR_ID,
        Keys.CAPABILITY_NAME,
    )
    _REQUIRED_COMPONENTS: ClassVar[dict[ConcurrencyScope, tuple[str, ...]]] = {
        ConcurrencyScope.GLOBAL: (),
        ConcurrencyScope.PROFILE: (Keys.PROFILE_ID,),
        ConcurrencyScope.USER: (Keys.PROFILE_ID, Keys.SUBJECT_FINGERPRINT),
        ConcurrencyScope.INSTALLATION: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.INSTALLATION_ID,
        ),
        ConcurrencyScope.CONNECTOR: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.CONNECTOR_ID,
        ),
        ConcurrencyScope.CAPABILITY: (
            Keys.PROFILE_ID,
            Keys.SUBJECT_FINGERPRINT,
            Keys.CAPABILITY_NAME,
        ),
    }

    kind: ConcurrencyScope
    profile_id: str | None = Field(
        default=None, pattern=PermitBounds.IDENTIFIER_PATTERN
    )
    subject_fingerprint: str | None = Field(
        default=None,
        pattern=PermitBounds.DIGEST_PATTERN,
    )
    installation_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )
    connector_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )
    capability_name: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )

    @model_validator(mode="after")
    def _components_match_kind(self) -> Self:
        required = self._REQUIRED_COMPONENTS.get(self.kind)
        if required is None:
            raise ValueError(
                "an unknown concurrency scope cannot identify a permit pool"
            )
        for name in self._COMPONENT_NAMES:
            value = getattr(self, name)
            if name in required and value is None:
                raise ValueError(f"{self.kind.value} permit scope requires {name}")
            if name not in required and value is not None:
                raise ValueError(f"{self.kind.value} permit scope must not set {name}")
        return self

    def digest_payload(self) -> dict[str, str]:
        """Return the transient canonical body hashed into the scope key."""

        payload: dict[str, str] = {
            self.Keys.DOMAIN: PermitBounds.SCOPE_KEY_DOMAIN,
            self.Keys.KIND: self.kind.value,
        }
        for name in self._REQUIRED_COMPONENTS[self.kind]:
            component = getattr(self, name)
            payload[name] = str(component)
        return payload

    def key(self) -> PermitScopeKey:
        """Return the stable digested key for this scope."""

        return PermitScopeKey(
            kind=self.kind,
            digest=canonical_json_sha256(self.digest_payload()),
        )

    @classmethod
    def required_components(cls, kind: ConcurrencyScope) -> tuple[str, ...] | None:
        """Return the components ``kind``'s pool identity needs, or ``None``.

        ``None`` means the kind names no pool at all, which is true of exactly
        one member — ``UNKNOWN`` — and is why callers resolve a declared scope
        through :meth:`ConcurrencyScope.permit_pool` before asking.
        """

        required = cls._REQUIRED_COMPONENTS.get(kind)
        return None if required is None else tuple(required)

    @classmethod
    def for_pool(
        cls,
        kind: ConcurrencyScope,
        *,
        profile_id: str | None = None,
        subject_fingerprint: str | None = None,
        installation_id: str | None = None,
        connector_id: str | None = None,
        capability_name: str | None = None,
    ) -> Self | None:
        """Return the scope for one pool kind, or ``None`` when unidentifiable.

        The single component-driven constructor, so the required-component table
        stays the one place that knows what a pool is made of. Returning ``None``
        rather than raising is deliberate: "this operation cannot identify the
        pool its capability declared" is an answer a caller has to act on
        conservatively, not a fault. Every named constructor below is this method
        with its components already known to be present.
        """

        required = cls._REQUIRED_COMPONENTS.get(kind)
        if required is None:
            return None
        supplied: dict[str, str | None] = {
            cls.Keys.PROFILE_ID: profile_id,
            cls.Keys.SUBJECT_FINGERPRINT: subject_fingerprint,
            cls.Keys.INSTALLATION_ID: installation_id,
            cls.Keys.CONNECTOR_ID: connector_id,
            cls.Keys.CAPABILITY_NAME: capability_name,
        }
        components = {name: supplied[name] for name in required}
        if any(value is None for value in components.values()):
            return None
        return cls(kind=kind, **components)

    @classmethod
    def for_global(cls) -> Self:
        """Return the process-wide scope for this run."""

        return cls(kind=ConcurrencyScope.GLOBAL)

    @classmethod
    def for_profile(cls, *, profile_id: str) -> Self:
        """Return the deployment-profile scope."""

        return cls(kind=ConcurrencyScope.PROFILE, profile_id=profile_id)

    @classmethod
    def for_user(cls, *, profile_id: str, subject_fingerprint: str) -> Self:
        """Return the verified-subject scope."""

        return cls(
            kind=ConcurrencyScope.USER,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
        )

    @classmethod
    def for_installation(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        installation_id: str,
    ) -> Self:
        """Return the subject-qualified installed-capability-source scope."""

        return cls(
            kind=ConcurrencyScope.INSTALLATION,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            installation_id=installation_id,
        )

    @classmethod
    def for_connector(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        connector_id: str,
    ) -> Self:
        """Return the subject-qualified connector scope."""

        return cls(
            kind=ConcurrencyScope.CONNECTOR,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            connector_id=connector_id,
        )

    @classmethod
    def for_capability(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        capability_name: str,
    ) -> Self:
        """Return the subject-qualified capability scope."""

        return cls(
            kind=ConcurrencyScope.CAPABILITY,
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            capability_name=capability_name,
        )


class PermitCapacity(RuntimeContract):
    """Configured concurrency ceiling for one scope kind."""

    kind: ConcurrencyScope
    max_concurrency: int = Field(
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )

    @model_validator(mode="after")
    def _kind_identifies_a_pool(self) -> Self:
        if self.kind is ConcurrencyScope.UNKNOWN:
            raise ValueError(
                "an unknown concurrency scope cannot be given permit capacity"
            )
        return self


class PermitCapacityPolicy(RuntimeContract):
    """Configuration-driven capacities with a conservative serial default.

    An empty policy is fully serial. Any scope kind without an explicit entry
    is serial, so unknown metadata can never authorize overlap.
    """

    capacities: tuple[PermitCapacity, ...] = Field(
        default=(),
        max_length=len(ConcurrencyScope.permit_pool_kinds()),
    )

    @model_validator(mode="after")
    def _kinds_are_unique(self) -> Self:
        kinds = tuple(entry.kind for entry in self.capacities)
        if len(set(kinds)) != len(kinds):
            raise ValueError("permit capacity kinds must be unique")
        return self

    def capacity_for(self, kind: ConcurrencyScope) -> int:
        """Return the configured ceiling, or serial when unknown or absent."""

        for entry in self.capacities:
            if entry.kind is kind:
                return entry.max_concurrency
        return ConcurrencyBounds.SERIAL_PARALLELISM

    @classmethod
    def serial(cls) -> Self:
        """Return the fully conservative policy."""

        return cls()

    @classmethod
    def from_limits(cls, limits: Mapping[ConcurrencyScope, int]) -> Self:
        """Build a deterministic policy from a configuration mapping."""

        return cls(
            capacities=tuple(
                PermitCapacity(kind=kind, max_concurrency=limits[kind])
                for kind in sorted(limits, key=lambda entry: entry.value)
            )
        )


class PermitWaitMode(StrEnum):
    """Closed set of saturation behaviors a caller may request."""

    REFUSE_IF_SATURATED = "refuse_if_saturated"
    QUEUE = "queue"


class PermitOutcome(StrEnum):
    """Closed, deterministic result of one acquisition attempt."""

    ADMITTED = "admitted"
    QUEUED_ADMITTED = "queued_admitted"
    REFUSED_SATURATED = "refused_saturated"
    REFUSED_DEADLINE = "refused_deadline"
    REFUSED_QUEUE_FULL = "refused_queue_full"
    REFUSED_DISPOSED = "refused_disposed"

    @property
    def admitted(self) -> bool:
        """Return whether this outcome holds capacity."""

        return self in (PermitOutcome.ADMITTED, PermitOutcome.QUEUED_ADMITTED)


class PermitAcquisitionRequest(RuntimeContract):
    """One child's declared permit scopes and saturation policy."""

    scopes: tuple[PermitScope, ...] = Field(
        min_length=1,
        max_length=PermitBounds.MAX_SCOPES_PER_REQUEST,
    )
    wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED
    timeout_seconds: float | None = Field(
        default=None,
        ge=0.0,
        le=PermitBounds.MAX_TIMEOUT_SECONDS,
    )
    max_parallelism: int | None = Field(
        default=None,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )

    @model_validator(mode="after")
    def _request_is_well_formed(self) -> Self:
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("permit scopes must be unique")
        if self.wait_mode is PermitWaitMode.QUEUE and self.timeout_seconds is None:
            raise ValueError("queued permit acquisition requires timeout_seconds")
        if (
            self.wait_mode is PermitWaitMode.REFUSE_IF_SATURATED
            and self.timeout_seconds is not None
        ):
            raise ValueError("refuse_if_saturated acquisition must not set a timeout")
        return self

    def scope_keys(self) -> tuple[PermitScopeKey, ...]:
        """Return this request's digested keys in a deterministic order."""

        return tuple(
            sorted(
                (scope.key() for scope in self.scopes),
                key=lambda key: (key.kind.value, key.digest),
            )
        )

    @classmethod
    def for_operation(
        cls,
        *,
        profile_id: str,
        subject_fingerprint: str,
        capability_name: str,
        connector_id: str | None = None,
        installation_id: str | None = None,
        rate_limit_scope: ConcurrencyScope = ConcurrencyScope.UNKNOWN,
        wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED,
        timeout_seconds: float | None = None,
        max_parallelism: int | None = None,
    ) -> Self:
        """Build the canonical broad-to-narrow scope ladder for one child.

        The ladder always includes the ``GLOBAL`` pool, which is why a
        capability whose declared rate-limit scope is ``UNKNOWN`` is already
        bounded: :meth:`ConcurrencyScope.permit_pool` resolves it to a pool this
        ladder always acquires.

        ``rate_limit_scope`` is the capability's *declared* pool, and it may only
        add to the ladder or narrow the requested width — never remove a rung.
        Two outcomes are possible and both are narrowings:

        - The declared pool is identifiable, so it is acquired. When the ladder
          already holds it this is a no-op, which is why an undeclared
          capability's request is byte-identical to the pre-declaration one.
        - The declared pool is **not** identifiable — a capability declaring a
          connector rate limit on an operation that carries no connector id, say.
          The declaration is then known to be unenforceable at the pool it names,
          so the request is bound to :attr:`ConcurrencyBounds.SERIAL_PARALLELISM`.
          Unknown means serial, structurally, rather than silently unbounded.
        """

        scopes: list[PermitScope] = [
            PermitScope.for_global(),
            PermitScope.for_profile(profile_id=profile_id),
            PermitScope.for_user(
                profile_id=profile_id,
                subject_fingerprint=subject_fingerprint,
            ),
        ]
        if installation_id is not None:
            scopes.append(
                PermitScope.for_installation(
                    profile_id=profile_id,
                    subject_fingerprint=subject_fingerprint,
                    installation_id=installation_id,
                )
            )
        if connector_id is not None:
            scopes.append(
                PermitScope.for_connector(
                    profile_id=profile_id,
                    subject_fingerprint=subject_fingerprint,
                    connector_id=connector_id,
                )
            )
        scopes.append(
            PermitScope.for_capability(
                profile_id=profile_id,
                subject_fingerprint=subject_fingerprint,
                capability_name=capability_name,
            )
        )
        declared = PermitScope.for_pool(
            rate_limit_scope.permit_pool(),
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            installation_id=installation_id,
            connector_id=connector_id,
            capability_name=capability_name,
        )
        if declared is None:
            max_parallelism = ConcurrencyBounds.SERIAL_PARALLELISM
        elif declared not in scopes:
            scopes.append(declared)
        return cls(
            scopes=tuple(scopes),
            wait_mode=wait_mode,
            timeout_seconds=timeout_seconds,
            max_parallelism=max_parallelism,
        )


class PermitLease(RuntimeContract):
    """Deterministic outcome of one acquisition, admitted or refused.

    A refused lease has no ``lease_id``. Saturation is reported here, never as
    an exception, so a caller can never mistake it for a tool failure.
    """

    outcome: PermitOutcome
    scope_keys: tuple[PermitScopeKey, ...] = Field(
        min_length=1,
        max_length=PermitBounds.MAX_SCOPES_PER_REQUEST,
    )
    effective_capacity: int = Field(
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )
    lease_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _lease_identity_matches_outcome(self) -> Self:
        if self.outcome.admitted and self.lease_id is None:
            raise ValueError("an admitted permit lease requires a lease_id")
        if not self.outcome.admitted and self.lease_id is not None:
            raise ValueError("a refused permit lease must not carry a lease_id")
        return self

    @property
    def admitted(self) -> bool:
        """Return whether this lease holds capacity."""

        return self.outcome.admitted


class BatchFailurePolicy(StrEnum):
    """Admission behavior after a child operation fails."""

    FAIL_FAST = "fail_fast"
    COLLECT_ALL = "collect_all"
    STOP_NEW = "stop_new"


class BatchOperation(RuntimeContract):
    """Planning facts for one already-authorized operation.

    ``dependency_ids=None`` and ``resource_fingerprints=None`` mean unknown,
    not empty. Callers must supply empty tuples to explicitly attest that an
    operation has no dependencies or resource subjects.

    Resource fingerprints are keyed/opaque values produced outside this
    domain — normally by :meth:`ResourceKeyTemplate.render`. Raw connector
    arguments and object identifiers do not belong in a batch plan.
    """

    operation_id: str = Field(min_length=1, max_length=255)
    authorization_epoch: str = Field(min_length=1, max_length=255)
    dependency_ids: tuple[str, ...] | None = None
    resource_fingerprints: tuple[str, ...] | None = None

    @staticmethod
    def _canonical_identifiers(
        value: object,
        *,
        field_name: str,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        if isinstance(value, (str, bytes)):
            raise ValueError(f"{field_name} must be an iterable of identifiers")
        normalized: list[str] = []
        for item in value:  # type: ignore[union-attr]
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{field_name} must contain non-empty strings")
            identifier = item.strip()
            if identifier in normalized:
                raise ValueError(f"{field_name} must not contain duplicates")
            normalized.append(identifier)
        return tuple(normalized)

    @field_validator("operation_id", "authorization_epoch")
    @classmethod
    def _strip_required_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operation identifiers must be non-empty")
        return normalized

    @field_validator("dependency_ids", mode="before")
    @classmethod
    def _canonical_dependencies(cls, value: object) -> tuple[str, ...] | None:
        return cls._canonical_identifiers(value, field_name="dependency_ids")

    @field_validator("resource_fingerprints", mode="before")
    @classmethod
    def _canonical_resources(cls, value: object) -> tuple[str, ...] | None:
        normalized = cls._canonical_identifiers(
            value,
            field_name="resource_fingerprints",
        )
        if normalized is not None and any(
            ResourceKeyTemplate.Digest.PATTERN.fullmatch(item) is None
            for item in normalized
        ):
            raise ValueError(
                "resource_fingerprints must contain keyed HMAC-SHA256 digests"
            )
        return normalized

    @model_validator(mode="after")
    def _cannot_depend_on_self(self) -> Self:
        if self.dependency_ids and self.operation_id in self.dependency_ids:
            raise ValueError("an operation cannot depend on itself")
        return self


class OperationBatch(RuntimeContract):
    """Ordered operations and the batch-level concurrency authority.

    ``allowance`` carries both the posture and the ceiling, so the same value a
    kill switch narrows is the value the planner reads. It defaults to
    :meth:`ConcurrencyAllowance.serial`, which means an unconfigured batch plans
    every operation into its own serial segment.
    """

    batch_id: str = Field(min_length=1, max_length=255)
    parent_operation_id: str | None = Field(default=None, min_length=1, max_length=255)
    operations: tuple[BatchOperation, ...] = Field(min_length=1, max_length=100)
    deadline: datetime | None = None
    allowance: ConcurrencyAllowance = Field(default_factory=ConcurrencyAllowance.serial)
    failure_policy: BatchFailurePolicy = BatchFailurePolicy.STOP_NEW

    @field_validator("allowance", mode="before")
    @classmethod
    def _coerce_allowance(cls, value: object) -> object:
        return ConcurrencyAllowance.coerce(value)

    @property
    def effective_max_parallelism(self) -> int:
        """Return the width this batch's authority actually permits."""

        return self.allowance.effective_max_parallelism

    @field_validator("batch_id")
    @classmethod
    def _strip_batch_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("batch_id must be non-empty")
        return normalized

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _operation_graph_is_well_formed(self) -> Self:
        operation_ids = tuple(operation.operation_id for operation in self.operations)
        known_ids = set(operation_ids)
        if len(operation_ids) != len(known_ids):
            raise ValueError("batch operation_id values must be unique")
        preceding_ids: set[str] = set()
        for operation in self.operations:
            unknown_dependencies = set(operation.dependency_ids or ()) - known_ids
            if unknown_dependencies:
                raise ValueError(
                    "dependency_ids must reference operations in the batch"
                )
            forward_dependencies = set(operation.dependency_ids or ()) - preceding_ids
            if forward_dependencies:
                raise ValueError(
                    "dependency_ids must reference earlier operations in the batch"
                )
            preceding_ids.add(operation.operation_id)
        return self


class BatchSegmentMode(StrEnum):
    """Execution mode assigned to one contiguous batch segment."""

    SERIAL = "serial"
    PARALLEL = "parallel"


class BatchSegmentReason(StrEnum):
    """Stable, content-free reason explaining a planner decision."""

    INDEPENDENT_READS = "independent_reads"
    BATCH_SERIAL_DEFAULT = "batch_serial_default"
    CONSERVATIVE_POLICY_DEFAULT = "conservative_policy_default"
    POLICY_REQUIRES_SERIAL = "policy_requires_serial"
    POLICY_PARALLELISM_DISABLED = "policy_parallelism_disabled"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"
    EFFECTFUL_OPERATION = "effectful_operation"
    UNKNOWN_DEPENDENCIES = "unknown_dependencies"
    EXPLICIT_DEPENDENCIES = "explicit_dependencies"
    UNKNOWN_RESOURCES = "unknown_resources"
    SAME_SUBJECT_REQUIRES_RESOURCE = "same_subject_requires_resource"
    RESOURCE_CONFLICT = "resource_conflict"
    AUTHORIZATION_EPOCH_BARRIER = "authorization_epoch_barrier"
    INSUFFICIENT_PARALLEL_MEMBERS = "insufficient_parallel_members"


class BatchSegment(RuntimeContract):
    """One deterministic serial or parallel section of a plan.

    ``allowance`` is the same authority type the enclosing batch and any live
    kill switch speak, so narrowing a segment is the same operation as narrowing
    a batch. A segment can never be wider than the authority it carries.
    """

    segment_index: int = Field(ge=0)
    mode: BatchSegmentMode
    operation_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=ConcurrencyBounds.MAX_PARALLELISM,
    )
    reason: BatchSegmentReason
    allowance: ConcurrencyAllowance

    @field_validator("allowance", mode="before")
    @classmethod
    def _coerce_allowance(cls, value: object) -> object:
        return ConcurrencyAllowance.coerce(value)

    @property
    def effective_max_parallelism(self) -> int:
        """Return the width this segment's authority actually permits."""

        return self.allowance.effective_max_parallelism

    @model_validator(mode="after")
    def _mode_matches_width(self) -> Self:
        width = self.effective_max_parallelism
        if self.mode is BatchSegmentMode.PARALLEL and len(self.operation_ids) < 2:
            raise ValueError("parallel segments require at least two operations")
        if (
            self.mode is BatchSegmentMode.SERIAL
            and width != ConcurrencyBounds.SERIAL_PARALLELISM
        ):
            raise ValueError("serial segments require max_parallelism=1")
        if len(self.operation_ids) > width:
            raise ValueError("segment width exceeds max_parallelism")
        return self


class BatchPlan(RuntimeContract):
    """Stable planner output that covers every input operation exactly once."""

    batch_id: str = Field(min_length=1, max_length=255)
    operation_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    segments: tuple[BatchSegment, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _segments_cover_ordered_operations(self) -> Self:
        expected_indices = tuple(range(len(self.segments)))
        actual_indices = tuple(segment.segment_index for segment in self.segments)
        if actual_indices != expected_indices:
            raise ValueError("segment indices must be contiguous and zero-based")
        planned_ids = tuple(
            operation_id
            for segment in self.segments
            for operation_id in segment.operation_ids
        )
        if planned_ids != self.operation_ids:
            raise ValueError("segments must preserve and exactly cover operation order")
        return self


__all__ = (
    "ApprovalRequirement",
    "BatchFailurePolicy",
    "BatchOperation",
    "BatchPlan",
    "BatchSegment",
    "BatchSegmentMode",
    "BatchSegmentReason",
    "ConcurrencyAllowance",
    "ConcurrencyBounds",
    "ConcurrencyMode",
    "ConcurrencyPolicy",
    "ConcurrencyPolicyField",
    "ConcurrencyScope",
    "IdempotencyKind",
    "NarrowableEnum",
    "OperationBatch",
    "OrderingRequirement",
    "PermitAcquisitionRequest",
    "PermitBounds",
    "PermitCapacity",
    "PermitCapacityPolicy",
    "PermitLease",
    "PermitOutcome",
    "PermitScope",
    "PermitScopeKey",
    "PermitWaitMode",
    "PolicySource",
    "ProviderSessionConstraint",
    "ResourceKeyDimension",
    "ResourceKeyTemplate",
    "SideEffectKind",
)
