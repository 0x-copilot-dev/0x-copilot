"""Registration: the four prerequisites, and OFF unless every one of them holds.

This is the file that answers "is the capability on for everyone the moment it
lands". It is not: each test below turns exactly one prerequisite off and
asserts no tool is built, so a future edit that folds two of them together
fails here rather than in a packaged app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.desktop.workspace_backend import WorkspaceMount
from agent_runtime.capabilities.shell.run_command_tool import (
    TOOL_NAME,
    BoundWorkspace,
)
from runtime_worker.shell_composition import (
    BrokerWorkspaceBinding,
    ShellWorkerBundle,
    _resolve_never_list,
    command_capable_workspaces,
)
from tests.unit.agent_runtime.capabilities.shell._lanes import (
    FakeGate,
    FakeNeverList,
    runtime_context,
)

_ROOT = "/Users/sarah/code/project"
_DESKTOP_ENV = {
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    "RUNTIME_ENABLE_SHELL_EXECUTION": "true",
    "COPILOT_HOME": "",
}


def _mount(
    *,
    name: str = "project",
    mode: str = "read_write",
    shell_enabled: bool = True,
    host_root: str | None = _ROOT,
) -> WorkspaceMount:
    return WorkspaceMount(
        name=name,
        grant_id=f"grant-{name}",
        label=name,
        mode=mode,  # type: ignore[arg-type]
        host_root=host_root,
        shell_enabled=shell_enabled,
    )


async def _compose(
    *,
    mounts: tuple[WorkspaceMount, ...] = (),
    env: dict[str, str] | None = None,
    gate: FakeGate | None = None,
    tmp_path: Path | None = None,
) -> StructuredTool | None:
    source = dict(_DESKTOP_ENV if env is None else env)
    if tmp_path is not None:
        source["COPILOT_HOME"] = str(tmp_path)

    async def _provider() -> tuple[WorkspaceMount, ...]:
        return mounts

    return await ShellWorkerBundle.compose(
        runtime_context=runtime_context(run_id="run-compose"),
        conversation_id="conv-1",
        gate=gate or FakeGate(),  # type: ignore[arg-type]
        mounts_provider=_provider,
        env=source,
        never_list=FakeNeverList(),
    )


class TestOffByDefault:
    async def test_no_env_at_all_builds_nothing(self, tmp_path: Path) -> None:
        assert await _compose(mounts=(_mount(),), env={}, tmp_path=tmp_path) is None

    async def test_a_hosted_deployment_builds_nothing(self, tmp_path: Path) -> None:
        """§7.1.4 — a command runs on a machine; a hosted profile has none."""

        env = dict(_DESKTOP_ENV) | {"ENTERPRISE_DEPLOYMENT_PROFILE": "enterprise"}

        assert await _compose(mounts=(_mount(),), env=env, tmp_path=tmp_path) is None

    async def test_the_deployment_flag_off_builds_nothing(self, tmp_path: Path) -> None:
        env = dict(_DESKTOP_ENV) | {"RUNTIME_ENABLE_SHELL_EXECUTION": "false"}

        assert await _compose(mounts=(_mount(),), env=env, tmp_path=tmp_path) is None

    async def test_no_attached_workspace_builds_nothing(self, tmp_path: Path) -> None:
        assert await _compose(mounts=(), tmp_path=tmp_path) is None

    async def test_a_workspace_without_the_user_toggle_builds_nothing(
        self, tmp_path: Path
    ) -> None:
        """§7.3 — the per-workspace enablement is a prerequisite, not a hint."""

        assert (
            await _compose(mounts=(_mount(shell_enabled=False),), tmp_path=tmp_path)
            is None
        )

    async def test_a_read_only_workspace_builds_nothing(self, tmp_path: Path) -> None:
        """Writable AND shell-enabled: two authorities, neither implying the other."""

        assert (
            await _compose(mounts=(_mount(mode="read_only"),), tmp_path=tmp_path)
            is None
        )

    async def test_a_grant_snapshot_that_cannot_be_read_builds_nothing(
        self, tmp_path: Path
    ) -> None:
        async def _explode() -> tuple[WorkspaceMount, ...]:
            raise RuntimeError("broker unreachable")

        tool = await ShellWorkerBundle.compose(
            runtime_context=runtime_context(run_id="run-broker-down"),
            conversation_id="conv-1",
            gate=FakeGate(),  # type: ignore[arg-type]
            mounts_provider=_explode,
            env=dict(_DESKTOP_ENV) | {"COPILOT_HOME": str(tmp_path)},
            never_list=FakeNeverList(),
        )

        assert tool is None

    async def test_a_missing_never_list_builds_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A command lane without its §9.3 screen is not shipped at all."""

        import runtime_worker.shell_composition as composition

        monkeypatch.setattr(composition, "_resolve_never_list", lambda: None)

        tool = await ShellWorkerBundle.compose(
            runtime_context=runtime_context(run_id="run-no-screen"),
            conversation_id="conv-1",
            gate=FakeGate(),  # type: ignore[arg-type]
            mounts_provider=_mounts_provider((_mount(),)),
            env=dict(_DESKTOP_ENV) | {"COPILOT_HOME": str(tmp_path)},
        )

        assert tool is None

    def test_the_never_list_contract_is_the_three_judgements(self) -> None:
        """Whatever the loader returns must satisfy the port, or be ``None``.

        Stable in both worlds: before the never-list module lands this asserts
        the fail-closed ``None``; after it lands it asserts the object actually
        implements the screen, the floor and the grant-pattern judgement, which
        is the contract ``ShellCommandPolicyGate`` was written against.
        """

        resolved = _resolve_never_list()

        if resolved is None:
            return
        assert callable(resolved.screen)
        assert callable(resolved.floor)
        assert callable(resolved.always_grant_patterns)


