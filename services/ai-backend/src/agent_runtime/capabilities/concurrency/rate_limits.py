"""The join between a *declared* rate-limit scope and the pool it is taken at.

F6.1 resolves ``ConcurrencyPolicy.rate_limit_scope`` through the full precedence
chain, narrows it, and journals it. F6.4 built a permit table whose pools are
already scoped, bounded, and digest-keyed at exactly that vocabulary. Nothing
joined them: every operation acquired the same fixed ladder — global, profile,
user, capability — so a capability declaring "my rate limit applies per
connector" got a *private* capability pool and never contended with anything
else on that connector. The declaration was resolved, narrowed, and then read by
nobody.

This module is that join, and it is deliberately not a second permit mechanism.
It chooses :class:`PermitScope` values; the one
:class:`~agent_runtime.capabilities.concurrency.permits.RunPermitManager` still
owns every count, every waiter, and every lease.

Three properties are structural rather than conventional.

**The ladder is a floor.** The base ladder is whatever
:meth:`PermitAcquisitionRequest.for_operation` builds from an operation's own
identity. A declaration may only *add* a pool to it. Adding a pool can only
lower admission — :meth:`RunPermitManager.effective_capacity` is a minimum over
the request's scopes and every scope must independently have room — so no
declaration can widen what an operation was already bound by.

**Only a declaration opens a shared pool.** The connector and installation
components are carried on the identity but never turned into a rung by
themselves. A connector pool is acquired because the capability *declared*
``connector``, which is what makes ``rate_limit_scope`` load-bearing rather than
decorative, and what stops an identity field from quietly changing contention.

**An unidentifiable declared pool is serial.** A capability that declares a
connector rate limit on an operation carrying no connector id has a rate limit
that cannot be enforced where it was declared. That is positively known to be
unenforceable, not merely undeclared, so the request is bound to
``SERIAL_PARALLELISM``. Undeclared is different and stays cheap: ``UNKNOWN``
resolves through :meth:`ConcurrencyScope.permit_pool` to ``GLOBAL``, a rung the
base ladder always has, so an undeclared capability's request is identical to
the one it got before this module existed.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyBounds,
    ConcurrencyPolicy,
    ConcurrencyScope,
    PermitAcquisitionRequest,
    PermitBounds,
    PermitScope,
    PermitWaitMode,
)
from agent_runtime.execution.contracts import RuntimeContract


class RateLimitPoolReason(StrEnum):
    """Closed, content-free account of why one operation acquired what it did."""

    #: The declared pool was already a rung of the operation's own ladder.
    ALREADY_LADDERED = "already_laddered"
    #: The declared pool was identifiable and added to the ladder.
    DECLARED_POOL_ADDED = "declared_pool_added"
    #: The declared pool needs a component this operation does not carry.
    POOL_UNIDENTIFIABLE = "pool_unidentifiable"


class RateLimitScopeIdentity(RuntimeContract):
    """The opaque components one operation's permit pools can be built from.

    Carrying the components without implying rungs is the whole point: an
    operation may *know* its connector without being bound at the connector
    pool, and only a declaration decides that it is. Every field is an opaque
    identifier or a keyed digest, pattern-constrained by
    :class:`PermitScope` itself, so nothing here can carry a body.
    """

    profile_id: str = Field(pattern=PermitBounds.IDENTIFIER_PATTERN)
    subject_fingerprint: str = Field(pattern=PermitBounds.DIGEST_PATTERN)
    capability_name: str = Field(pattern=PermitBounds.IDENTIFIER_PATTERN)
    connector_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )
    installation_id: str | None = Field(
        default=None,
        pattern=PermitBounds.IDENTIFIER_PATTERN,
    )


class RateLimitPoolDecision(RuntimeContract):
    """What one operation acquires, and the pool its declaration resolved to.

    Content-free and safe to log: it names scope *kinds* and digested keys, never
    a connector, a path, or a capability's arguments.
    """

    declared: ConcurrencyScope
    pool: ConcurrencyScope | None
    reason: RateLimitPoolReason
    scopes: tuple[PermitScope, ...] = Field(
        min_length=1,
        max_length=PermitBounds.MAX_SCOPES_PER_REQUEST,
    )
    max_parallelism: int | None = Field(
        default=None,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )

    @model_validator(mode="after")
    def _unidentifiable_is_serial(self) -> Self:
        """Pin the fail-closed rule in the type, not only in the resolver.

        A decision that names no pool and is not bound to serial is not a
        conservative reading of an unenforceable declaration; it is the bug this
        module exists to remove, so it cannot be constructed.
        """

        if self.pool is None and self.max_parallelism != (
            ConcurrencyBounds.SERIAL_PARALLELISM
        ):
            raise ValueError(
                "an unidentifiable rate-limit pool must be bound to serial width"
            )
        if self.pool is not None and self.pool is ConcurrencyScope.UNKNOWN:
            raise ValueError("an unknown concurrency scope cannot identify a pool")
        return self

    @property
    def bounded_by_declaration(self) -> bool:
        """Return whether the declaration changed this operation's admission."""

        return self.reason is not RateLimitPoolReason.ALREADY_LADDERED


