"""Filesystem bypass mode — the three-tier resolution of "does this pause?".

Bypass removes the approval **pause**. It never removes the ledger, and it
never creates a second way to touch the disk. There is exactly ONE host write
lane in this runtime:

    staged C3 overlay  ->  A4 effect ledger  ->  C2 commit executor

and every host mutation goes down it whether bypass is on or off. What bypass
decides is only whether that lane stops and waits for a human between the
"staged" and "approved" ledger rows. With bypass on, the approval row is still
appended — authored by ``EffectActor.POLICY`` instead of ``EffectActor.USER`` —
so the run's history still answers "who approved this, and when".

The direct ``write_file`` path on a host path stays ``deny`` at the deepagents
permission layer in every mode (see
:mod:`agent_runtime.capabilities.desktop.host_filesystem` rule 5). Nothing in
this module can change that, and nothing should: a second, unrecorded write
lane is what produced the two-workspace-backend defect earlier in this program.

Three tiers, most specific wins
-------------------------------
1. **master** — a Settings switch persisted in ``WorkspaceBehaviorOverrides``
   (``filesystem_bypass_enabled``), default **off**. While it is off the run
   and message controls are not offered by the UI, and a request that carries
   one anyway resolves to ``manual`` here. Fail-closed, not
   offered-and-ignored: a client cannot opt itself in.
2. **run** — a selection that stays in force for the run.
3. **message** — a selection for one message/turn.

``message > run > master``. A per-message pick must be able to turn bypass
back OFF for one turn even though the run is in bypass; that is the whole
reason the precedence is that way round and not the other.

Run vs message scope, honestly
------------------------------
In this runtime one run executes one user turn, so both scopes bind the same
run's effects. They differ in **stickiness**, which is a real behaviour and the
one the composer needs: a ``message`` selection is spent when that turn is sent
(the pill returns to Manual), a ``run`` selection persists across sends until
the user changes it. The resolver records which scope produced the answer so
the host, the ledger and any future audit read the same fact.

The bound that must hold
------------------------
Bypass applies **only inside a folder the user explicitly attached with write
permission**. An ungranted or ad-hoc path always asks, in every mode —
otherwise a single approval click silently becomes blanket write access to the
machine. :class:`FilesystemBypassBound` is that rule, expressed over the two
facts the caller already holds (is there an active grant, does it carry write),
plus reversibility (a delete is destructive and never auto-approves).

The bound is deliberately expressed over primitives rather than over
``WorkspaceGrantBinding``: this module is pure ``execution`` vocabulary, the
same layer as :mod:`agent_runtime.execution.depth`, and must not import a
capability package.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class _BypassContract(BaseModel):
    """``RuntimeContract``'s config, redeclared so this module stays a LEAF.

    ``AgentRuntimeContext`` (in ``execution.contracts``) carries a
    :class:`FilesystemBypassDecision`, and ``runtime_api.schemas.runs`` carries
    a :class:`FilesystemBypassSelection`. Importing ``RuntimeContract`` from
    ``execution.contracts`` here would close that into an import cycle that
    pydantic cannot build through. Three kwargs of duplication buys a module
    with no intra-package imports at all — the same shape
    ``execution.depth`` uses for the same reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class FilesystemBypassMode(StrEnum):
    """What the composer pill says. The string values are the wire contract.

    The api-types ``FilesystemBypassMode`` literal union must stay in lockstep.
    """

    MANUAL = "manual"
    BYPASS = "bypass"


class FilesystemBypassScope(StrEnum):
    """How long a selection lasts. See the module header on stickiness."""

    MESSAGE = "message"
    RUN = "run"


class FilesystemBypassSource(StrEnum):
    """Which tier produced the resolved mode — recorded, not inferred.

    ``MASTER_OFF`` is distinct from ``MASTER`` on purpose. Both resolve to
    ``manual``, but they mean different things: ``MASTER_OFF`` says a selection
    arrived and was refused because the Settings switch is off, which is a
    thing worth being able to see in a run record. ``MASTER`` says nobody
    selected anything.
    """

    MASTER_OFF = "master_off"
    MASTER = "master"
    RUN = "run"
    MESSAGE = "message"


class FilesystemBypassSelection(_BypassContract):
    """Both selectable tiers the composer can express, in ONE wire field.

    Two named slots rather than a list of ``(mode, scope)`` pairs: the field
    name IS the scope, so the payload cannot disagree with itself (no duplicate
    scopes, no pair whose ``scope`` contradicts where it was filed). ``None``
    means "not selected", which is deliberately NOT the same as
    ``manual`` — an explicit Manual pick at message scope must be able to
    override a sticky ``run`` bypass, and it can only do that if absence and
    Manual are distinguishable.
    """

    run: FilesystemBypassMode | None = None
    message: FilesystemBypassMode | None = None


class FilesystemBypassDecision(_BypassContract):
    """The sealed, per-run answer. Persisted with the run; never recomputed.

    ``offered`` mirrors tier 1 for the surfaces that need to know whether the
    control should have been available at all. It is NOT the same question as
    ``mode``: master can be on while the user leaves the pill on Manual.
    """

    master_enabled: bool = False
    mode: FilesystemBypassMode = FilesystemBypassMode.MANUAL
    source: FilesystemBypassSource = FilesystemBypassSource.MASTER

    @property
    def offered(self) -> bool:
        """Whether the run/message controls may be offered by a surface."""

        return self.master_enabled

    @property
    def skips_approval_pause(self) -> bool:
        """Whether a staged host write may be approved without a human.

        This is necessary, never sufficient — :class:`FilesystemBypassBound`
        still has to admit the specific target.
        """

        return self.mode is FilesystemBypassMode.BYPASS