def _mounts_provider(mounts: tuple[WorkspaceMount, ...]):
    async def _provider() -> tuple[WorkspaceMount, ...]:
        return mounts

    return _provider


class TestOnWhenEveryPrerequisiteHolds:
    async def test_the_tool_is_built_and_model_facing(self, tmp_path: Path) -> None:
        tool = await _compose(mounts=(_mount(),), tmp_path=tmp_path)

        assert tool is not None
        assert tool.name == TOOL_NAME

    async def test_only_the_capable_workspaces_are_sealed(self, tmp_path: Path) -> None:
        mounts = (
            _mount(name="project"),
            _mount(name="notes", shell_enabled=False),
            _mount(name="readonly", mode="read_only"),
        )
        workspaces = command_capable_workspaces(mounts, scratch_dir=tmp_path)

        assert [workspace.label for workspace in workspaces] == ["project"]

    def test_an_unusable_root_is_dropped_not_guessed(self, tmp_path: Path) -> None:
        assert (
            command_capable_workspaces((_mount(host_root=None),), scratch_dir=tmp_path)
            == ()
        )


class TestTheComposedToolReachesADecision:
    """End to end through the wire this module exists to build.

    The composition tests above prove the tool is or is not built; this one
    proves the object it hands back is bound to the PEP — a tool that composed
    correctly and dispatched without a decision would pass every other test in
    this file.
    """

    async def test_a_composed_command_is_asked_about_and_declined(
        self, tmp_path: Path
    ) -> None:
        gate = FakeGate(approved=False)
        tool = await _compose(mounts=(_mount(),), gate=gate, tmp_path=tmp_path)
        assert tool is not None

        result = json.loads(await tool.ainvoke({"command": "pytest -q"}))

        assert result["status"] == "refused"
        assert result["reason"] == "command_declined"
        assert len(gate.parks) == 1
        assert gate.parks[0]["command"] == "pytest -q"
        assert gate.parks[0]["workspace_label"] == "project"

    async def test_the_composed_tool_screens_before_it_asks(
        self, tmp_path: Path
    ) -> None:
        gate = FakeGate()
        screen = FakeNeverList(screen_hits=frozenset({"sudo rm -rf /"}))

        async def _provider() -> tuple[WorkspaceMount, ...]:
            return (_mount(),)

        tool = await ShellWorkerBundle.compose(
            runtime_context=runtime_context(run_id="run-composed-screen"),
            conversation_id="conv-1",
            gate=gate,  # type: ignore[arg-type]
            mounts_provider=_provider,
            env=dict(_DESKTOP_ENV) | {"COPILOT_HOME": str(tmp_path)},
            never_list=screen,
        )
        assert tool is not None

        result = json.loads(await tool.ainvoke({"command": "sudo rm -rf /"}))

        assert result["reason"] == "command_not_permitted"
        assert screen.screened == ["sudo rm -rf /"]
        assert gate.parks == []