class RateLimitScopeResolver:
    """Resolve the pools one operation acquires at from its declared scope.

    Stateless and deterministic. The same policy and identity always produce the
    same decision, which is what lets a plan replay to the same pools.
    """

    def decide(
        self,
        policy: ConcurrencyPolicy,
        identity: RateLimitScopeIdentity,
    ) -> RateLimitPoolDecision:
        """Return the pools, the width bound, and why they were chosen."""

        ladder = self._ladder(identity)
        pool = policy.rate_limit_scope.permit_pool()
        declared = PermitScope.for_pool(
            pool,
            profile_id=identity.profile_id,
            subject_fingerprint=identity.subject_fingerprint,
            installation_id=identity.installation_id,
            connector_id=identity.connector_id,
            capability_name=identity.capability_name,
        )
        if declared is None:
            return RateLimitPoolDecision(
                declared=policy.rate_limit_scope,
                pool=None,
                reason=RateLimitPoolReason.POOL_UNIDENTIFIABLE,
                scopes=ladder,
                max_parallelism=ConcurrencyBounds.SERIAL_PARALLELISM,
            )
        if declared in ladder:
            return RateLimitPoolDecision(
                declared=policy.rate_limit_scope,
                pool=pool,
                reason=RateLimitPoolReason.ALREADY_LADDERED,
                scopes=ladder,
                max_parallelism=policy.max_parallelism,
            )
        return RateLimitPoolDecision(
            declared=policy.rate_limit_scope,
            pool=pool,
            reason=RateLimitPoolReason.DECLARED_POOL_ADDED,
            scopes=(*ladder, declared),
            max_parallelism=policy.max_parallelism,
        )

    def request_for(
        self,
        policy: ConcurrencyPolicy,
        identity: RateLimitScopeIdentity,
        *,
        wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED,
        timeout_seconds: float | None = None,
        max_parallelism: int | None = None,
    ) -> PermitAcquisitionRequest:
        """Return the acquisition request this policy and identity authorize.

        ``max_parallelism`` is the caller's own already-narrowed ceiling — a
        segment allowance, typically. It is folded with the decision's bound by
        minimum, so neither the caller nor the declaration can raise the other.
        """

        decision = self.decide(policy, identity)
        return PermitAcquisitionRequest(
            scopes=decision.scopes,
            wait_mode=wait_mode,
            timeout_seconds=timeout_seconds,
            max_parallelism=_narrowest_width(max_parallelism, decision.max_parallelism),
        )

    @staticmethod
    def _ladder(identity: RateLimitScopeIdentity) -> tuple[PermitScope, ...]:
        """Return the base ladder built from the operation's own identity.

        Deliberately delegated to :meth:`PermitAcquisitionRequest.for_operation`
        with no connector or installation component, so the ladder has exactly
        one definition and a declaration is the only thing that can extend it.
        """

        return PermitAcquisitionRequest.for_operation(
            profile_id=identity.profile_id,
            subject_fingerprint=identity.subject_fingerprint,
            capability_name=identity.capability_name,
        ).scopes


@runtime_checkable
class RateLimitPolicyLookup(Protocol):
    """Answer which resolved policy governs one planned child.

    A port rather than a table because the resolved policy lives in the durable
    plan, and whoever composes the run is the only party that can reach it.
    """

    def policy_for(self, capability_ref: str) -> ConcurrencyPolicy | None:
        """Return the resolved policy for this capability, or ``None``."""


