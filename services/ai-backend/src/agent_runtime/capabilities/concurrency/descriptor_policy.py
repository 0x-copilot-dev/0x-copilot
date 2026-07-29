"""Trusted descriptor metadata and the F6 concurrency precedence resolver.

Precedence is fixed: product catalog → user-approved tightening → trusted
provider tightening → conservative serial/unknown. The catalog *establishes* a
policy; every less authoritative source may only *narrow* it.

Widening is not forbidden by convention here — it is structurally impossible.
The only composition operator used is :meth:`NarrowableEnum.narrowest` (a
minimum over declaration-order authority rank), plus its two non-enum analogues
for the resource-key template and the scheduling bound. A source that declares a
wider value therefore cannot change the outcome; the attempt is recorded as a
typed :class:`ConcurrencyPolicyRejection` and the narrower value survives.
:meth:`ConcurrencyPolicyResolver.resolve_strict` turns that record into a raised
typed error for build-time catalog and fixture validation.

Application order is derived from the same rank rather than hardcoded, and the
fold is a per-field minimum, so resolving A-then-B equals B-then-A.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import ClassVar, Self

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyBounds,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyPolicyField,
    ConcurrencyScope,
    IdempotencyKind,
    NarrowableEnum,
    OrderingRequirement,
    PolicySource,
    ProviderSessionConstraint,
    ResourceKeyTemplate,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.errors import (
    ConcurrencyDeclarationRejected,
    ConcurrencyPolicyWideningRejected,
    ConcurrencyRejectionReason,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes


class ConcurrencyPolicyRejection(RuntimeContract):
    """Content-free record of one refused declared value.

    Carries the trust source, the closed field, and a low-cardinality reason.
    It never carries the declared value, because that value arrived from an
    untrusted connector or MCP server.
    """

    source: PolicySource
    policy_field: ConcurrencyPolicyField
    reason: ConcurrencyRejectionReason

    @property
    def is_widening(self) -> bool:
        """Return whether this rejection was an attempt to widen authority."""

        return self.reason in {
            ConcurrencyRejectionReason.WIDER_THAN_ESTABLISHED,
            ConcurrencyRejectionReason.TEMPLATE_NOT_NARROWER,
        }


class ConcurrencyNarrowing(RuntimeContract):
    """Result of applying one declaration to an already-established policy."""

    policy: ConcurrencyPolicy
    changed_fields: tuple[ConcurrencyPolicyField, ...] = ()
    rejections: tuple[ConcurrencyPolicyRejection, ...] = ()


class CapabilityConcurrencyDeclaration(RuntimeContract):
    """Concurrency metadata attributed to exactly one trust source.

    Every field is optional and ``None`` means *not declared*: the established
    value survives untouched. This is distinct from declaring a conservative
    value, which actively narrows. :class:`ConcurrencyDescriptorParser` maps an
    unparseable declared value onto the vocabulary's conservative floor, so
    malformed metadata narrows to serial instead of being silently ignored.

    ``source`` is supplied by the caller from the trust boundary the metadata
    crossed. It is never read out of the payload, and
    ``CONSERVATIVE_DEFAULT`` is not an admissible declaration source — that
    member is the resolver's floor, not a claim anyone may make.
    """

    CAPABILITY_REF_PATTERN: ClassVar[str] = r"^cap_[0-9a-f]{32}$"

    capability_ref: str = Field(pattern=CAPABILITY_REF_PATTERN)
    source: PolicySource
    mode: ConcurrencyMode | None = None
    side_effect: SideEffectKind | None = None
    idempotency: IdempotencyKind | None = None
    resource_key_template: ResourceKeyTemplate | None = None
    max_parallelism: int | None = Field(
        default=None,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )
    rate_limit_scope: ConcurrencyScope | None = None
    ordering_requirement: OrderingRequirement | None = None
    provider_session_constraint: ProviderSessionConstraint | None = None
    rejections: tuple[ConcurrencyPolicyRejection, ...] = ()

    @model_validator(mode="after")
    def _source_is_declarable(self) -> Self:
        if self.source is PolicySource.CONSERVATIVE_DEFAULT:
            raise ConcurrencyDeclarationRejected(
                ConcurrencyRejectionReason.UNSUPPORTED_SOURCE
            )
        return self

    def declared_fields(self) -> tuple[ConcurrencyPolicyField, ...]:
        """Return the closed fields this source actually declared, in order."""

        return tuple(
            policy_field
            for policy_field in ConcurrencyPolicyField
            if getattr(self, policy_field.value) is not None
        )

    def establish(self) -> ConcurrencyPolicy:
        """Return the policy this declaration establishes as the resolution base.

        Undeclared fields fall to :class:`ConcurrencyPolicy`'s conservative
        structural defaults rather than to anything this source implies.
        """

        return ConcurrencyPolicy(
            **{
                policy_field.value: getattr(self, policy_field.value)
                for policy_field in self.declared_fields()
            },
            policy_source=self.source,
        )

    def narrow(self, established: ConcurrencyPolicy) -> ConcurrencyNarrowing:
        """Apply this declaration to ``established`` as a narrowing-only fold."""

        changes: dict[str, object] = {}
        changed_fields: list[ConcurrencyPolicyField] = []
        rejections: list[ConcurrencyPolicyRejection] = []
        for policy_field in self.declared_fields():
            declared = getattr(self, policy_field.value)
            current = established.value_for(policy_field)
            narrowed = self._narrowest(policy_field, current, declared)
            if narrowed != declared:
                rejections.append(
                    ConcurrencyPolicyRejection(
                        source=self.source,
                        policy_field=policy_field,
                        reason=self._widening_reason(policy_field),
                    )
                )
            if narrowed != current:
                changes[policy_field.value] = narrowed
                changed_fields.append(policy_field)
        return ConcurrencyNarrowing(
            policy=established.model_copy(update=changes) if changes else established,
            changed_fields=tuple(changed_fields),
            rejections=tuple(rejections),
        )

    @classmethod
    def _narrowest(
        cls,
        policy_field: ConcurrencyPolicyField,
        current: object,
        declared: object,
    ) -> object:
        if policy_field is ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE:
            return ResourceKeyTemplate.narrowest(current, declared)  # type: ignore[arg-type]
        if policy_field is ConcurrencyPolicyField.MAX_PARALLELISM:
            if current is None:
                return declared
            return min(current, declared)  # type: ignore[call-overload]
        return type(declared).narrowest(current, declared)  # type: ignore[union-attr]

    @staticmethod
    def _widening_reason(
        policy_field: ConcurrencyPolicyField,
    ) -> ConcurrencyRejectionReason:
        if policy_field is ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE:
            return ConcurrencyRejectionReason.TEMPLATE_NOT_NARROWER
        return ConcurrencyRejectionReason.WIDER_THAN_ESTABLISHED


class ConcurrencyDescriptorParser:
    """Parse untrusted descriptor metadata into one conservative declaration.

    The wire vocabulary is :class:`ConcurrencyPolicyField` itself, so payload
    keys, record fields, and policy attributes cannot drift apart.

    A malformed payload never raises: a connector or MCP server must not be able
    to abort a run by sending garbage. A declared-but-unparseable value resolves
    to its vocabulary's conservative floor, an unparseable scheduling bound
    resolves to ``1``, and an unparseable resource-key template is dropped so no
    key can be established. Each of those outcomes is recorded as a typed
    rejection. Only caller misuse — an inadmissible ``source`` or a
    non-opaque ``capability_ref`` — raises.
    """

    _ENUM_BY_FIELD: ClassVar[Mapping[ConcurrencyPolicyField, type[NarrowableEnum]]] = {
        ConcurrencyPolicyField.MODE: ConcurrencyMode,
        ConcurrencyPolicyField.SIDE_EFFECT: SideEffectKind,
        ConcurrencyPolicyField.IDEMPOTENCY: IdempotencyKind,
        ConcurrencyPolicyField.RATE_LIMIT_SCOPE: ConcurrencyScope,
        ConcurrencyPolicyField.ORDERING_REQUIREMENT: OrderingRequirement,
        ConcurrencyPolicyField.PROVIDER_SESSION_CONSTRAINT: ProviderSessionConstraint,
    }

    def parse(
        self,
        *,
        capability_ref: str,
        source: PolicySource,
        payload: object = None,
    ) -> CapabilityConcurrencyDeclaration:
        """Return the declaration this payload may claim for ``source``."""

        values: dict[str, object] = {}
        rejections: list[ConcurrencyPolicyRejection] = []
        mapping = payload if isinstance(payload, Mapping) else {}
        for policy_field in ConcurrencyPolicyField:
            raw = mapping.get(policy_field.value)
            if raw is None:
                continue
            parsed, reason = self._parse_field(policy_field, raw)
            if reason is not None:
                rejections.append(
                    ConcurrencyPolicyRejection(
                        source=source,
                        policy_field=policy_field,
                        reason=reason,
                    )
                )
            if parsed is not None:
                values[policy_field.value] = parsed
        return CapabilityConcurrencyDeclaration(
            capability_ref=capability_ref,
            source=source,
            rejections=tuple(rejections),
            **values,
        )

    @classmethod
    def _parse_field(
        cls,
        policy_field: ConcurrencyPolicyField,
        raw: object,
    ) -> tuple[object | None, ConcurrencyRejectionReason | None]:
        if policy_field is ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE:
            template = ResourceKeyTemplate.parse(raw)
            if template is None:
                return None, ConcurrencyRejectionReason.MALFORMED_TEMPLATE
            return template, None
        if policy_field is ConcurrencyPolicyField.MAX_PARALLELISM:
            if (
                isinstance(raw, int)
                and not isinstance(raw, bool)
                and ConcurrencyBounds.SERIAL_PARALLELISM
                <= raw
                <= ConcurrencyBounds.MAX_PARALLELISM
            ):
                return raw, None
            return (
                ConcurrencyBounds.SERIAL_PARALLELISM,
                ConcurrencyRejectionReason.UNPARSEABLE_DEFAULTED_SAFE,
            )
        vocabulary = cls._ENUM_BY_FIELD[policy_field]
        if isinstance(raw, vocabulary):
            return raw, None
        if isinstance(raw, str):
            try:
                return vocabulary(raw.strip().lower()), None
            except ValueError:
                pass
        return (
            vocabulary.conservative(),
            ConcurrencyRejectionReason.UNPARSEABLE_DEFAULTED_SAFE,
        )


class ConcurrencyPolicyResolution(RuntimeContract):
    """Effective policy for one capability plus its content-free lineage."""

    class Digest:
        ALGORITHM = "sha256"

    capability_ref: str = Field(
        pattern=CapabilityConcurrencyDeclaration.CAPABILITY_REF_PATTERN
    )
    policy: ConcurrencyPolicy
    considered_sources: tuple[PolicySource, ...] = ()
    contributing_sources: tuple[PolicySource, ...] = ()
    rejections: tuple[ConcurrencyPolicyRejection, ...] = ()
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def of(
        cls,
        *,
        capability_ref: str,
        policy: ConcurrencyPolicy,
        considered_sources: Sequence[PolicySource] = (),
        contributing_sources: Sequence[PolicySource] = (),
        rejections: Sequence[ConcurrencyPolicyRejection] = (),
    ) -> Self:
        """Build a resolution and bind its policy digest for plan lineage."""

        return cls(
            capability_ref=capability_ref,
            policy=policy,
            considered_sources=tuple(considered_sources),
            contributing_sources=tuple(contributing_sources),
            rejections=tuple(rejections),
            policy_digest=cls.digest_of(policy),
        )

    @classmethod
    def digest_of(cls, policy: ConcurrencyPolicy) -> str:
        """Return the stable digest of one resolved policy."""

        encoded = canonical_json_bytes(policy.model_dump(mode="json"))
        return f"{cls.Digest.ALGORITHM}:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def widening_rejections(self) -> tuple[ConcurrencyPolicyRejection, ...]:
        """Return only the rejections that were attempts to widen authority."""

        return tuple(
            rejection for rejection in self.rejections if rejection.is_widening
        )


class ConcurrencyPolicyResolver:
    """Resolve one capability's effective policy across trusted sources.

    ``resolve`` never raises on a widening attempt: an untrusted provider must
    not be able to fail a run by over-claiming. It narrows, records, and
    continues. ``resolve_strict`` raises instead, and is intended for checked-in
    catalog and fixture validation where an over-claim is a build defect.
    """

    _BASE_SOURCE: ClassVar[PolicySource] = PolicySource.PRODUCT_CATALOG

    def resolve(
        self,
        *,
        capability_ref: str,
        declarations: Sequence[CapabilityConcurrencyDeclaration] = (),
    ) -> ConcurrencyPolicyResolution:
        """Return the narrowest policy every supplied source jointly supports."""

        ordered = self._ordered(
            capability_ref=capability_ref,
            declarations=declarations,
        )
        base = next(
            (
                declaration
                for declaration in ordered
                if declaration.source is self._BASE_SOURCE
            ),
            None,
        )
        policy = base.establish() if base is not None else ConcurrencyPolicy()
        contributing: list[PolicySource] = [policy.policy_source]
        rejections: list[ConcurrencyPolicyRejection] = []
        for declaration in ordered:
            rejections.extend(declaration.rejections)
            if declaration is base:
                continue
            narrowing = declaration.narrow(policy)
            policy = narrowing.policy
            rejections.extend(narrowing.rejections)
            if narrowing.changed_fields:
                contributing.append(declaration.source)
        effective_source = PolicySource.narrowest(*contributing)
        return ConcurrencyPolicyResolution.of(
            capability_ref=capability_ref,
            policy=policy.model_copy(update={"policy_source": effective_source}),
            considered_sources=tuple(declaration.source for declaration in ordered),
            contributing_sources=tuple(dict.fromkeys(contributing)),
            rejections=rejections,
        )

    def resolve_strict(
        self,
        *,
        capability_ref: str,
        declarations: Sequence[CapabilityConcurrencyDeclaration] = (),
    ) -> ConcurrencyPolicyResolution:
        """Resolve, raising a typed error on the first recorded rejection."""

        resolution = self.resolve(
            capability_ref=capability_ref,
            declarations=declarations,
        )
        for rejection in resolution.rejections:
            if rejection.is_widening:
                raise ConcurrencyPolicyWideningRejected(rejection.reason)
            raise ConcurrencyDeclarationRejected(rejection.reason)
        return resolution

    @staticmethod
    def _ordered(
        *,
        capability_ref: str,
        declarations: Sequence[CapabilityConcurrencyDeclaration],
    ) -> tuple[CapabilityConcurrencyDeclaration, ...]:
        seen: set[PolicySource] = set()
        for declaration in declarations:
            if declaration.capability_ref != capability_ref:
                raise ConcurrencyDeclarationRejected(
                    ConcurrencyRejectionReason.CAPABILITY_MISMATCH
                )
            if declaration.source in seen:
                raise ConcurrencyDeclarationRejected(
                    ConcurrencyRejectionReason.DUPLICATE_SOURCE
                )
            seen.add(declaration.source)
        return tuple(
            sorted(declarations, key=lambda declaration: -declaration.source.rank)
        )


__all__ = (
    "CapabilityConcurrencyDeclaration",
    "ConcurrencyDescriptorParser",
    "ConcurrencyNarrowing",
    "ConcurrencyPolicyRejection",
    "ConcurrencyPolicyResolution",
    "ConcurrencyPolicyResolver",
)
