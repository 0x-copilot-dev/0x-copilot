"""Run-scoped sidecar: which Skills a run offered, and which it actually used.

Progressive disclosure creates a question the runtime could not previously
answer: *was the token we spent on that index row worth spending?* A Skill that
appears in every prompt and is never loaded is pure tax; a Skill that is
repeatedly loaded from the deferred tail belongs in the always-loaded tier. Both
judgements need one fact — surfaced versus used — and nothing in the system
recorded it.

This ledger records exactly that fact and nothing more. Four disjoint sets,
assigned once at prompt assembly and appended to when the model loads a Skill:

``surfaced``  rendered as a full row in the system-prompt index
``deferred``  visible to this run, but past the index bound (reachable via
              ``list_skills``)
``hidden``    conditions unmet — never offered at all
``used``      ``load_skill`` returned a bundle for it

``surfaced`` minus ``used`` is the wasted-token set; ``deferred`` intersected
with ``used`` is the promotion candidate set. Deliberately **not** here:
ageing, archiving, consolidation or any self-improvement loop. Those are a
lifecycle, they need this data to exist first, and Hermes' own version of them
(``tools/skill_usage.py``, 1145 lines) is an order of magnitude more machinery
than the recording it is built on.

Binding follows the runtime's established per-run ContextVar pattern
(``McpToolAnnotationsRegistry``, ``CitationLedger``, ``ToolBudgetGuard``): the
worker's run handler binds one ledger before building the harness and unbinds it
in its ``finally``, so every exit path — completion, failure, timeout, cancel —
drains the same object exactly once. Every classmethod is a no-op when nothing
is bound, so replay, evals and unit tests need no ledger.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import logging

from agent_runtime.execution.contracts import RuntimeContract

_LOGGER = logging.getLogger(__name__)


class Log:
    """The one line the sidecar emits per run, spelled once.

    Counts go out at INFO; Skill *names* are user-authored and go out only at
    DEBUG, the same posture the rest of the runtime takes toward user content.
    """

    SUMMARY = (
        "skill_usage.run run_id=%s surfaced=%d used=%d unused=%d deferred=%d hidden=%d"
    )
    NAMES = "skill_usage.names run_id=%s surfaced=%s used=%s deferred=%s hidden=%s"
    NONE = "-"

    @classmethod
    def joined(cls, names: Iterable[str]) -> str:
        """Render a name list for the log, never blank."""
        return ",".join(names) or cls.NONE


class SkillUsageSnapshot(RuntimeContract):
    """An immutable read of one run's Skill offer/use accounting."""

    run_id: str = ""
    surfaced: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()
    used: tuple[str, ...] = ()

    @property
    def surfaced_unused(self) -> tuple[str, ...]:
        """Skills the run paid prompt tokens for and never loaded."""
        used = set(self.used)
        return tuple(name for name in self.surfaced if name not in used)

    @property
    def used_from_deferred(self) -> tuple[str, ...]:
        """Skills the model had to search for — promotion candidates."""
        deferred = set(self.deferred)
        return tuple(name for name in self.used if name in deferred)


_SKILL_USAGE_LEDGER: ContextVar["SkillUsageLedger | None"] = ContextVar(
    "skill_usage_ledger", default=None
)


@dataclass
class SkillUsageLedger:
    """Per-run accounting of the Skills a run offered and the ones it loaded."""

    run_id: str = ""
    _surfaced: list[str] = field(default_factory=list, repr=False)
    _deferred: list[str] = field(default_factory=list, repr=False)
    _hidden: list[str] = field(default_factory=list, repr=False)
    _used: list[str] = field(default_factory=list, repr=False)

    def offer(
        self,
        *,
        surfaced: Iterable[str] = (),
        deferred: Iterable[str] = (),
        hidden: Iterable[str] = (),
    ) -> None:
        """Record the index decision for this run.

        Replaces rather than accumulates: the index is assembled once per run,
        and a second assembly (a rebuilt harness after an approval resume) is
        the *current* offer, not an addition to a stale one.
        """

        self._surfaced = list(surfaced)
        self._deferred = list(deferred)
        self._hidden = list(hidden)

    def use(self, name: str) -> None:
        """Record that the model loaded ``name``'s full body."""

        cleaned = name.strip()
        if cleaned and cleaned not in self._used:
            self._used.append(cleaned)

    def snapshot(self) -> SkillUsageSnapshot:
        """Return an immutable read of the accounting so far."""

        return SkillUsageSnapshot(
            run_id=self.run_id,
            surfaced=tuple(self._surfaced),
            deferred=tuple(self._deferred),
            hidden=tuple(self._hidden),
            used=tuple(self._used),
        )

    @property
    def offered_names(self) -> tuple[str, ...]:
        """Every Skill this run may reach: surfaced first, then deferred.

        Skills excluded by their own conditions are absent by construction —
        ``list_skills`` must not become a back door around the visibility
        predicate.
        """

        return tuple(self._surfaced) + tuple(self._deferred)

    @property
    def has_offer(self) -> bool:
        """Return ``True`` once an index decision has been recorded."""
        return bool(self._surfaced or self._deferred or self._hidden)

    # ── run binding ────────────────────────────────────────────────────

    @classmethod
    def bind_for_run(cls, ledger: "SkillUsageLedger") -> Token:
        """Bind ``ledger`` to the current run; return the restoration token."""

        return _SKILL_USAGE_LEDGER.set(ledger)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Emit the run's summary and restore the previous binding."""

        ledger = _SKILL_USAGE_LEDGER.get(None)
        if ledger is not None:
            ledger.emit()
        _SKILL_USAGE_LEDGER.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "SkillUsageLedger | None":
        """Return the bound ledger, or ``None`` outside a bound run."""

        return _SKILL_USAGE_LEDGER.get(None)

    @classmethod
    def record_offer(
        cls,
        *,
        surfaced: Iterable[str] = (),
        deferred: Iterable[str] = (),
        hidden: Iterable[str] = (),
    ) -> None:
        """Record the index decision on the bound ledger; no-op when unbound."""

        ledger = _SKILL_USAGE_LEDGER.get(None)
        if ledger is not None:
            ledger.offer(surfaced=surfaced, deferred=deferred, hidden=hidden)

    @classmethod
    def record_use(cls, name: str) -> None:
        """Record a load on the bound ledger; no-op when unbound."""

        ledger = _SKILL_USAGE_LEDGER.get(None)
        if ledger is not None:
            ledger.use(name)

    def emit(self) -> None:
        """Log the run's offer/use accounting. Never raises."""

        try:
            snapshot = self.snapshot()
            _LOGGER.info(
                Log.SUMMARY,
                snapshot.run_id,
                len(snapshot.surfaced),
                len(snapshot.used),
                len(snapshot.surfaced_unused),
                len(snapshot.deferred),
                len(snapshot.hidden),
            )
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    Log.NAMES,
                    snapshot.run_id,
                    Log.joined(snapshot.surfaced),
                    Log.joined(snapshot.used),
                    Log.joined(snapshot.deferred),
                    Log.joined(snapshot.hidden),
                )
        except Exception:  # pragma: no cover - observability must never fail a run
            _LOGGER.debug("skill_usage.emit_failed", exc_info=True)