class DeclaredRateLimitPolicies:
    """A frozen ``capability_ref -> ConcurrencyPolicy`` lookup.

    Immutable after construction, for the reason
    :class:`~agent_runtime.capabilities.concurrency.graph_admission.DeclaredConcurrencyPolicySource`
    is: a run must not acquire a policy mid-flight, or a turn would be planned
    under one set of pools and run under another.
    """

    __slots__ = ("_policies",)

    def __init__(self, policies: Mapping[str, ConcurrencyPolicy]) -> None:
        self._policies = dict(policies)

    def __len__(self) -> int:
        return len(self._policies)

    def policy_for(self, capability_ref: str) -> ConcurrencyPolicy | None:
        """Return the resolved policy for this capability, or ``None``."""

        return self._policies.get(capability_ref)


class RateLimitedPermitScopeFactory:
    """A ``BatchPermitScopeFactory`` that honours each child's declared scope.

    Drop-in for the identity-only closure the composition root supplies today:
    same call shape, same return type, and an unknown child still gets exactly
    the ladder it got before. The difference is that a child whose capability
    declared a rate-limit scope now acquires at that pool.

    A child with no plan position, an unknown capability, or a lookup that
    fails answers with the conservative policy, whose ``rate_limit_scope`` is
    ``UNKNOWN`` and therefore resolves to a rung the ladder already has. Every
    failure mode is the pre-declaration ladder rather than a wider one.
    """

    __slots__ = ("_identity", "_policies", "_resolver")

    def __init__(
        self,
        *,
        profile_id: str,
        subject_fingerprint: str,
        policies: RateLimitPolicyLookup,
        resolver: RateLimitScopeResolver | None = None,
    ) -> None:
        self._identity = (profile_id, subject_fingerprint)
        self._policies = policies
        self._resolver = resolver or RateLimitScopeResolver()

    def decision_for(
        self,
        *,
        capability_ref: str | None,
        capability_name: str,
        connector_id: str | None = None,
        installation_id: str | None = None,
    ) -> RateLimitPoolDecision:
        """Return the full decision, including the width it would bind."""

        profile_id, subject_fingerprint = self._identity
        identity = RateLimitScopeIdentity(
            profile_id=profile_id,
            subject_fingerprint=subject_fingerprint,
            capability_name=capability_name,
            connector_id=connector_id,
            installation_id=installation_id,
        )
        return self._resolver.decide(self._policy_for(capability_ref), identity)

    def scopes_for(
        self,
        *,
        capability_ref: str | None,
        capability_name: str,
        connector_id: str | None = None,
        installation_id: str | None = None,
    ) -> tuple[PermitScope, ...]:
        """Return only the pools, for the scope-factory contract."""

        return self.decision_for(
            capability_ref=capability_ref,
            capability_name=capability_name,
            connector_id=connector_id,
            installation_id=installation_id,
        ).scopes

    def _policy_for(self, capability_ref: str | None) -> ConcurrencyPolicy:
        if capability_ref is None:
            return ConcurrencyPolicy()
        try:
            policy = self._policies.policy_for(capability_ref)
        except Exception:  # noqa: BLE001 - a lookup that fails declares nothing.
            return ConcurrencyPolicy()
        return policy if isinstance(policy, ConcurrencyPolicy) else ConcurrencyPolicy()


def _narrowest_width(*widths: int | None) -> int | None:
    """Return the narrowest declared width, or ``None`` when none is declared.

    The scheduling-bound analogue of :meth:`NarrowableEnum.narrowest`: ``None``
    means *no bound declared* and never competes, so folding a bound with an
    absent one keeps the bound rather than dropping it.
    """

    declared = tuple(width for width in widths if width is not None)
    return min(declared) if declared else None


__all__ = (
    "DeclaredRateLimitPolicies",
    "RateLimitPolicyLookup",
    "RateLimitPoolDecision",
    "RateLimitPoolReason",
    "RateLimitScopeIdentity",
    "RateLimitScopeResolver",
    "RateLimitedPermitScopeFactory",
)