class TestTheDefaultWiring:
    """No injected screen: the composition resolves the SHIPPED never-list.

    Every other test in this file injects a fake so it can isolate one
    prerequisite. This one deliberately does not — it is the check that the
    production wire is complete, and it is the one that would fail if the
    never-list module were renamed, moved, or given a constructor argument.
    """

    async def test_the_shipped_screen_is_the_one_that_is_bound(
        self, tmp_path: Path
    ) -> None:
        gate = FakeGate()
        tool = await ShellWorkerBundle.compose(
            runtime_context=runtime_context(run_id="run-real-screen"),
            conversation_id="conv-1",
            gate=gate,  # type: ignore[arg-type]
            mounts_provider=_mounts_provider((_mount(),)),
            env=dict(_DESKTOP_ENV) | {"COPILOT_HOME": str(tmp_path)},
        )
        assert tool is not None

        refused = json.loads(await tool.ainvoke({"command": "sudo rm -rf /"}))

        assert refused["status"] == "refused"
        assert refused["reason"] == "command_not_permitted"
        # AC7.1 — an unappealable refusal drew no card.
        assert gate.parks == []

    async def test_an_ordinary_command_still_asks(self, tmp_path: Path) -> None:
        gate = FakeGate(approved=False)
        tool = await ShellWorkerBundle.compose(
            runtime_context=runtime_context(run_id="run-real-ask"),
            conversation_id="conv-1",
            gate=gate,  # type: ignore[arg-type]
            mounts_provider=_mounts_provider((_mount(),)),
            env=dict(_DESKTOP_ENV) | {"COPILOT_HOME": str(tmp_path)},
        )
        assert tool is not None

        result = json.loads(await tool.ainvoke({"command": "pytest -q"}))

        assert result["reason"] == "command_declined"
        assert len(gate.parks) == 1
        # The shipped tokeniser vouched for a simple command, so the human was
        # offered the run-scoped "always" control.
        assert gate.parks[0]["simple_command"] is True


class TestTheSealAndTheRecheck:
    """§7.2 / §7.4 — enabling mid-run does nothing; disabling mid-run bites."""

    async def test_a_workspace_enabled_mid_run_cannot_enter(
        self, tmp_path: Path
    ) -> None:
        binding = BrokerWorkspaceBinding(
            sealed_labels=("project",),
            mounts_provider=_mounts_provider(
                (_mount(name="project"), _mount(name="new-folder"))
            ),
            scratch_dir=tmp_path,
        )

        view = await binding.resolve("new-folder")

        assert view.labels == ("project",)
        assert view.workspace is None

    async def test_a_workspace_disabled_mid_run_disappears(
        self, tmp_path: Path
    ) -> None:
        binding = BrokerWorkspaceBinding(
            sealed_labels=("project",),
            mounts_provider=_mounts_provider((_mount(shell_enabled=False),)),
            scratch_dir=tmp_path,
        )

        view = await binding.resolve("project")

        assert view.labels == ()
        assert view.workspace is None

    async def test_an_unreachable_broker_narrows_to_nothing(
        self, tmp_path: Path
    ) -> None:
        async def _explode() -> tuple[WorkspaceMount, ...]:
            raise RuntimeError("broker unreachable")

        binding = BrokerWorkspaceBinding(
            sealed_labels=("project",),
            mounts_provider=_explode,
            scratch_dir=tmp_path,
        )

        view = await binding.resolve("project")

        assert view.labels == () and view.workspace is None

    async def test_a_sole_workspace_resolves_without_being_named(
        self, tmp_path: Path
    ) -> None:
        binding = BrokerWorkspaceBinding(
            sealed_labels=("project",),
            mounts_provider=_mounts_provider((_mount(),)),
            scratch_dir=tmp_path,
        )

        view = await binding.resolve(None)

        assert view.workspace == BoundWorkspace(
            label="project", root=Path(_ROOT), scratch_dir=tmp_path
        )

    async def test_two_workspaces_are_not_chosen_between(self, tmp_path: Path) -> None:
        binding = BrokerWorkspaceBinding(
            sealed_labels=("project", "notes"),
            mounts_provider=_mounts_provider(
                (_mount(name="project"), _mount(name="notes"))
            ),
            scratch_dir=tmp_path,
        )

        view = await binding.resolve(None)

        assert view.labels == ("project", "notes")
        assert view.workspace is None
