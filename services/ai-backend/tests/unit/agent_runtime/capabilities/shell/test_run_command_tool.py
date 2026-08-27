"""The tool boundary: registration posture, and no dispatch without permission.

The boundary's whole job is ordering, so these tests are mostly about what did
NOT happen — the executor is a spy, and "the spy was never called" is the
assertion that a refusal actually stopped a process from starting.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.shell.config import (
    ShellCommandBudget,
    ShellExecutionConfig,
)
from agent_runtime.capabilities.shell.contracts import (
    RunCommandInput,
    ShellExecutionOutcome,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusalReason,
)
from agent_runtime.capabilities.shell.policy_gate import ShellCommandPolicyGate
from agent_runtime.capabilities.shell.run_command_tool import (
    TOOL_NAME,
    RunCommandToolFactory,
)
from tests.unit.agent_runtime.capabilities.shell._lanes import (
    WORKSPACE,
    FakeBinding,
    FakeGate,
    FakeNeverList,
    runtime_context,
)


@dataclass
class SpyExecutor:
    """Records requests; never spawns anything."""

    outcome: ShellExecutionOutcome = field(
        default_factory=lambda: ShellExecutionOutcome(
            status=ShellExecutionStatus.COMPLETED,
            exit_code=0,
            output="2 passed",
            duration_ms=12,
        )
    )
    requests: list[ShellExecutionRequest] = field(default_factory=list)

    async def run(
        self, request: ShellExecutionRequest, *, output_ref: str | None = None
    ) -> ShellExecutionOutcome:
        del output_ref
        self.requests.append(request)
        return self.outcome


def _config(**overrides: object) -> ShellExecutionConfig:
    return ShellExecutionConfig(
        enabled=True,
        default_timeout_s=120,
        max_timeout_s=600,
        combined_output_preview_bytes=64 * 1024,
        max_commands_per_run=64,
        shell_path="/bin/sh",
    ).model_copy(update=dict(overrides))


def _build(
    *,
    run_id: str = "run-tool",
    config: ShellExecutionConfig | None = None,
    binding: FakeBinding | None = None,
    never_list: FakeNeverList | None = None,
    gate: FakeGate | None = None,
    executor: SpyExecutor | None = None,
    budget: ShellCommandBudget | None = None,
    execute: str | None = None,
) -> tuple[StructuredTool | None, SpyExecutor, FakeGate]:
    spy = executor or SpyExecutor()
    approval = gate or FakeGate()
    tool = RunCommandToolFactory.build(
        config=config or _config(),
        binding=binding or FakeBinding(),  # type: ignore[arg-type]
        policy_gate=ShellCommandPolicyGate(
            runtime_context=runtime_context(run_id=run_id, execute=execute),
            never_list=never_list or FakeNeverList(),
            gate=approval,  # type: ignore[arg-type]
        ),
        budget=budget or ShellCommandBudget(64),
        executor=spy,  # type: ignore[arg-type]
        environment=None,
        env_source={"LANG": "en_GB.UTF-8", "HOME": "/Users/sarah"},
    )
    return tool, spy, approval


async def _call(tool: StructuredTool, **kwargs: object) -> dict[str, object]:
    payload = await tool.ainvoke({"command": "pytest -q", **kwargs})
    assert isinstance(payload, str)
    return json.loads(payload)


class TestRegistrationPosture:
    """§7.1 — any missing prerequisite yields no tool, not a weaker one."""

    def test_the_capability_flag_off_yields_no_tool(self) -> None:
        tool, _, _ = _build(config=_config(enabled=False))

        assert tool is None

    def test_no_command_capable_workspace_yields_no_tool(self) -> None:
        tool, _, _ = _build(binding=FakeBinding(sealed=()))

        assert tool is None

    def test_registration_reads_the_SEAL_not_the_live_view(self) -> None:
        """§7.4 — a workspace enabled mid-run cannot enter this run."""

        tool, _, _ = _build(binding=FakeBinding(sealed=(), live=(WORKSPACE,)))

        assert tool is None

    def test_the_built_tool_is_the_model_facing_contract(self) -> None:
        tool, _, _ = _build()

        assert tool is not None
        assert tool.name == TOOL_NAME
        assert tool.args_schema is RunCommandInput
        # The undo statement is in the schema text, not only in the prompt
        # block: a model that never reads the system prompt still sees it.
        assert "undone" in tool.description.lower()


class TestNothingRunsWithoutPermission:
    async def test_a_declined_command_never_reaches_the_executor(self) -> None:
        tool, spy, _ = _build(gate=FakeGate(approved=False))
        assert tool is not None

        result = await _call(tool)

        assert result["status"] == ShellExecutionStatus.REFUSED.value
        assert result["reason"] == ShellRefusalReason.COMMAND_DECLINED.value
        assert spy.requests == []

    async def test_a_never_listed_command_never_reaches_the_executor(self) -> None:
        tool, spy, gate = _build(
            never_list=FakeNeverList(screen_hits=frozenset({"pytest -q"})),
            execute="auto",
        )
        assert tool is not None

        result = await _call(tool)

        assert result["reason"] == ShellRefusalReason.COMMAND_NOT_PERMITTED.value
        assert spy.requests == []
        assert gate.parks == []

    async def test_an_approved_command_reaches_the_executor_once(self) -> None:
        tool, spy, gate = _build()
        assert tool is not None

        result = await _call(tool)

        assert result["status"] == ShellExecutionStatus.COMPLETED.value
        assert result["exit_code"] == 0
        assert result["workspace"] == WORKSPACE
        assert len(spy.requests) == 1
        assert len(gate.parks) == 1

    async def test_the_executed_command_is_the_approved_command(self) -> None:
        """The card and the process must never disagree (§4.2)."""

        tool, spy, gate = _build()
        assert tool is not None

        await tool.ainvoke({"command": "make test"})

        assert gate.parks[0]["command"] == "make test"
        assert spy.requests[0].command == "make test"


class TestWorkspaceResolution:
    async def test_an_unknown_label_is_refused_and_names_the_real_ones(self) -> None:
        tool, spy, _ = _build()
        assert tool is not None

        result = await _call(tool, workspace="not-attached")

        assert result["reason"] == ShellRefusalReason.UNKNOWN_WORKSPACE.value
        assert WORKSPACE in str(result["exit_note"])
        assert spy.requests == []

    async def test_a_withdrawn_grant_is_unavailable_not_a_fallback_root(self) -> None:
        """§7.2 — mid-run revocation, caught at call time."""

        tool, spy, _ = _build(binding=FakeBinding(sealed=(WORKSPACE,), live=()))
        assert tool is not None

        result = await _call(tool)

        assert result["status"] == ShellExecutionStatus.UNAVAILABLE.value
        assert spy.requests == []

    async def test_an_ambiguous_omitted_label_is_refused(self) -> None:
        tool, spy, _ = _build(binding=FakeBinding(sealed=(WORKSPACE, "notes")))
        assert tool is not None

        result = await _call(tool)

        assert result["reason"] == ShellRefusalReason.UNKNOWN_WORKSPACE.value
        assert spy.requests == []

    async def test_the_command_runs_in_the_bound_root(self) -> None:
        tool, spy, _ = _build(binding=FakeBinding(root=Path("/Users/sarah/code")))
        assert tool is not None

        await _call(tool)

        assert spy.requests[0].cwd == Path("/Users/sarah/code")
        # The result carries the LABEL; the host path is not in it.
        assert "/Users/sarah" not in json.dumps(spy.requests[0].command)


class TestDeploymentBounds:
    async def test_a_timeout_above_the_ceiling_is_refused_not_clamped(self) -> None:
        tool, spy, gate = _build()
        assert tool is not None

        result = await _call(tool, timeout_s=6000)

        assert result["reason"] == ShellRefusalReason.TIMEOUT_ABOVE_MAXIMUM.value
        assert spy.requests == []
        assert gate.parks == []

    async def test_an_exhausted_budget_refuses_before_a_human_is_asked(self) -> None:
        budget = ShellCommandBudget(1)
        budget.consume()
        tool, spy, gate = _build(budget=budget)
        assert tool is not None

        result = await _call(tool)

        assert result["reason"] == ShellRefusalReason.COMMAND_BUDGET_EXHAUSTED.value
        assert gate.parks == []
        assert spy.requests == []

    async def test_a_declined_command_does_not_spend_the_budget(self) -> None:
        budget = ShellCommandBudget(2)
        tool, _, _ = _build(gate=FakeGate(approved=False), budget=budget)
        assert tool is not None

        await _call(tool)

        assert budget.spent == 0

    async def test_the_environment_is_built_not_inherited(self) -> None:
        """§11.3 — the child gets an allowlist, and never the parent's map."""

        tool, spy, _ = _build()
        assert tool is not None

        await _call(tool)

        env = spy.requests[0].env
        assert env["LANG"] == "en_GB.UTF-8"
        # Allowlisted, so a name the builder was never told about cannot reach
        # the child even though it is in the source mapping.
        assert "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
        assert env["PATH"].startswith(str(Path("/tmp/project") / ".venv/bin"))
        assert env["TMPDIR"] == "/tmp/scratch"
        # Phase 1 keeps the user's REAL home (§11.5, a documented residual risk
        # closed in Phase 2), so this asserts the posture rather than wishing.
        assert env["HOME"] == "/Users/sarah"

    async def test_the_configured_shell_and_cap_are_used(self) -> None:
        tool, spy, _ = _build(config=_config(shell_path="/bin/dash"))
        assert tool is not None

        await _call(tool)

        assert spy.requests[0].shell_path == "/bin/dash"
        assert spy.requests[0].timeout_s == 120


