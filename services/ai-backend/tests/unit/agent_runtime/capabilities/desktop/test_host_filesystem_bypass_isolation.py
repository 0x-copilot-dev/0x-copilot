"""Bypass must never reach the deepagents permission layer.

There is exactly ONE host write lane — staged C3 -> ledger -> C2 commit — and
bypass only decides whether it pauses. The way that invariant dies is quietly:
somebody notices that ``write_file`` on a host path is denied, decides bypass
"should" relax it, plumbs the decision into ``HostFilesystemRules``, and the
product grows a second write path that records nothing. That is the same defect
shape as the two-workspace-backend one.

These tests are the tripwire. They are deliberately in their own module rather
than folded into ``test_host_filesystem.py``: the rule set is under active
change by other lanes, and this property is about what the rule builder must
NOT learn, not about what it currently says.
"""

from __future__ import annotations

import inspect

from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _check_fs_permission,
)

from agent_runtime.capabilities.desktop import host_filesystem
from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.execution import filesystem_bypass

GRANTED = "/Users/ada/Projects"


def _rules(*roots: GrantedRoot) -> list[FilesystemPermission]:
    return [
        FilesystemPermission(**rule)  # type: ignore[arg-type]
        for rule in HostFilesystemRules.build(roots)
    ]


def test_the_rule_builder_cannot_be_told_about_bypass() -> None:
    """No bypass input exists, so no rule can depend on one.

    Asserted as "no parameter mentions bypass" rather than as an exact
    signature, so a legitimate change by another lane (a new ``roots``-shaped
    input) does not fail here while the thing this guards against still does.
    """

    parameters = inspect.signature(HostFilesystemRules.build).parameters
    assert not [name for name in parameters if "bypass" in name.lower()]


def test_the_permission_module_does_not_import_the_bypass_vocabulary() -> None:
    """The two modules stay strangers.

    ``filesystem_bypass`` is importable from anywhere; what must not happen is
    the deepagents rule layer growing a dependency on it. A source-level check
    is crude but it is exactly the edit that would introduce the second lane,
    and it fails on the commit that makes it rather than on the incident.
    """

    source = inspect.getsource(host_filesystem)
    for name in filesystem_bypass.__all__:
        assert name not in source, (
            f"{name} reached the deepagents permission layer; host writes must "
            "stay on the staged C3 -> ledger -> C2 lane"
        )


def test_a_granted_writable_root_still_denies_a_direct_host_write() -> None:
    """The rule bypass is most likely to be asked to relax. It does not.

    A fully granted, writable root, judged by deepagents' OWN matcher: a direct
    ``write_file`` outside the agent's ``.copilot`` scratch directory is
    ``deny``, not ``interrupt`` and not ``allow``. An approval on a filesystem
    interrupt must never be able to authorize a host mutation, so the mutation
    verdict has to be a refusal rather than a question.
    """

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    assert (
        _check_fs_permission(_rules(*roots), "write", f"{GRANTED}/report.csv") == "deny"
    )
    # …and with zero grants, which is the first-run case.
    assert _check_fs_permission(_rules(), "write", f"{GRANTED}/report.csv") == "deny"


def test_an_ungranted_read_still_asks_with_a_writable_grant_present() -> None:
    """Bypass changes nothing about what a run may READ without consent."""

    roots = (GrantedRoot(path=GRANTED, writable=True),)
    assert (
        _check_fs_permission(_rules(*roots), "read", "/Users/ada/Downloads/tax.pdf")
        == "interrupt"
    )
