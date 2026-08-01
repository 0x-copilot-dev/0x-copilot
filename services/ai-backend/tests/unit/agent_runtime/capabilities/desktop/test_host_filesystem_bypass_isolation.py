"""Bypass may remove a PAUSE. It may never widen REACH.

WHAT THIS FILE USED TO SAY, AND WHY IT CHANGED. It used to forbid the rule
builder from learning about bypass at all, on the premise that there was
"exactly ONE host write lane — staged C3 -> ledger -> C2 commit" and that
plumbing the decision into ``HostFilesystemRules`` would grow a second write
path recording nothing.

Both halves of that premise are now false, and it is worth being exact about
which, because the guard was right when it was written:

* There is no longer one lane. A ``write_file`` inside a granted writable root
  lands bytes directly through ``FilesystemBackend(virtual_mode=False)``. That
  happened WITHOUT bypass, in the change that made writes work at all — so the
  feared second lane already exists and this file did not stop it.
* The staged lane is the one that does not run. It requires a C2 attestation an
  unpackaged build cannot produce by design, so on every desktop install to date
  it has written nothing. Guarding the pause on a lane that never pauses meant
  Manual and Bypass were byte-identical on the only writes that happen.

So the decision now reaches the rule layer deliberately, and what it may do
there is bounded. The bound is no longer "bypass is absent"; it is the matrix
below, in which bypass moves EXACTLY ONE CELL:

                          | Manual      | Bypass
    --------------------- + ----------- + -----------
    write, granted+writable | interrupt | allow      <- the only cell it moves
    write, granted read-only| deny      | deny
    write, ungranted        | deny      | deny
    read,  granted          | allow     | allow
    read,  ungranted        | interrupt | interrupt

Every row but the first is asserted in BOTH postures. If a later change lets
bypass touch rule 4 or rule 5 — the ungranted rows — it fails here, which is the
property the original tripwire was really protecting: no approval, and no
posture, can conjure access to a folder nobody attached.
"""

from __future__ import annotations

import pytest
from deepagents.middleware._fs_interrupt import (
    _build_interrupt_on_from_permissions,
)
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _check_fs_permission,
)

from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
    FilesystemBypassMode,
)

GRANTED = "/Users/ada/Projects"
UNGRANTED = "/Users/ada/Downloads"

MANUAL = MANUAL_FILESYSTEM_BYPASS
BYPASS = FilesystemBypassDecision(master_enabled=True, mode=FilesystemBypassMode.BYPASS)
#: Both postures, for the rows that must not differ between them.
BOTH = (MANUAL, BYPASS)


def _rules(
    *roots: GrantedRoot,
    bypass: FilesystemBypassDecision = MANUAL,
) -> list[FilesystemPermission]:
    return [
        FilesystemPermission(**rule)  # type: ignore[arg-type]
        for rule in HostFilesystemRules.build(roots, bypass=bypass)
    ]


@pytest.mark.parametrize("bypass", BOTH, ids=("manual", "bypass"))
def test_bypass_cannot_substitute_for_a_grant(
    bypass: FilesystemBypassDecision,
) -> None:
    """Zero grants is the first-run case, and it is `deny` in every posture.

    `deny` rather than `interrupt` is the load-bearing part: an ungranted write
    is never even a QUESTION, so there is no card whose approval could hand over
    write access to a folder the user never attached.
    """

    assert _check_fs_permission(_rules(bypass=bypass), "write", f"{GRANTED}/r.csv") == (
        "deny"
    )


@pytest.mark.parametrize("bypass", BOTH, ids=("manual", "bypass"))
def test_bypass_cannot_upgrade_a_read_only_grant(
    bypass: FilesystemBypassDecision,
) -> None:
    """The attach-time answer is the one bypass is not allowed to revisit.

    A read-only grant emits no write rule at all, so the write falls through to
    rule 5. Turning the pill on must not reinterpret "read-only" as "read-only
    unless you are in a hurry".
    """

    roots = (GrantedRoot(path=GRANTED, writable=False),)
    assert _check_fs_permission(
        _rules(*roots, bypass=bypass), "write", f"{GRANTED}/r.csv"
    ) == ("deny")