class TestModelFacingResult:
    async def test_a_nonzero_exit_is_still_a_completed_run(self) -> None:
        tool, _, _ = _build(
            executor=SpyExecutor(
                outcome=ShellExecutionOutcome(
                    status=ShellExecutionStatus.COMPLETED,
                    exit_code=1,
                    output="FAILED tests/test_x.py",
                    duration_ms=8,
                )
            )
        )
        assert tool is not None

        result = await _call(tool)

        assert result["status"] == ShellExecutionStatus.COMPLETED.value
        assert result["exit_code"] == 1

    async def test_truncation_is_a_field_not_only_a_sentence(self) -> None:
        tool, _, _ = _build(
            executor=SpyExecutor(
                outcome=ShellExecutionOutcome(
                    status=ShellExecutionStatus.COMPLETED,
                    exit_code=0,
                    output="tail",
                    truncated=True,
                    output_total_bytes=4_200_000,
                    duration_ms=9,
                )
            )
        )
        assert tool is not None

        result = await _call(tool)

        assert result["truncated"] is True
        assert result["output_total_bytes"] == 4_200_000

    async def test_a_cancelled_command_keeps_its_partial_output(self) -> None:
        from agent_runtime.capabilities.shell.contracts import ShellCommandCancelled

        @dataclasses.dataclass
        class _Cancelling:
            async def run(self, request: object, *, output_ref: str | None = None):
                del request, output_ref
                raise ShellCommandCancelled(
                    ShellExecutionOutcome(
                        status=ShellExecutionStatus.CANCELLED,
                        output="partial",
                        duration_ms=3,
                    )
                )

        tool, _, _ = _build(executor=_Cancelling())  # type: ignore[arg-type]
        assert tool is not None

        result = await _call(tool)

        assert result["status"] == ShellExecutionStatus.CANCELLED.value
        assert result["output"] == "partial"

    @pytest.mark.parametrize("command", ["", "   ", "ls\x00 -la"])
    async def test_the_schema_refuses_before_any_decision(self, command: str) -> None:
        """Validation is LangChain's, above the gate — nothing is asked about it."""

        tool, spy, gate = _build()
        assert tool is not None

        with pytest.raises(Exception):
            await tool.ainvoke({"command": command})

        assert spy.requests == []
        assert gate.parks == []