#: The decision every run starts from, and the one a lane that cannot resolve
#: bypass must fall back to. Manual, master off, nothing offered.
MANUAL_FILESYSTEM_BYPASS: FilesystemBypassDecision = FilesystemBypassDecision()

#: Whether tier 1 OFFERS the control when a workspace has never said either way.
#:
#: Lives here, in the vocabulary module, because two layers have to agree about
#: it and they reach the setting by different routes. ``WorkspaceBehaviorOverrides``
#: (a pydantic model) applies it by CONSTRUCTION; ``RunCoordinator`` reads the
#: persisted JSONB blob as a plain dict, where a pydantic default does not exist.
#: When those two disagreed, the API and Settings reported the control as
#: offered while every run sealed ``master_enabled=False`` — a pill the user
#: could select that the server then silently ignored, with no feedback anywhere.
#: Measured on a fresh install, which is the case with no persisted row at all.
#:
#: ABSENT is not FALSE. An explicit ``False`` is an operator turning the control
#: off and must survive; only a missing key takes this default. Anything that
#: collapses the two re-creates the split.
DEFAULT_FILESYSTEM_BYPASS_OFFERED: bool = True


def filesystem_bypass_offered(stored: object) -> bool:
    """Resolve tier 1 from whatever a persistence layer happens to hold.

    ``None`` (or a missing key) means "never configured" and takes the
    deployment default; anything else is an explicit choice and is honoured as
    written. Callers pass ``blob.get("filesystem_bypass_enabled")`` rather than
    re-implementing this, so the absent-vs-false distinction exists once.
    """

    if stored is None:
        return DEFAULT_FILESYSTEM_BYPASS_OFFERED
    return stored is True


class FilesystemBypassResolver:
    """Fold the three tiers into one decision. Pure, total, no I/O."""

    @staticmethod
    def resolve(
        *,
        master_enabled: bool,
        selection: FilesystemBypassSelection | None = None,
    ) -> FilesystemBypassDecision:
        """Return the sealed decision for one run.

        ``master_enabled`` false wins over everything: a selection that arrives
        with the switch off is refused, not honoured, and the refusal is
        recorded as :attr:`FilesystemBypassSource.MASTER_OFF` so it is visible
        rather than silent.
        """

        picked = selection or FilesystemBypassSelection()

        if not master_enabled:
            offered_anyway = picked.message is not None or picked.run is not None
            return FilesystemBypassDecision(
                master_enabled=False,
                mode=FilesystemBypassMode.MANUAL,
                source=(
                    FilesystemBypassSource.MASTER_OFF
                    if offered_anyway
                    else FilesystemBypassSource.MASTER
                ),
            )

        for mode, source in (
            (picked.message, FilesystemBypassSource.MESSAGE),
            (picked.run, FilesystemBypassSource.RUN),
        ):
            if mode is None:
                continue
            return FilesystemBypassDecision(
                master_enabled=True,
                mode=mode,
                source=source,
            )

        # Master is on but nobody picked anything. The switch alone does NOT
        # turn bypass on — it only makes the control available. Anything else
        # would make flipping a Settings toggle silently auto-approve writes.
        return FilesystemBypassDecision(
            master_enabled=True,
            mode=FilesystemBypassMode.MANUAL,
            source=FilesystemBypassSource.MASTER,
        )


class FilesystemBypassTarget(_BypassContract):
    """The three facts the bound needs about one proposed host mutation.

    Supplied by the staging seam from the grant binding it already resolved,
    so the bound never has to look anything up and can be tested exhaustively.
    """

    #: An ACTIVE grant covers the target path. False for an ungranted or
    #: ad-hoc path — which must always ask, in every mode.
    granted: bool
    #: That grant carries write permission (not ``read_only``).
    writable: bool
    #: The effect is reversible. A delete is destructive: it never
    #: auto-approves, regardless of bypass, grant, or master switch.
    reversible: bool


class FilesystemBypassBound:
    """The one rule bypass may never widen past.

    Kept as its own type rather than an ``and`` in the caller because it is the
    security property of this feature: read the rule in one place, test the
    rule in one place, and make widening it a visible edit.
    """

    @staticmethod
    def permits(
        decision: FilesystemBypassDecision,
        target: FilesystemBypassTarget,
    ) -> bool:
        """Whether this decision may skip the pause for THIS target."""

        return (
            decision.skips_approval_pause
            and target.granted
            and target.writable
            and target.reversible
        )


__all__ = [
    "DEFAULT_FILESYSTEM_BYPASS_OFFERED",
    "MANUAL_FILESYSTEM_BYPASS",
    "FilesystemBypassBound",
    "FilesystemBypassDecision",
    "FilesystemBypassMode",
    "FilesystemBypassResolver",
    "FilesystemBypassScope",
    "FilesystemBypassSelection",
    "FilesystemBypassSource",
    "FilesystemBypassTarget",
    "filesystem_bypass_offered",
]