@pytest.mark.parametrize("bypass", BOTH, ids=("manual", "bypass"))
def test_bypass_changes_nothing_about_ungranted_reads(
    bypass: FilesystemBypassDecision,
) -> None:
    """Bypass is about writes the user already scoped, not about looking around."""

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    assert _check_fs_permission(
        _rules(*roots, bypass=bypass), "read", f"{UNGRANTED}/tax.pdf"
    ) == ("interrupt")


@pytest.mark.parametrize("bypass", BOTH, ids=("manual", "bypass"))
def test_bypass_changes_nothing_about_granted_reads(
    bypass: FilesystemBypassDecision,
) -> None:
    """Reading an attached folder never asks — that was the FS-G defect."""

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    assert _check_fs_permission(
        _rules(*roots, bypass=bypass), "read", f"{GRANTED}/notes.md"
    ) == ("allow")


def test_the_one_cell_bypass_moves() -> None:
    """A write inside a granted writable root: asks under Manual, runs under Bypass.

    This is the whole observable difference between the two postures, and the
    reason the composer pill exists. Before it, the decision was sealed onto the
    run and then read only by a lane that never executed, so both settings
    produced identical behaviour on every write that actually happened.
    """

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    target = f"{GRANTED}/report.csv"

    assert _check_fs_permission(_rules(*roots, bypass=MANUAL), "write", target) == (
        "interrupt"
    )
    assert _check_fs_permission(_rules(*roots, bypass=BYPASS), "write", target) == (
        "allow"
    )


class TestTheInterruptIsActuallyGenerated:
    """A rule that SAYS interrupt is not yet a rule that ASKS.

    The verdict tests above assert `_check_fs_permission`, which is the
    enforcement half. Whether a human is ever consulted is a second question,
    answered by deepagents' `_build_interrupt_on_from_permissions`: it emits an
    `interrupt_on` entry per filesystem tool, and only for tools whose OPERATION
    some interrupt-mode rule covers.

    That generator is worth pinning here rather than trusting, because it is the
    seam where this change could have gone wrong in the direction nobody would
    notice — a write that says `interrupt` but generates no entry would simply
    execute, silently, exactly as if Bypass were on.
    """

    @staticmethod
    def _tools(bypass: FilesystemBypassDecision) -> set[str]:
        roots = (GrantedRoot(path=GRANTED, writable=True),)
        return set(_build_interrupt_on_from_permissions(_rules(*roots, bypass=bypass)))

    def test_manual_gates_both_write_tools(self) -> None:
        assert {"write_file", "edit_file"} <= self._tools(MANUAL)

    def test_bypass_gates_neither_write_tool(self) -> None:
        """No entry at all — not an entry whose predicate happens to say no.

        Under Bypass rule 3 answers `allow`, so no interrupt-mode rule covers
        `write`, so the generator skips both tools entirely. The write runs
        without the middleware ever being consulted.
        """

        assert not ({"write_file", "edit_file"} & self._tools(BYPASS))

    @pytest.mark.parametrize("bypass", BOTH, ids=("manual", "bypass"))
    def test_the_bulk_read_tools_are_gated_in_both_postures(
        self, bypass: FilesystemBypassDecision
    ) -> None:
        """The regression this change could most easily have caused.

        `_make_bulk_when_predicate` fires when a search subtree OVERLAPS an
        interrupt rule, and rule 4 is anchored at `/` — which is why every `ls`
        once asked, including inside a folder the user had just attached.

        Adding a write interrupt anchored at a GRANTED root could have widened
        that again. It does not, because the generator filters interrupt rules
        by operation (`_fs_interrupt.py`), so a `write`-only rule is invisible to
        the read-scoped tools. These three stay gated by rule 4 alone, and the
        host-supplied `HostBulkReadScope` override is what actually confines
        them — see `factory._with_host_bulk_read_scope`.
        """

        assert {"ls", "glob", "grep"} <= self._tools(bypass)


def test_manual_is_what_a_caller_gets_by_default() -> None:
    """Forgetting to thread the decision must cost a prompt, not a silent write.

    The builder's default is the ASKING posture. Any future call site that adds
    the argument last, or drops it in a refactor, degrades toward asking.
    """

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    assert _check_fs_permission(_rules(*roots), "write", f"{GRANTED}/r.csv") == (
        "interrupt"
    )
