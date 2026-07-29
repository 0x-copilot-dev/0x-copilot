"""Global, per-connector, and per-capability serial kill switches for F6.

This module is pure domain policy for one property: an operator must be able to
force capability execution back to serial *while a run is in flight*, and must
never be able to do the opposite.

Narrowing is structural, not conventional.  A directive names a target and
carries nothing else, so "force serial" is the only statement the vocabulary
can express.  There is no representable directive that enables parallelism,
raises a ceiling, or re-enables a path the immutable run snapshot already
disabled.  Composition is a fold of :meth:`FeatureMode.least_authoritative` and
``min`` over the snapshot and every applicable switch, so it is idempotent,
commutative, and bounded above by the snapshot.  The decision contract itself
refuses to be constructed if it would broaden the snapshot, so even a future
resolver bug cannot widen authority.

Effective allowance is always ``narrowest(snapshot_allowance, live_switch)``.
Both bounds hold at once: :class:`ConcurrencyKillSwitchGate` captures the
immutable snapshot once and re-reads the live switch at every admission
decision, so a switch flipped mid-run applies to the next decision without a
restart while never widening what the run snapshot already allowed.  Decisions
are immutable values and are never revisited, so admitted or completed work is
never retroactively invalidated.

Inputs are trusted deployment/operator configuration — never model output and
never connector data — but are validated anyway.  Anything unparseable,
unknown, or unreadable resolves to serial; nothing here ever fails open.
Diagnostics carry a scope and a stable reason code only: no identifiers,
connector URLs, arguments, bodies, or user content ever enter a decision.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from itertools import islice
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.contracts import ConcurrencyAllowance
from agent_runtime.execution.contracts import RuntimeContract

_MAX_IDENTIFIER_LENGTH: Final[int] = 128
_MAX_DIRECTIVES: Final[int] = 256
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z0-9][a-z0-9._:-]{{0,{_MAX_IDENTIFIER_LENGTH - 1}}}$"
)


class _DirectiveDocumentKeys:
    """Closed key vocabulary for one operator directive document."""

    SCOPE: Final[str] = "scope"
    IDENTIFIER: Final[str] = "identifier"

    @classmethod
    def is_closed(cls, document: Mapping[object, object]) -> bool:
        """Return whether ``document`` uses exactly the allowed key vocabulary."""

        keys = set(document)
        return cls.SCOPE in keys and keys <= {cls.SCOPE, cls.IDENTIFIER}


class ConcurrencyKillSwitchScope(StrEnum):
    """Closed set of scopes at which an operator may force serial execution.

    This vocabulary is deliberately **not** folded into
    :class:`~agent_runtime.capabilities.concurrency.contracts.ConcurrencyScope`,
    even though its three members are spelled the same as three of that enum's
    seven. Its smallness is a safety property, not an accident of authorship: a
    kill switch must not silently gain ``USER``, ``INSTALLATION``, or
    ``PROFILE`` scopes because someone added a rate-limit scope to an unrelated
    enum. Sharing the type would make an emergency control's blast radius a
    side effect of an edit made for a different reason.

    Adding a member here is therefore an explicit decision about what an
    operator may disable, and it must be made in this file, with the
    corresponding :class:`ConcurrencyKillSwitchReason` and precedence entry.
    """

    GLOBAL = "global"
    CONNECTOR = "connector"
    CAPABILITY = "capability"

    @property
    def precedence(self) -> int:
        """Return the deterministic diagnostic-attribution order.

        Every asserted switch produces the same serial outcome, so this order
        never changes *what* is decided.  It only fixes *which* switch is named
        when several apply: broadest scope first.
        """

        return {
            ConcurrencyKillSwitchScope.GLOBAL: 0,
            ConcurrencyKillSwitchScope.CONNECTOR: 1,
            ConcurrencyKillSwitchScope.CAPABILITY: 2,
        }[self]

    @property
    def requires_identifier(self) -> bool:
        """Return whether this scope names one target rather than everything."""

        return self is not ConcurrencyKillSwitchScope.GLOBAL

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        """Return the closed scope, or ``None`` for an unknown scope token."""

        if isinstance(raw, ConcurrencyKillSwitchScope):
            return cls(raw.value)
        if not isinstance(raw, str):
            return None
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return None


class ConcurrencyKillSwitchError(RuntimeError):
    """Base typed error for the F6 serial kill-switch domain."""


class ConcurrencyKillSwitchTargetError(ConcurrencyKillSwitchError):
    """A trusted call site supplied an identity that is not a usable target.

    The public message names the scope only.  A rejected value is never echoed,
    because it may be an operator-supplied connector reference.
    """

    _MESSAGE_TEMPLATE: Final[str] = "invalid {scope} kill-switch target identity"

    def __init__(self, *, scope: ConcurrencyKillSwitchScope) -> None:
        self.scope = scope
        super().__init__(self._MESSAGE_TEMPLATE.format(scope=scope.value))


class ConcurrencyKillSwitchTarget(RuntimeContract):
    """Validated identity of one kill-switch target.

    Operator configuration reaches this domain as free-form text.  It becomes a
    lookup key only after it is normalized into this closed shape, so a raw
    string is never used directly as a key.
    """

    scope: ConcurrencyKillSwitchScope
    identifier: str | None = Field(default=None, max_length=_MAX_IDENTIFIER_LENGTH)

    _LOOKUP_KEY_SEPARATOR: ClassVar[str] = ":"

    @model_validator(mode="after")
    def _identity_matches_scope(self) -> Self:
        if self.scope.requires_identifier:
            if self.identifier is None or not _IDENTIFIER_PATTERN.fullmatch(
                self.identifier
            ):
                raise ValueError(
                    "a scoped kill-switch target requires a normalized identifier"
                )
        elif self.identifier is not None:
            raise ValueError("the global kill-switch target has no identifier")
        return self

    @classmethod
    def global_(cls) -> Self:
        """Return the target that covers every capability in the deployment."""

        return cls(scope=ConcurrencyKillSwitchScope.GLOBAL)

    @classmethod
    def for_connector(cls, identifier: object) -> Self:
        """Return one connector target, raising a typed error when unusable."""

        return cls._require(ConcurrencyKillSwitchScope.CONNECTOR, identifier)

    @classmethod
    def for_capability(cls, identifier: object) -> Self:
        """Return one capability target, raising a typed error when unusable."""

        return cls._require(ConcurrencyKillSwitchScope.CAPABILITY, identifier)

    @classmethod
    def for_scope(
        cls,
        scope: ConcurrencyKillSwitchScope,
        identifier: object,
    ) -> Self | None:
        """Return the typed target, or ``None`` when the identity is unusable."""

        if not scope.requires_identifier:
            return cls(scope=scope) if identifier is None else None
        normalized = cls._normalize_identifier(identifier)
        if normalized is None:
            return None
        return cls(scope=scope, identifier=normalized)

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        """Parse one trusted directive element; ``None`` means fail closed."""

        if isinstance(raw, ConcurrencyKillSwitchTarget):
            return cls(scope=raw.scope, identifier=raw.identifier)
        if isinstance(raw, str):
            return cls._parse_compact(raw)
        if isinstance(raw, Mapping):
            return cls._parse_document(raw)
        return None

    @classmethod
    def _require(
        cls,
        scope: ConcurrencyKillSwitchScope,
        identifier: object,
    ) -> Self:
        target = cls.for_scope(scope, identifier)
        if target is None:
            raise ConcurrencyKillSwitchTargetError(scope=scope)
        return target

    @classmethod
    def _normalize_identifier(cls, identifier: object) -> str | None:
        if not isinstance(identifier, str):
            return None
        normalized = identifier.strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized):
            return None
        return normalized

    @classmethod
    def _parse_compact(cls, raw: str) -> Self | None:
        normalized = raw.strip().lower()
        if not normalized:
            return None
        scope_token, separator, identifier = normalized.partition(
            cls._LOOKUP_KEY_SEPARATOR
        )
        scope = ConcurrencyKillSwitchScope.parse(scope_token)
        if scope is None:
            return None
        return cls.for_scope(scope, identifier if separator else None)

    @classmethod
    def _parse_document(cls, raw: Mapping[object, object]) -> Self | None:
        if not _DirectiveDocumentKeys.is_closed(raw):
            return None
        scope = ConcurrencyKillSwitchScope.parse(raw.get(_DirectiveDocumentKeys.SCOPE))
        if scope is None:
            return None
        return cls.for_scope(scope, raw.get(_DirectiveDocumentKeys.IDENTIFIER))

    @property
    def lookup_key(self) -> str:
        """Return the derived, low-cardinality key for this validated identity.

        This is the only place an identifier may be combined into a key, and it
        is for permit/registry adapters.  It never enters a decision, because a
        decision is content-free.
        """

        if self.identifier is None:
            return self.scope.value
        return f"{self.scope.value}{self._LOOKUP_KEY_SEPARATOR}{self.identifier}"


class ConcurrencyKillSwitchSourceStatus(StrEnum):
    """Closed availability posture of the live switch source."""

    ABSENT = "absent"
    AVAILABLE = "available"
    UNPARSEABLE = "unparseable"
    UNAVAILABLE = "unavailable"

    @property
    def fails_closed(self) -> bool:
        """Return whether this posture forces serial regardless of target."""

        return self in {
            ConcurrencyKillSwitchSourceStatus.UNPARSEABLE,
            ConcurrencyKillSwitchSourceStatus.UNAVAILABLE,
        }


class ConcurrencyKillSwitchDirectives(RuntimeContract):
    """Validated live switch set, or a fail-closed reason it is unusable.

    A single malformed element invalidates the whole set.  Partial application
    is deliberately impossible: the unreadable element may have been the one
    that mattered, so the safe interpretation is that everything is serial.
    """

    status: ConcurrencyKillSwitchSourceStatus
    targets: frozenset[ConcurrencyKillSwitchTarget] = frozenset()

    @model_validator(mode="after")
    def _only_an_available_set_carries_targets(self) -> Self:
        if (
            self.targets
            and self.status is not ConcurrencyKillSwitchSourceStatus.AVAILABLE
        ):
            raise ValueError("only an available kill-switch set may carry targets")
        return self

    @classmethod
    def absent(cls) -> Self:
        """Return the posture for "no live switch configured"."""

        return cls(status=ConcurrencyKillSwitchSourceStatus.ABSENT)

    @classmethod
    def unavailable(cls) -> Self:
        """Return the posture for a switch source that could not be read."""

        return cls(status=ConcurrencyKillSwitchSourceStatus.UNAVAILABLE)

    @classmethod
    def unparseable(cls) -> Self:
        """Return the posture for switch configuration that did not validate."""

        return cls(status=ConcurrencyKillSwitchSourceStatus.UNPARSEABLE)

    @classmethod
    def available(cls, targets: Iterable[ConcurrencyKillSwitchTarget]) -> Self:
        """Return a validated switch set, possibly empty."""

        return cls(
            status=ConcurrencyKillSwitchSourceStatus.AVAILABLE,
            targets=frozenset(targets),
        )

    @classmethod
    def parse(cls, raw: object) -> Self:
        """Parse trusted operator configuration, failing closed on anything odd."""

        if raw is None:
            return cls.absent()
        if isinstance(raw, ConcurrencyKillSwitchDirectives):
            return cls(status=raw.status, targets=raw.targets)
        if isinstance(raw, str):
            return cls._parse_json(raw)
        if isinstance(raw, (bytes, bytearray, Mapping)):
            return cls.unparseable()
        if isinstance(raw, Iterable):
            return cls._parse_elements(raw)
        return cls.unparseable()

    @classmethod
    def _parse_json(cls, raw: str) -> Self:
        normalized = raw.strip()
        if not normalized:
            return cls.absent()
        try:
            document = json.loads(normalized)
        except ValueError:
            return cls.unparseable()
        if not isinstance(document, list):
            return cls.unparseable()
        return cls._parse_elements(document)

    @classmethod
    def _parse_elements(cls, elements: Iterable[object]) -> Self:
        bounded = list(islice(iter(elements), _MAX_DIRECTIVES + 1))
        if len(bounded) > _MAX_DIRECTIVES:
            return cls.unparseable()
        parsed: set[ConcurrencyKillSwitchTarget] = set()
        for element in bounded:
            target = ConcurrencyKillSwitchTarget.parse(element)
            if target is None:
                return cls.unparseable()
            parsed.add(target)
        return cls.available(parsed)

    def asserts(self, target: ConcurrencyKillSwitchTarget) -> bool:
        """Return whether an operator forced ``target`` serial."""

        return target in self.targets

    @property
    def forces_serial_everywhere(self) -> bool:
        """Return whether this posture narrows every decision to serial."""

        return self.status.fails_closed


class ConcurrencyKillSwitchReason(StrEnum):
    """Stable, content-free explanation of one admission decision."""

    SNAPSHOT_GOVERNS = "snapshot_governs"
    SNAPSHOT_ALREADY_SERIAL = "snapshot_already_serial"
    GLOBAL_KILL_SWITCH = "global_kill_switch"
    CONNECTOR_KILL_SWITCH = "connector_kill_switch"
    CAPABILITY_KILL_SWITCH = "capability_kill_switch"
    UNKNOWN_TARGET = "unknown_target"
    UNPARSEABLE_SWITCH_CONFIG = "unparseable_switch_config"
    SWITCH_SOURCE_UNAVAILABLE = "switch_source_unavailable"

    @classmethod
    def for_scope(cls, scope: ConcurrencyKillSwitchScope) -> Self:
        """Return the reason code owned by one switch scope."""

        return {
            ConcurrencyKillSwitchScope.GLOBAL: cls.GLOBAL_KILL_SWITCH,
            ConcurrencyKillSwitchScope.CONNECTOR: cls.CONNECTOR_KILL_SWITCH,
            ConcurrencyKillSwitchScope.CAPABILITY: cls.CAPABILITY_KILL_SWITCH,
        }[scope]


class ConcurrencyKillSwitchDecision(RuntimeContract):
    """Immutable, body-free outcome of one admission decision.

    It deliberately carries no target identifier.  An operator already knows
    what they disabled, and a decision may be recorded or logged, so it must
    never carry a connector id, URL, argument, or any user content.

    The validator below is the structural narrowing proof: a decision that
    would broaden the run snapshot cannot be constructed at all.
    """

    snapshot_allowance: ConcurrencyAllowance
    effective_allowance: ConcurrencyAllowance
    reason: ConcurrencyKillSwitchReason
    narrowed_by_scope: ConcurrencyKillSwitchScope | None = None

    @model_validator(mode="after")
    def _decision_never_broadens_the_snapshot(self) -> Self:
        if self.effective_allowance.mode.rank > self.snapshot_allowance.mode.rank:
            raise ValueError("a kill-switch decision cannot broaden the run snapshot")
        if (
            self.effective_allowance.max_parallelism
            > self.snapshot_allowance.max_parallelism
        ):
            raise ValueError("a kill-switch decision cannot raise the run ceiling")
        if self.narrowed_by_scope is not None and self.reason is not (
            ConcurrencyKillSwitchReason.for_scope(self.narrowed_by_scope)
        ):
            raise ValueError("kill-switch scope and reason must agree")
        return self

    @property
    def permits_parallel(self) -> bool:
        """Return whether this decision admits overlapping work."""

        return self.effective_allowance.permits_parallel

    @property
    def max_parallelism(self) -> int:
        """Return the width a scheduler may use for this decision."""

        return self.effective_allowance.effective_max_parallelism

    @property
    def serial_forced(self) -> bool:
        """Return whether this decision is narrower than the run snapshot."""

        return self.snapshot_allowance.permits_parallel and not self.permits_parallel


@runtime_checkable
class ConcurrencyKillSwitchSourcePort(Protocol):
    """Live read of trusted operator kill-switch configuration.

    Deliberately synchronous and cheap: it is called on the admission path for
    every decision so that a switch flipped mid-run takes effect without a
    restart.  Adapters must refresh out of band — config reload, poll, or watch
    — rather than performing IO here.  Any raise is treated as unavailable and
    resolves to serial.
    """

    def current_kill_switch_directives(self) -> object: ...


class ConcurrencyKillSwitchResolver:
    """Fold the run snapshot and every applicable live switch into one decision.

    Reason precedence is deterministic and documented:

    1. an unreadable switch source;
    2. unparseable switch configuration;
    3. an unusable request target identity;
    4. the global switch;
    5. the connector switch;
    6. the capability switch;
    7. the run snapshot.

    The order fixes only which reason is reported.  It cannot change the
    outcome, because every one of steps 1-6 narrows to exactly serial and
    :meth:`ConcurrencyAllowance.narrowed_by` is idempotent and commutative.
    """

    _SERIAL_SOURCE_REASONS: ClassVar[
        Mapping[ConcurrencyKillSwitchSourceStatus, ConcurrencyKillSwitchReason]
    ] = MappingProxyType(
        {
            ConcurrencyKillSwitchSourceStatus.UNAVAILABLE: (
                ConcurrencyKillSwitchReason.SWITCH_SOURCE_UNAVAILABLE
            ),
            ConcurrencyKillSwitchSourceStatus.UNPARSEABLE: (
                ConcurrencyKillSwitchReason.UNPARSEABLE_SWITCH_CONFIG
            ),
        }
    )

    def read(
        self,
        source: ConcurrencyKillSwitchSourcePort | None,
    ) -> ConcurrencyKillSwitchDirectives:
        """Read the live switch now, converting any failure into fail-closed."""

        if source is None:
            return ConcurrencyKillSwitchDirectives.absent()
        try:
            raw = source.current_kill_switch_directives()
        except Exception:
            return ConcurrencyKillSwitchDirectives.unavailable()
        return ConcurrencyKillSwitchDirectives.parse(raw)

    def resolve(
        self,
        *,
        snapshot_allowance: ConcurrencyAllowance,
        directives: ConcurrencyKillSwitchDirectives,
        connector_id: object = None,
        capability_id: object = None,
    ) -> ConcurrencyKillSwitchDecision:
        """Return ``narrowest(snapshot_allowance, live_switch)`` for one decision.

        ``connector_id``/``capability_id`` are ``None`` when the decision has no
        such dimension.  A supplied but unusable identity is not ignored: it
        resolves to serial, because an unrecognized target could be exactly the
        one an operator disabled.
        """

        failure_reason = self._SERIAL_SOURCE_REASONS.get(directives.status)
        if failure_reason is not None:
            return self._serial(snapshot_allowance, failure_reason)

        applicable: list[ConcurrencyKillSwitchTarget] = [
            ConcurrencyKillSwitchTarget.global_()
        ]
        for scope, raw_identifier in (
            (ConcurrencyKillSwitchScope.CONNECTOR, connector_id),
            (ConcurrencyKillSwitchScope.CAPABILITY, capability_id),
        ):
            if raw_identifier is None:
                continue
            target = ConcurrencyKillSwitchTarget.for_scope(scope, raw_identifier)
            if target is None:
                return self._serial(
                    snapshot_allowance,
                    ConcurrencyKillSwitchReason.UNKNOWN_TARGET,
                )
            applicable.append(target)

        asserted = tuple(target for target in applicable if directives.asserts(target))
        if not asserted:
            return ConcurrencyKillSwitchDecision(
                snapshot_allowance=snapshot_allowance,
                effective_allowance=snapshot_allowance,
                reason=(
                    ConcurrencyKillSwitchReason.SNAPSHOT_ALREADY_SERIAL
                    if snapshot_allowance.is_serial
                    else ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS
                ),
            )

        effective = snapshot_allowance
        for _target in asserted:
            effective = effective.narrowed_to_serial()
        narrowing_scope = min(
            (target.scope for target in asserted),
            key=lambda scope: scope.precedence,
        )
        return ConcurrencyKillSwitchDecision(
            snapshot_allowance=snapshot_allowance,
            effective_allowance=effective,
            reason=ConcurrencyKillSwitchReason.for_scope(narrowing_scope),
            narrowed_by_scope=narrowing_scope,
        )

    @staticmethod
    def _serial(
        snapshot_allowance: ConcurrencyAllowance,
        reason: ConcurrencyKillSwitchReason,
    ) -> ConcurrencyKillSwitchDecision:
        return ConcurrencyKillSwitchDecision(
            snapshot_allowance=snapshot_allowance,
            effective_allowance=snapshot_allowance.narrowed_to_serial(),
            reason=reason,
        )


class ConcurrencyKillSwitchGate:
    """Run-scoped gate whose snapshot is fixed and whose switch is live.

    The immutable run snapshot allowance is captured once, at construction.
    The live switch is re-read on every :meth:`admit` call, so an operator
    flipping a switch mid-run affects the next admission decision without a
    restart.  Because the resolver folds the switch against the captured
    snapshot, flipping a switch back off can restore at most what the snapshot
    already allowed — never more.  Decisions already returned are immutable and
    are never revisited, so admitted or completed work is never retroactively
    invalidated.
    """

    def __init__(
        self,
        *,
        snapshot_allowance: ConcurrencyAllowance,
        source: ConcurrencyKillSwitchSourcePort | None = None,
        resolver: ConcurrencyKillSwitchResolver | None = None,
    ) -> None:
        self._snapshot_allowance = snapshot_allowance
        self._source = source
        self._resolver = resolver or ConcurrencyKillSwitchResolver()

    @property
    def snapshot_allowance(self) -> ConcurrencyAllowance:
        """Return the immutable ceiling this run was admitted under."""

        return self._snapshot_allowance

    def admit(
        self,
        *,
        connector_id: object = None,
        capability_id: object = None,
    ) -> ConcurrencyKillSwitchDecision:
        """Decide admission now, against the current live switch value."""

        return self._resolver.resolve(
            snapshot_allowance=self._snapshot_allowance,
            directives=self._resolver.read(self._source),
            connector_id=connector_id,
            capability_id=capability_id,
        )


__all__ = (
    "ConcurrencyKillSwitchDecision",
    "ConcurrencyKillSwitchDirectives",
    "ConcurrencyKillSwitchError",
    "ConcurrencyKillSwitchGate",
    "ConcurrencyKillSwitchReason",
    "ConcurrencyKillSwitchResolver",
    "ConcurrencyKillSwitchScope",
    "ConcurrencyKillSwitchSourcePort",
    "ConcurrencyKillSwitchSourceStatus",
    "ConcurrencyKillSwitchTarget",
    "ConcurrencyKillSwitchTargetError",
)
