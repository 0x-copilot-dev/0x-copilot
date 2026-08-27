"""Shared fakes for the ``run_command`` decision-path tests.

Everything here is a *narrowing* stand-in: each fake either answers exactly what
the real collaborator answers or refuses. None of them can widen a decision, so
a test that passes with one of these in place is not passing because the fake
was permissive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.capabilities.policy.rules import (
    PermissionRule,
    PermissionRuleset,
    RuleAction,
)
from agent_runtime.capabilities.shell.contracts import (
    ShellRefusal,
    ShellRefusalReason,
)
from agent_runtime.capabilities.shell.run_command_tool import (
    BoundWorkspace,
    WorkspaceBindingView,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassDecision,
    FilesystemBypassMode,
)
from agent_runtime.surfaces_v2.gate import GateResume

WORKSPACE = "project"


def runtime_context(
    *,
    run_id: str,
    execute: str | None = None,
    bypass: bool = False,
    policies: dict[str, object] | None = None,
) -> AgentRuntimeContext:
    """A run context with one authored axis and one posture.

    ``execute`` is written where the backend's aggregate writes it —
    ``user_policies_json['tool_use']['workspace']`` — so the test drives the
    same read ``ToolUsePolicyResolver`` performs in production rather than
    injecting a pre-folded snapshot.
    """

    user_policies: dict[str, object] = dict(policies or {})
    if execute is not None:
        user_policies.setdefault("tool_use", {})
        tool_use = user_policies["tool_use"]
        assert isinstance(tool_use, dict)
        tool_use.setdefault("workspace", {})
        workspace = tool_use["workspace"]
        assert isinstance(workspace, dict)
        workspace["execute"] = execute
    return AgentRuntimeContext(
        model_profile=ModelConfig(
            provider="Fake",
            model_name="fake-enterprise-model",
            max_input_tokens=128_000,
            timeout_seconds=30,
            temperature=0,
            supports_streaming=True,
        ),
        user_id="user-sarah",
        org_id="org-acme",
        run_id=run_id,
        roles=frozenset({"member"}),
        permission_scopes=frozenset(),
        connector_scopes={},
        user_policies_json=user_policies,
        filesystem_bypass=FilesystemBypassDecision(
            master_enabled=bypass,
            mode=(
                FilesystemBypassMode.BYPASS if bypass else FilesystemBypassMode.MANUAL
            ),
        ),
    )


@dataclass
class FakeNeverList:
    """A tokenising never-list, reduced to the three judgements the PEP asks for.

    ``screen_hits`` is exact-match rather than glob so a test cannot accidentally
    depend on pattern semantics the real table owns.
    """

    screen_hits: frozenset[str] = frozenset()
    floor_patterns: tuple[str, ...] = ()
    grant_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    screened: list[str] = field(default_factory=list)

    def screen(self, command: str) -> ShellRefusal | None:
        self.screened.append(command)
        if command in self.screen_hits:
            return ShellRefusal.refused(
                ShellRefusalReason.COMMAND_NOT_PERMITTED,
                "This command is not permitted in this workspace.",
            )
        return None

    def floor(self) -> PermissionRuleset:
        return PermissionRuleset(
            rules=tuple(
                PermissionRule(pattern=pattern, action=RuleAction.DENY)
                for pattern in self.floor_patterns
            )
        )

    def always_grant_patterns(self, command: str) -> tuple[str, ...]:
        return self.grant_patterns.get(command, ())


@dataclass
class FakeGate:
    """Records every park and answers with a scripted human decision."""

    approved: bool = True
    decision_scope: str | None = "once"
    parks: list[dict[str, object]] = field(default_factory=list)

    async def park_command_for_approval(
        self,
        *,
        command: str,
        workspace_label: str | None,
        approval_id: str,
        simple_command: bool,
    ) -> GateResume:
        self.parks.append(
            {
                "command": command,
                "workspace_label": workspace_label,
                "approval_id": approval_id,
                "simple_command": simple_command,
            }
        )
        return GateResume(approved=self.approved, decision_scope=self.decision_scope)


@dataclass
class FakeBinding:
    """A workspace binding whose seal and live view can be moved apart."""

    sealed: tuple[str, ...] = (WORKSPACE,)
    live: tuple[str, ...] | None = None
    root: Path = Path("/tmp/project")
    scratch: Path = Path("/tmp/scratch")

    def sealed_labels(self) -> tuple[str, ...]:
        return self.sealed

    async def resolve(self, label: str | None) -> WorkspaceBindingView:
        labels = self.sealed if self.live is None else self.live
        if not labels:
            return WorkspaceBindingView()
        chosen = labels[0] if label is None and len(labels) == 1 else label
        if chosen not in labels:
            return WorkspaceBindingView(labels=labels)
        return WorkspaceBindingView(
            labels=labels,
            workspace=BoundWorkspace(
                label=chosen, root=self.root, scratch_dir=self.scratch
            ),
        )
