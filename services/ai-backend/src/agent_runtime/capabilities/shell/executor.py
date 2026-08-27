"""The one subprocess call site in ``agent_runtime`` (PRD-shell-execution §11.1-§11.2, §13).

Everything above this module decides *whether* a command may run. This module
only runs it, and bounds it. It makes no policy decision, resolves no path,
reads no environment variable and knows nothing about workspace labels — which
is what lets it be the single spawn point without also becoming the place every
rule accretes. §16.6 pins that with a guardrail test: no ``shell=True`` and no
``create_subprocess_shell`` anywhere in ``agent_runtime`` outside this file.

**What is bounded, and by what.** Every one of these has a defined outcome
rather than a hope:

============================  ==========================================================
Unbounded thing               Bound
============================  ==========================================================
A command that never exits    ``timeout_s`` -> the **process group** is killed, status
                              ``timeout``, ``exit_code=None``, partial output kept.
A command that ignores TERM   SIGTERM, a grace window, then SIGKILL to the group.
A command that spawns         ``start_new_session=True`` puts the whole tree in one
children                      group, so the kill reaches the children too.
A command that prints GBs     A ring buffer bounded at ``2 x cap`` -> memory never holds
                              more than that, whatever the process writes.
The spill file                ``spill_cap_bytes``. Beyond the PRD, which bounds only what
                              the model sees; without it "print a gigabyte" is bounded in
                              memory and unbounded on the user's disk.
A run that is cancelled       The group is killed and the partial output is carried out
                              on a typed exception rather than lost.
A command that never runs     A failed spawn is a typed ``unavailable`` refusal, never an
                              ``OSError`` reaching the model as a paraphrase.
============================  ==========================================================

**What is NOT bounded here, said plainly rather than half-built.** There is no
OS-level sandboxing in this phase. A command runs as the user, with the user's
permissions: it can read any file the user can read, write any file the user can
write, and reach the network. The bound working directory constrains where a
command *starts*, not where it can reach, and ``HOME`` is the user's real home
(§11.5). Those residual risks are documented in-product and deferred to Phase 2,
where the scratch ``HOME`` and SPIKE-S1's per-platform confinement live. Nothing
in this module should be read as claiming isolation it does not have.

**On killing the group rather than the child.** OpenCode sets ``detached: true``
and then never calls its own ``killTree`` helper, so whether its kill reaches the
group is unverified. Here the teardown is an explicit ``os.killpg`` escalation
that waits on the group, and the group id is checked against the worker's own
before any signal is sent — signalling our own process group would kill the
worker along with the command.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Final

from agent_runtime.capabilities.shell.contracts import (
    ShellCommandCancelled,
    ShellExecutionOutcome,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
)


class _Note:
    """Model-facing sentences for the failures this module can produce.

    Authored here, never interpolated from an exception: an ``OSError`` message
    can name a host path, and this result is model-visible.
    """

    SPAWN_FAILED: Final = (
        "The command could not be started. Nothing ran, and nothing changed."
    )
    TIMEOUT: Final = (
        "The command did not finish within {timeout}s and was stopped, along "
        "with anything it started. The output below is what it had produced by "
        "then. Re-run something shorter, or ask again with a larger timeout_s "
        "(up to the configured maximum)."
    )
    TRUNCATED: Final = (
        "...output truncated (kept the last {kept} of {total})...\n"
        "Full output: {ref}\n\n"
    )
    TRUNCATED_NO_REF: Final = (
        "...output truncated (kept the last {kept} of {total}; the rest was "
        "not retained)...\n\n"
    )


class _OutputCollector:
    """Bounded output accumulation: the tail in memory, the rest on disk.

    Three rules, each of which is load-bearing:

    * **Tail, not head.** The error is at the end of a build log, so the tail is
      what the model needs. This is the opposite of the generic head-first
      truncation used elsewhere; here there is one rule.
    * **Bounded at ``2 x cap``.** The ring is trimmed back to ``cap`` whenever it
      passes twice that, so a process writing a gigabyte never causes a gigabyte
      to be held. Trimming at ``2 x cap`` rather than at ``cap`` is what keeps
      the trim amortised instead of per-chunk. Stated exactly: after every
      ``feed`` the ring is at most ``2 x cap``, and its transient peak *during*
      one ``feed`` is that plus the chunk being appended — 64 KiB in the
      executor, whatever the process wrote.
    * **A codepoint is never split.** After clipping to the last ``cap`` bytes,
      the leading UTF-8 continuation bytes are walked past, so the tail starts
      at a character boundary rather than half-way through one.

    The spill file, when one is configured, receives the output from the first
    byte and stops at ``spill_cap_bytes``. So on an enormous output the model
    reads the true tail and the file holds the head — the two halves that are
    actually worth keeping — and the collector says so rather than implying the
    file is complete.
    """

    def __init__(
        self,
        *,
        cap_bytes: int,
        spill_path: Path | None = None,
        spill_cap_bytes: int = 0,
    ) -> None:
        self._cap = max(cap_bytes, 1)
        self._spill_path = spill_path
        self._spill_cap = spill_cap_bytes
        self._tail = bytearray()
        self._total = 0
        self._spilled = 0
        self._spill: BinaryIO | None = None
        self._spill_truncated = False
        self._spill_failed = False

    @property
    def total_bytes(self) -> int:
        """Every byte the process wrote, including the ones no longer held."""

        return self._total

    @property
    def truncated(self) -> bool:
        """Whether the model is seeing less than the process wrote."""

        return self._total > self._cap

    @property
    def spill_written(self) -> bool:
        """Whether a spill file was actually created and holds bytes."""

        return self._spilled > 0

    @property
    def spill_truncated(self) -> bool:
        """Whether the spill file hit its own ceiling and stopped growing."""

        return self._spill_truncated

    def feed(self, chunk: bytes) -> None:
        """Accept one read. Never grows unboundedly, whatever the chunk size."""

        if not chunk:
            return
        was_within_cap = self._total <= self._cap
        self._total += len(chunk)
        self._tail.extend(chunk)
        if was_within_cap and self._total > self._cap:
            # First overflow. The ring has not been trimmed yet (it only trims
            # above 2 x cap and everything before this chunk fitted in cap), so
            # it still holds the complete output from byte zero - which is
            # exactly what the spill file needs as its opening.
            self._open_spill()
            self._write_spill(bytes(self._tail))
        elif self._spill is not None:
            self._write_spill(chunk)
        if len(self._tail) > 2 * self._cap:
            del self._tail[: len(self._tail) - self._cap]

    def tail_text(self) -> str:
        """The decoded tail, inside the cap, never split mid-codepoint."""

        data = bytes(self._tail[-self._cap :])
        if self.truncated:
            data = self._skip_continuation_bytes(data)
        return data.decode("utf-8", errors="replace")

    def notice(self, output_ref: str | None) -> str:
        """The in-band truncation notice, or an empty string when nothing was cut.

        In the output string as well as in the structured fields, so a model
        that reads only ``output`` still learns that it was cut.
        """

        if not self.truncated:
            return ""
        kept = self._humanise(min(self._cap, self._total))
        total = self._humanise(self._total)
        if output_ref and self.spill_written:
            return _Note.TRUNCATED.format(kept=kept, total=total, ref=output_ref)
        return _Note.TRUNCATED_NO_REF.format(kept=kept, total=total)

    def close(self) -> None:
        """Release the spill handle. Safe to call more than once."""

        if self._spill is not None:
            with contextlib.suppress(OSError):
                self._spill.close()
            self._spill = None

    def _open_spill(self) -> None:
        if self._spill_path is None or self._spill_cap <= 0 or self._spill_failed:
            return
        try:
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)
            self._spill = self._spill_path.open("wb")
        except OSError:
            # A spill we cannot write is not a reason to fail the command: the
            # model still gets the tail and an honest "not retained" notice.
            self._spill = None
            self._spill_failed = True

    def _write_spill(self, data: bytes) -> None:
        if self._spill is None:
            return
        room = self._spill_cap - self._spilled
        if room <= 0:
            self._spill_truncated = True
            return
        if len(data) > room:
            data = data[:room]
            self._spill_truncated = True
        try:
            self._spill.write(data)
        except OSError:
            self.close()
            self._spill_failed = True
            return
        self._spilled += len(data)

    @staticmethod
    def _skip_continuation_bytes(data: bytes) -> bytes:
        """Advance past leading UTF-8 continuation bytes (``0b10xxxxxx``).

        A byte clip can land in the middle of a multi-byte character; without
        this walk the decoder would replace the orphaned tail bytes with U+FFFD
        and the model would read a corrupted first character.
        """

        index = 0
        limit = min(len(data), 4)
        while index < limit and (data[index] & 0xC0) == 0x80:
            index += 1
        return data[index:]

    @staticmethod
    def _humanise(size: int) -> str:
        """Byte counts for the in-band notice. Whole units, no false precision."""

        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KiB"
        return f"{size / (1024 * 1024):.1f} MiB"


class ShellCommandExecutor:
    """Spawns one command, bounds it, and reaps it. The only spawn point.

    Stateless between calls: one request in, one outcome out, no persistent
    shell and no ``cd`` that survives to the next call. The grace windows are
    constructor arguments so a test can compress them without touching the
    escalation logic itself.
    """

    #: How long a process gets to handle SIGTERM before SIGKILL.
    DEFAULT_SIGTERM_GRACE_S: Final = 5.0
    #: How long the group gets to disappear after SIGKILL before we stop waiting.
    DEFAULT_REAP_GRACE_S: Final = 2.0
    #: Read size. Large enough that a flooding process does not cost a syscall
    #: per line, small enough that the ring's overshoot stays trivial.
    DEFAULT_READ_CHUNK_BYTES: Final = 64 * 1024

    def __init__(
        self,
        *,
        sigterm_grace_s: float = DEFAULT_SIGTERM_GRACE_S,
        reap_grace_s: float = DEFAULT_REAP_GRACE_S,
        read_chunk_bytes: int = DEFAULT_READ_CHUNK_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sigterm_grace_s = sigterm_grace_s
        self._reap_grace_s = reap_grace_s
        self._read_chunk_bytes = read_chunk_bytes
        self._clock = clock

    async def run(
        self, request: ShellExecutionRequest, *, output_ref: str | None = None
    ) -> ShellExecutionOutcome:
        """Run one command to completion, timeout, or cancellation.

        ``output_ref`` is the *virtual* path the model will be given for the
        spill file, used only to compose the in-band truncation notice; the
        executor never mints it and never puts a host path in the output.

        Raises :class:`ShellRefusedError` when the process could not be started
        at all, and :class:`ShellCommandCancelled` — carrying the partial
        outcome — when the surrounding task is cancelled while the command is
        live.
        """

        started = self._clock()
        collector = _OutputCollector(
            cap_bytes=request.output_cap_bytes,
            spill_path=request.spill_path,
            spill_cap_bytes=request.spill_cap_bytes,
        )
        process = await self._spawn(request)
        # No await between the spawn and the try, so a cancellation cannot
        # arrive here and strand a live process outside the teardown path.
        group = self._process_group(process)
        reader = asyncio.create_task(self._drain(process, collector))
        try:
            status, exit_code = await self._await_completion(
                process, reader, group, request.timeout_s
            )
        except asyncio.CancelledError:
            # Converted into a typed domain signal rather than propagated, so
            # the partial output survives (AC5.2). The signals below are
            # synchronous, so the group dies even if the awaits here are
            # interrupted again. Whether to re-raise the cancellation is the
            # boundary's decision, not the executor's.
            reader.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._terminate(process, group)
            collector.close()
            raise ShellCommandCancelled(
                self._outcome(
                    ShellExecutionStatus.CANCELLED, None, collector, started, output_ref
                )
            ) from None
        finally:
            reader.cancel()
            # Suppressed broadly on purpose: this is cleanup running while
            # another exception may already be in flight, and a pipe error here
            # must not replace the outcome the caller is about to be given.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            collector.close()
        return self._outcome(status, exit_code, collector, started, output_ref)

    async def _spawn(
        self, request: ShellExecutionRequest
    ) -> asyncio.subprocess.Process:
        """The one ``create_subprocess_exec`` call in the service.

        ``-c`` with an explicit shell binary, never ``shell=True`` and never
        ``create_subprocess_shell``: the shell that interprets the command is
        the configured one, not whatever the platform picks, and it is not a
        login shell, so no rc file is sourced into the child.

        ``stdin=DEVNULL`` closes the door on interactive prompting and on
        password entry — which is also what makes ``sudo -S`` pointless as well
        as never-listed.
        """

        try:
            return await asyncio.create_subprocess_exec(
                request.shell_path,
                "-c",
                request.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(request.cwd),
                env=dict(request.env),
                start_new_session=os.name == "posix",
            )
        except (OSError, ValueError) as error:
            # A missing shell, an unusable cwd, or an environment value the exec
            # boundary rejects. The typed refusal keeps the OSError's text -
            # which can name a host path - away from the model.
            raise ShellRefusedError(
                ShellRefusal.unavailable(
                    ShellRefusalReason.EXECUTION_UNAVAILABLE, _Note.SPAWN_FAILED
                )
            ) from error

    async def _await_completion(
        self,
        process: asyncio.subprocess.Process,
        reader: asyncio.Task[None],
        group: int | None,
        timeout_s: int,
    ) -> tuple[ShellExecutionStatus, int | None]:
        """Wait for EOF and exit inside one budget, or kill the group.

        Draining and waiting are two separate waits against one shared deadline:
        a process can close stdout and keep running, and a process can exit
        while a child of it still holds the pipe open. Both are bounded.
        """

        deadline = self._clock() + timeout_s
        try:
            await asyncio.wait_for(reader, timeout=timeout_s)
            remaining = max(deadline - self._clock(), 0.0)
            exit_code = await asyncio.wait_for(process.wait(), timeout=remaining)
        except TimeoutError:
            await self._terminate(process, group)
            return ShellExecutionStatus.TIMEOUT, None
        return ShellExecutionStatus.COMPLETED, exit_code

    async def _drain(
        self, process: asyncio.subprocess.Process, collector: _OutputCollector
    ) -> None:
        """Read combined stdout+stderr until EOF, feeding the bounded collector.

        Reading continuously is not only about capture: a process whose pipe
        fills up blocks on write, so a tool that stopped reading would hang any
        command that printed more than a pipe buffer.
        """

        stream = process.stdout
        if stream is None:
            return
        while True:
            try:
                chunk = await stream.read(self._read_chunk_bytes)
            except (OSError, ValueError):
                # The pipe went away underneath us. That ends the capture; it
                # does not fail the command, and the bytes already collected
                # stay in the outcome.
                return
            if not chunk:
                return
            collector.feed(chunk)

    async def _terminate(
        self, process: asyncio.subprocess.Process, group: int | None
    ) -> None:
        """SIGTERM the group, wait, then SIGKILL it. The kill is guaranteed.

        The escalation is in a ``finally`` and the signals themselves are
        synchronous, so a cancellation arriving mid-teardown cannot leave the
        process group alive: SIGKILL is delivered before the awaited reap.
        """

        if process.returncode is not None:
            return
        self._signal(process, group, signal.SIGTERM)
        try:
            await self._wait_bounded(process, self._sigterm_grace_s)
        finally:
            if process.returncode is None:
                self._signal(process, group, signal.SIGKILL)
                with contextlib.suppress(asyncio.CancelledError):
                    await self._wait_bounded(process, self._reap_grace_s)

    async def _wait_bounded(
        self, process: asyncio.subprocess.Process, timeout_s: float
    ) -> None:
        """Wait for exit, giving up after ``timeout_s`` rather than hanging."""

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=timeout_s)

    def _signal(
        self,
        process: asyncio.subprocess.Process,
        group: int | None,
        number: signal.Signals,
    ) -> None:
        """Signal the whole process group, falling back to the direct child.

        ``ProcessLookupError`` is the ordinary case, not an error: the process
        finished between the check and the signal.
        """

        try:
            if group is not None:
                os.killpg(group, number)
                return
        except ProcessLookupError:
            return
        except OSError:
            pass
        with contextlib.suppress(ProcessLookupError, OSError, ValueError):
            process.send_signal(number)

    @staticmethod
    def _process_group(process: asyncio.subprocess.Process) -> int | None:
        """The child's process group, or ``None`` when it cannot be trusted.

        Two refusals to return a group, both deliberate: a platform without
        ``killpg`` (Windows, which Phase 1 does not target) and a group id equal
        to our own. The second is the one that matters — signalling the worker's
        own group would take down the run, the worker, and every other command
        with it.
        """

        if not hasattr(os, "killpg") or not hasattr(os, "getpgid"):
            return None
        try:
            group = os.getpgid(process.pid)
            if group == os.getpgid(0):
                return None
        except (ProcessLookupError, OSError):
            return None
        return group

    def _outcome(
        self,
        status: ShellExecutionStatus,
        exit_code: int | None,
        collector: _OutputCollector,
        started: float,
        output_ref: str | None,
    ) -> ShellExecutionOutcome:
        """Assemble the bounded, decoded result of one execution."""

        return ShellExecutionOutcome(
            status=status,
            exit_code=exit_code,
            output=collector.notice(output_ref) + collector.tail_text(),
            truncated=collector.truncated,
            output_total_bytes=collector.total_bytes,
            duration_ms=max(int((self._clock() - started) * 1000), 0),
            spill_truncated=collector.spill_truncated,
            spill_written=collector.spill_written,
        )

    @staticmethod
    def timeout_note(timeout_s: int) -> str:
        """The model-facing hint for a timed-out command, naming the value (AC5.4)."""

        return _Note.TIMEOUT.format(timeout=timeout_s)
