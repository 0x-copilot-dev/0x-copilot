"""The run-level wall clock — one budget for a whole execution of a run.

Deliberately NOT the same thing as ``ModelConfig.timeout_seconds``. That value
bounds a *single* invocation (and :mod:`agent_runtime.execution.depth` scales it
down per delegation depth), which means it re-arms every time the graph is
entered: a run that streams, parks on an approval, resumes, and streams again
gets the full per-call budget each time and has no ceiling of its own.

This object is that ceiling. It is a plain budget calculator plus one asyncio
scope, and it deliberately knows nothing about runs, events, or termination —
the worker owns the decision of what a blown deadline *means*. Keeping
``TerminationReason`` out of here is what lets ``agent_runtime.execution`` stay
below ``agent_runtime.api`` in the import graph.

Why the extra ``expired`` flag rather than just catching ``TimeoutError``: the
per-call timeout raises the very same exception from the very same ``await``.
Without a way to ask *which* scope fired, the run-level deadline would be
indistinguishable from a slow model call in the terminal event — the exact
"typed error collapses into a generic one" failure this exists to fix.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone


class RunDeadline:
    """A wall-clock budget for one run, anchored at the moment it started.

    ``seconds`` of ``None`` disables the deadline entirely: :meth:`scope`
    becomes a passthrough and :attr:`expired` stays ``False``. That is the shape
    a caller with no configured budget should build, rather than a very large
    number that pretends to be a limit.
    """

    __slots__ = ("_anchor", "_expired_before_start", "_scope", "_seconds")

    def __init__(
        self,
        *,
        seconds: float | None,
        anchor: datetime | None = None,
    ) -> None:
        if seconds is not None and seconds <= 0:
            msg = "RunDeadline seconds must be positive; use None to disable"
            raise ValueError(msg)
        self._seconds = seconds
        self._anchor = anchor
        self._scope: asyncio.Timeout | None = None
        self._expired_before_start = False

    @classmethod
    def disabled(cls) -> "RunDeadline":
        """Return a deadline that never fires."""

        return cls(seconds=None)

    @property
    def seconds(self) -> float | None:
        """The configured budget, or ``None`` when disabled."""

        return self._seconds

    def remaining_seconds(self, *, now: datetime | None = None) -> float | None:
        """Seconds left before the deadline; ``None`` when disabled.

        Clipped at zero — already-past reads as ``0.0``, never as a negative
        budget that :func:`asyncio.timeout` would interpret as "in the past" and
        act on only at the next suspension point.
        """

        if self._seconds is None:
            return None
        if self._anchor is None:
            return self._seconds
        moment = now or datetime.now(timezone.utc)
        anchor = self._anchor
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        elapsed = (moment - anchor).total_seconds()
        return max(0.0, self._seconds - elapsed)

    @property
    def expired(self) -> bool:
        """Whether *this* deadline is what cancelled the guarded body.

        ``False`` for a ``TimeoutError`` raised by an inner (per-call) timeout,
        which is the whole reason this flag exists.
        """

        if self._expired_before_start:
            return True
        return self._scope is not None and self._scope.expired()

    @asynccontextmanager
    async def scope(self, *, now: datetime | None = None) -> AsyncIterator[None]:
        """Run the guarded body under the remaining budget.

        Raises :class:`TimeoutError` — the same exception an inner
        :func:`asyncio.timeout` raises, so existing handlers keep working — and
        sets :attr:`expired` so the handler can tell the two apart.
        """

        remaining = self.remaining_seconds(now=now)
        if remaining is None:
            yield
            return
        if remaining <= 0:
            # Entering ``asyncio.timeout(0)`` would only fire at the body's
            # first suspension point, so a body that returns without awaiting
            # would sail past an already-blown deadline. Fail here instead.
            self._expired_before_start = True
            raise TimeoutError("run deadline already exceeded")
        async with asyncio.timeout(remaining) as scope:
            self._scope = scope
            yield


__all__ = ("RunDeadline",)
