"""Shared harness for the never-list suites (PRD-shell-execution §9, §16.2).

Both readers are exercised the way the runtime exercises them, because the two
answer differently and a suite that only asked one of them would be a suite over
half the mechanism:

* the **screen** is called on the raw command string, exactly as
  ``ShellCommandPolicyGate.authorize`` calls it before the PEP is entered;
* the **floor** is evaluated through ``PolicySubjects.of`` — the real subject
  builder, with the real 1024-character cap — against the same argument mapping
  ``ShellCommandPolicyGate._policy_arguments`` assembles. Matching a row against
  the bare command string instead would quietly test a subject the PDP never
  sees, and would hide the truncation property §16.2 asks us to pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from agent_runtime.capabilities.policy.contracts import CapabilityUrn
from agent_runtime.capabilities.policy.rules import (
    PermissionRuleset,
    PolicySubjects,
    RuleAction,
)
from agent_runtime.capabilities.shell.never_list import CommandNeverList


class NeverListProbeMixin:
    """Ask both readers about a command, the way the runtime asks them."""

    #: The capability URN the PDP is handed for a ``run_command`` call (§4.1).
    URN: Final = CapabilityUrn.for_builtin("shell", "run_command")
    WORKSPACE: Final = "my-project"
    #: The shape ``ShellCommandPolicyGate.grant_subject`` joins on.
    GRANT_SUBJECT: Final = "run_command@{label}: {command}"

    @property
    def never_list(self) -> CommandNeverList:
        return CommandNeverList()

    def subjects(self, command: str) -> tuple[str, ...]:
        """The real subject tuple for one ``run_command`` call."""

        return PolicySubjects.of(
            urn=self.URN,
            args={
                "command": command,
                "workspace": self.WORKSPACE,
                "grant_subject": self.GRANT_SUBJECT.format(
                    label=self.WORKSPACE, command=command
                ),
            },
        )

    def screen_refuses(self, command: str) -> bool:
        return self.never_list.screen(command) is not None

    def floor_refuses(self, command: str) -> bool:
        return self.verdict_of(self.never_list.floor(), command) is RuleAction.DENY

    def floor_rows_firing(self, command: str) -> tuple[str, ...]:
        """Which rows fired, so a failure names the row rather than a boolean."""

        subjects = self.subjects(command)
        return tuple(
            rule.pattern
            for rule in self.never_list.floor().rules
            if any(rule.matches(self.URN, subject) for subject in subjects)
        )

    def verdict_of(self, ruleset: PermissionRuleset, command: str) -> RuleAction | None:
        return ruleset.verdict(self.URN, self.subjects(command))

    def refused(self, command: str) -> bool:
        """True when either reader refuses — the runtime's effective answer."""

        return self.screen_refuses(command) or self.floor_refuses(command)


class DesktopSourceMixin:
    """Locate ``path-validation.ts``, the authority the Python table duplicates."""

    RELATIVE: Final = "apps/desktop/main/capabilities/path-validation.ts"

    @classmethod
    def path_validation_source(cls) -> Path | None:
        """The ``.ts`` file, or ``None`` when it is outside the checkout.

        Walks up rather than counting ``parents[n]`` so moving this file cannot
        silently turn the parity test into a skip. Returns ``None`` — and the
        test skips with a named reason — when the desktop app is not present,
        which is the case inside the ai-backend Docker build context. The guard
        that matters runs in repo CI, where the file is there.
        """

        for parent in Path(__file__).resolve().parents:
            candidate = parent / cls.RELATIVE
            if candidate.is_file():
                return candidate
        return None
