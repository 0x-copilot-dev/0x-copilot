"""Executor tests (PRD-shell-execution §11.1-§11.2, §13, §16.3).

These spawn real processes on purpose. A fake subprocess would assert that our
code calls the functions we wrote, which is not the question — the question is
whether a command that never exits, one that ignores SIGTERM, one that spawns
children and one that prints far too much each end in a defined state on this
machine.

Everything here is bounded to about a second so the suite stays cheap; the
grace windows are compressed through the constructor rather than by changing
the escalation logic.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

from agent_runtime.capabilities.shell.contracts import (
    ShellCommandCancelled,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusalReason,
    ShellRefusedError,
)
from agent_runtime.capabilities.shell.environment import ShellEnvironmentBuilder
from agent_runtime.capabilities.shell.executor import (
    ShellCommandExecutor,
    _OutputCollector,
)

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="Phase 1 targets the POSIX desktop; Windows is deferred"
)


class ExecutorMixin:
    """A fast executor and a request builder bound to a real temp directory."""

    def executor(self, **overrides: float) -> ShellCommandExecutor:
        settings: dict[str, float] = {"sigterm_grace_s": 0.5, "reap_grace_s": 1.0}
        settings.update(overrides)
        return ShellCommandExecutor(**settings)  # type: ignore[arg-type]

    def request(
        self, command: str, cwd: Path, **overrides: object
    ) -> ShellExecutionRequest:
        payload: dict[str, object] = {
            "command": command,
            "cwd": cwd,
            "timeout_s": 10,
            "env": ShellEnvironmentBuilder().build(
                bound_root=cwd, scratch_dir=cwd, source={"HOME": str(cwd)}
            ),
            "shell_path": "/bin/sh",
            "output_cap_bytes": 64 * 1024,
        }
        payload.update(overrides)
        return ShellExecutionRequest.model_validate(payload)


@posix_only
class TestCompletion(ExecutorMixin):
    async def test_reports_the_exit_code_as_a_fact(self, tmp_path: Path) -> None:
        """AC1.3: the model reads failure from a field, not from prose."""

        outcome = await self.executor().run(self.request("exit 3", tmp_path))

        assert outcome.status is ShellExecutionStatus.COMPLETED
        assert outcome.exit_code == 3

    async def test_a_failing_command_is_still_completed(self, tmp_path: Path) -> None:
        """ "Completed" describes the process, not the outcome of the work."""

        outcome = await self.executor().run(
            self.request("echo '2 failed'; exit 1", tmp_path)
        )

        assert outcome.status is ShellExecutionStatus.COMPLETED
        assert outcome.exit_code == 1
        assert "2 failed" in outcome.output

    async def test_combines_stdout_and_stderr_in_order(self, tmp_path: Path) -> None:
        outcome = await self.executor().run(
            self.request("echo out; echo err 1>&2", tmp_path)
        )

        assert outcome.output == "out\nerr\n"

    async def test_runs_in_the_bound_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        workspace.mkdir()

        outcome = await self.executor().run(self.request("pwd", workspace))

        assert outcome.output.strip() == str(workspace.resolve())

    async def test_keeps_no_state_between_calls(self, tmp_path: Path) -> None:
        """§12.1: one command, one process, no ``cd`` that survives."""

        executor = self.executor()
        await executor.run(self.request("cd /", tmp_path))

        outcome = await executor.run(self.request("pwd", tmp_path))

        assert outcome.output.strip() == str(tmp_path.resolve())

    async def test_stdin_is_closed(self, tmp_path: Path) -> None:
        """No interactive prompting, and no password entry — which is what makes
        ``sudo -S`` pointless as well as never-listed."""

        outcome = await self.executor().run(
            self.request('read line; echo "[$line]"', tmp_path)
        )

        assert outcome.output.strip() == "[]"

    async def test_sources_no_rc_file(self, tmp_path: Path) -> None:
        """§11.4: a login shell would run arbitrary user code in the child.

        The profile is placed in the ``HOME`` the child is given; a login shell
        would source it and its marker would appear in the output.
        """

        (tmp_path / ".profile").write_text("echo RC-FILE-WAS-SOURCED\n")

        outcome = await self.executor().run(self.request("echo ok", tmp_path))

        assert outcome.output.strip() == "ok"

    async def test_records_a_duration(self, tmp_path: Path) -> None:
        outcome = await self.executor().run(self.request("true", tmp_path))

        assert outcome.duration_ms >= 0


@posix_only
class TestTimeout(ExecutorMixin):
    async def test_reports_timeout_with_no_exit_code(self, tmp_path: Path) -> None:
        """AC5.4: ``exit_code: null``, never the kill signal rendered as a code."""

        outcome = await self.executor().run(
            self.request("sleep 30", tmp_path, timeout_s=1)
        )

        assert outcome.status is ShellExecutionStatus.TIMEOUT
        assert outcome.exit_code is None

    async def test_preserves_the_partial_output(self, tmp_path: Path) -> None:
        outcome = await self.executor().run(
            self.request("echo early; sleep 30", tmp_path, timeout_s=1)
        )

        assert "early" in outcome.output

    async def test_the_hint_names_the_timeout_value(self) -> None:
        assert "45s" in ShellCommandExecutor.timeout_note(45)

    async def test_kills_the_whole_process_group(self, tmp_path: Path) -> None:
        """AC5.1/AC5.3: children of the command die with it, and none is orphaned.

        The command records its own pid, which under ``start_new_session=True``
        is the group id, then leaves a background child holding the group open.
        """

        pid_file = tmp_path / "pgid"
        command = f"echo $$ > {pid_file}; sleep 30 & sleep 30"

        outcome = await self.executor().run(
            self.request(command, tmp_path, timeout_s=1)
        )

        assert outcome.status is ShellExecutionStatus.TIMEOUT
        group = int(pid_file.read_text().strip())
        assert self._group_is_gone(group), (
            f"process group {group} still has members after teardown"
        )

    async def test_escalates_to_sigkill_when_sigterm_is_ignored(
        self, tmp_path: Path
    ) -> None:
        """AC5.3: an ignored SIGTERM costs the grace window, not the guarantee.

        ``trap '' TERM`` sets an *ignored* disposition, which is inherited across
        fork and exec — so nothing in the group dies until SIGKILL. The elapsed
        time is the evidence that the escalation actually ran.
        """

        grace = 0.5
        started = time.monotonic()

        outcome = await self.executor(sigterm_grace_s=grace).run(
            self.request(
                "trap '' TERM; while :; do sleep 0.05; done", tmp_path, timeout_s=1
            )
        )

        elapsed = time.monotonic() - started
        assert outcome.status is ShellExecutionStatus.TIMEOUT
        assert elapsed >= 1 + grace - 0.1
        assert elapsed < 6

    @staticmethod
    def _group_is_gone(group: int, deadline_s: float = 4.0) -> bool:
        """Poll until the group has no members. SIGKILL is not instantaneous."""

        until = time.monotonic() + deadline_s
        while time.monotonic() < until:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return True
            except PermissionError:  # pragma: no cover - foreign group id
                return True
            time.sleep(0.05)
        return False


@posix_only
class TestCancellation(ExecutorMixin):
    async def test_carries_the_partial_output_out_on_a_typed_exception(
        self, tmp_path: Path
    ) -> None:
        """AC5.2: cancelled records the output captured up to the cancellation."""

        task = asyncio.create_task(
            self.executor().run(
                self.request("echo before-cancel; sleep 30", tmp_path, timeout_s=30)
            )
        )
        await asyncio.sleep(0.4)
        task.cancel()

        with pytest.raises(ShellCommandCancelled) as error:
            await task

        outcome = error.value.outcome
        assert outcome.status is ShellExecutionStatus.CANCELLED
        assert outcome.exit_code is None
        assert "before-cancel" in outcome.output

    async def test_kills_the_group_on_cancellation(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "pgid"
        command = f"echo $$ > {pid_file}; sleep 30 & sleep 30"
        task = asyncio.create_task(
            self.executor().run(self.request(command, tmp_path, timeout_s=30))
        )
        await asyncio.sleep(0.4)
        task.cancel()

        with pytest.raises(ShellCommandCancelled):
            await task

        group = int(pid_file.read_text().strip())
        assert TestTimeout._group_is_gone(group)


@posix_only
class TestSpawnFailure(ExecutorMixin):
    async def test_a_missing_working_directory_is_a_typed_refusal(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ShellRefusedError) as error:
            await self.executor().run(self.request("true", tmp_path / "does-not-exist"))

        refusal = error.value.refusal
        assert refusal.status is ShellExecutionStatus.UNAVAILABLE
        assert refusal.reason is ShellRefusalReason.EXECUTION_UNAVAILABLE

    async def test_the_refusal_names_no_host_path(self, tmp_path: Path) -> None:
        """An ``OSError`` message can name a host path; the model must not see it."""

        missing = tmp_path / "does-not-exist"

        with pytest.raises(ShellRefusedError) as error:
            await self.executor().run(self.request("true", missing))

        assert str(missing) not in error.value.refusal.note

    async def test_a_missing_shell_is_the_same_typed_refusal(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ShellRefusedError) as error:
            await self.executor().run(
                self.request(
                    "true", tmp_path, shell_path=str(tmp_path / "no-such-shell")
                )
            )

        assert error.value.refusal.reason is ShellRefusalReason.EXECUTION_UNAVAILABLE


@posix_only
class TestEnvironmentIsolation(ExecutorMixin):
    """AC6.1/AC6.2 end-to-end: assert on captured stdout, not a constructed dict."""

    async def test_a_novel_secret_never_reaches_the_child(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The load-bearing one. Name-independent by construction.

        The variable name is generated at run time, so no allowlist, denylist or
        assertion anywhere in the tree can mention it, and the test cannot be
        defeated by a typo. It fails the moment anyone converts the allowlist to
        a denylist — the positive control below proves it can see a leak.
        """

        novel = f"ZZ_{uuid.uuid4().hex.upper()}"
        monkeypatch.setenv(novel, "novel-secret-value")
        built = ShellEnvironmentBuilder().build(
            bound_root=tmp_path, scratch_dir=tmp_path
        )

        outcome = await self.executor().run(self.request("env", tmp_path, env=built))

        assert novel not in outcome.output
        assert "novel-secret-value" not in outcome.output

    async def test_the_previous_assertion_can_actually_see_a_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control: with an inherited environment the secret DOES show.

        Without this, a green ``env`` assertion could mean "the allowlist works"
        or "``env`` printed nothing"; with it, the assertion is known to have
        teeth.
        """

        novel = f"ZZ_{uuid.uuid4().hex.upper()}"
        monkeypatch.setenv(novel, "novel-secret-value")

        outcome = await self.executor().run(
            self.request("env", tmp_path, env=dict(os.environ))
        )

        assert novel in outcome.output

    @pytest.mark.parametrize(
        "name",
        [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "DESKTOP_WORKSPACE_BROKER_TOKEN",
            "DESKTOP_BROKER_TOKEN",
            "ENTERPRISE_SERVICE_TOKEN",
            "ENTERPRISE_AUTH_SECRET",
        ],
    )
    async def test_the_named_secrets_of_today_are_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        """AC6.1 — a regression pin for the names we know, not the property.

        A name-dependent assertion is vacuously green if the name is wrong, so
        each of these is set to a marker value first: the test then proves the
        variable existed in the parent and still did not reach the child.
        """

        monkeypatch.setenv(name, f"marker-for-{name}")
        built = ShellEnvironmentBuilder().build(
            bound_root=tmp_path, scratch_dir=tmp_path
        )

        outcome = await self.executor().run(self.request("env", tmp_path, env=built))

        assert name not in outcome.output
        assert f"marker-for-{name}" not in outcome.output


@posix_only
class TestOutputBounding(ExecutorMixin):
    LINES = 500
    LINE_BYTES = 10  # "line-0001\n"

    def flood(self) -> str:
        return f"awk 'BEGIN{{for(i=1;i<={self.LINES};i++) printf \"line-%04d\\n\", i}}'"

    async def test_keeps_the_tail_not_the_head(self, tmp_path: Path) -> None:
        """AC8.1: the error is at the end of a build log."""

        outcome = await self.executor().run(
            self.request(self.flood(), tmp_path, output_cap_bytes=64)
        )

        assert outcome.truncated is True
        assert "line-0500" in outcome.output
        assert "line-0001" not in outcome.output

    async def test_reports_the_true_total(self, tmp_path: Path) -> None:
        outcome = await self.executor().run(
            self.request(self.flood(), tmp_path, output_cap_bytes=64)
        )

        assert outcome.output_total_bytes == self.LINES * self.LINE_BYTES

    async def test_the_notice_is_in_the_output_string(self, tmp_path: Path) -> None:
        """A model that reads only ``output`` still learns it was cut."""

        outcome = await self.executor().run(
            self.request(self.flood(), tmp_path, output_cap_bytes=64)
        )

        assert "output truncated" in outcome.output

    async def test_the_notice_names_the_spill_reference(self, tmp_path: Path) -> None:
        outcome = await self.executor().run(
            self.request(
                self.flood(),
                tmp_path,
                output_cap_bytes=64,
                spill_path=tmp_path / "spill.txt",
                spill_cap_bytes=1_000_000,
            ),
            output_ref="tool-results/run-1/command.txt",
        )

        assert "tool-results/run-1/command.txt" in outcome.output

    async def test_spills_the_full_output_to_the_given_path(
        self, tmp_path: Path
    ) -> None:
        spill = tmp_path / "spill.txt"

        outcome = await self.executor().run(
            self.request(
                self.flood(),
                tmp_path,
                output_cap_bytes=64,
                spill_path=spill,
                spill_cap_bytes=1_000_000,
            )
        )

        assert outcome.spill_written is True
        assert outcome.spill_truncated is False
        assert spill.stat().st_size == self.LINES * self.LINE_BYTES
        assert spill.read_text().startswith("line-0001\n")

    async def test_the_spill_file_is_itself_bounded(self, tmp_path: Path) -> None:
        """Beyond the PRD: without this, a gigabyte of output fills the disk."""

        spill = tmp_path / "spill.txt"

        outcome = await self.executor().run(
            self.request(
                self.flood(),
                tmp_path,
                output_cap_bytes=64,
                spill_path=spill,
                spill_cap_bytes=100,
            )
        )

        assert spill.stat().st_size == 100
        assert outcome.spill_truncated is True
        assert outcome.output_total_bytes == self.LINES * self.LINE_BYTES

    async def test_no_spill_path_means_the_overflow_is_counted_and_dropped(
        self, tmp_path: Path
    ) -> None:
        outcome = await self.executor().run(
            self.request(self.flood(), tmp_path, output_cap_bytes=64)
        )

        assert outcome.spill_written is False
        assert "not retained" in outcome.output

    async def test_output_at_exactly_the_cap_is_not_truncated(
        self, tmp_path: Path
    ) -> None:
        outcome = await self.executor().run(
            self.request("printf 'abcd'", tmp_path, output_cap_bytes=4)
        )

        assert outcome.truncated is False
        assert outcome.output == "abcd"


class TestOutputCollector:
    """The memory bound, exercised directly.

    A 500 MB flood through a real process would be a slow test of the same
    property; here the ring is fed half a gigabyte in a fraction of a second and
    the invariant is checked after every chunk.
    """

    def test_memory_stays_bounded_across_half_a_gigabyte(self) -> None:
        cap = 1024
        collector = _OutputCollector(cap_bytes=cap)
        chunk = b"x" * (1 << 20)

        for _ in range(512):
            collector.feed(chunk)
            assert len(collector._tail) <= 2 * cap

        assert collector.total_bytes == 512 << 20
        assert collector.truncated is True
        assert len(collector.tail_text()) <= cap

    def test_never_splits_a_multi_byte_codepoint(self) -> None:
        """AC8.3: the byte clip lands mid-character and must walk forward."""

        euro = "€".encode()  # three bytes
        collector = _OutputCollector(cap_bytes=100)  # not a multiple of three
        collector.feed(euro * 200)

        tail = collector.tail_text()

        assert "�" not in tail
        assert set(tail) == {"€"}

    def test_an_empty_stream_produces_an_empty_tail(self) -> None:
        collector = _OutputCollector(cap_bytes=64)

        assert collector.tail_text() == ""
        assert collector.truncated is False
        assert collector.notice(None) == ""

    def test_undecodable_bytes_do_not_raise(self) -> None:
        """A command may print a binary file; that must not fail the call."""

        collector = _OutputCollector(cap_bytes=64)
        collector.feed(b"\xff\xfe\x00\x01" * 4)

        assert isinstance(collector.tail_text(), str)

    def test_a_spill_that_cannot_be_written_is_not_fatal(self, tmp_path: Path) -> None:
        """The model still gets the tail and an honest "not retained" notice."""

        unwritable = tmp_path / "a-file" / "spill.txt"
        (tmp_path / "a-file").write_text("not a directory")
        collector = _OutputCollector(
            cap_bytes=8, spill_path=unwritable, spill_cap_bytes=1024
        )

        collector.feed(b"0123456789abcdef")

        assert collector.spill_written is False
        assert collector.truncated is True
        assert "not retained" in collector.notice(None)
