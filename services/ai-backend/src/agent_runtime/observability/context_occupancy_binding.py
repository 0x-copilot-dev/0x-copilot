"""The run-scoped occupancy sink, installed independently of any feature mode.

Occupancy shipped reachable only through ``ModelInvocationRuntimeBinding``, and
that binding exists only when F10 (model reliability) is at ``SHADOW`` or
``ENFORCE``. ``FeatureModeSet.f10`` defaults to ``OFF``, so on a default
deployment ``ModelInvocationWorkerComposer.compose`` returns ``None``, no binding
is installed, and ``awrap_model_call`` returns at ``if binding is None`` before
any measurement runs. The ledger measured nothing, everywhere, and no test caught
it because every occupancy test injects a binding or a sink directly.

This module exists so the measurement lane has its **own** run-scoped binding,
with no F10 field on it and nothing to read a feature mode from. Two consequences
are deliberate:

- It is a separate binding rather than a widened F10 one. The alternative —
  emitting a partial ``ModelInvocationRuntimeBinding`` when F10 is OFF — would
  install an object that ``awrap_model_call`` reads as "F10 is on": it would
  drive authority preparation, attempt admission and journal appends from
  null-object collaborators, and write journal records on a deployment whose F10
  mode is OFF. The journal must stay byte-identical whether or not occupancy is
  measured; a binding that is *partly* F10 cannot promise that.
- It cannot be a constructor argument on the middleware.
  ``llm_seam_conformance`` pins the graph funnel's spelling — the root gets
  ``ModelInvocationMiddleware()`` and every child graph gets the *class itself*
  as a universal factory — so a child-graph middleware constructs itself with no
  arguments and could never be handed a sink. A run-scoped context slot is
  reachable from both, which is exactly why F10 and the F2 prompt runtime already
  use one.

The deferred-write lane is the other half of the contract. Design §6.4 requires
that measurement never raise into a model call *and* never add latency to it, and
on the file store (the desktop default) a persist is an ``fsync`` under the global
store lock. So the OFF path hands its write to :class:`DeferredContextOccupancyWrites`
and returns the provider's response immediately; the worker drains the lane when
the run ends, by which time nobody is waiting on a model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:  # pragma: no cover - typing only
    # Deferred for the same reason the model-invocation seam defers it: the
    # recorder module pulls the message classifier, which reads constants from
    # ``agent_runtime.capabilities``, and this module is imported from inside
    # that cycle's window. Only the *protocol* is needed here, and only as an
    # annotation, so nothing is imported at runtime.
    from agent_runtime.observability.context_occupancy_recorder import (
        ContextOccupancySink,
    )


_LOGGER = logging.getLogger(__name__)


class DeferredContextOccupancyWrites:
    """Run-scoped lane keeping occupancy writes off the model call's path.

    Awaiting a durable write before returning a provider response spends the
    user's latency on observability, and the store it writes to has no timeout
    (design §6.4, and the standing concern recorded against ``_persist_occupancy``).
    Scheduling instead costs the model call nothing measurable.

    Two properties make deferral safe rather than merely fast:

    - **Nothing is dropped silently.** Every scheduled task is held in a strong
      reference set until it finishes, so the event loop cannot garbage-collect a
      pending write mid-flight, and :meth:`drain` is what the run's teardown
      awaits before the store goes away.
    - **Nothing escapes.** The scheduled coroutine is already total by contract,
      and :meth:`drain` additionally collects exceptions rather than raising, so a
      degraded occupancy store cannot fail a run that has otherwise succeeded.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: set[asyncio.Task[None]] = set()

    def schedule(self, write: Coroutine[Any, Any, None]) -> None:
        """Start ``write`` in the background, or close it if there is no loop."""

        try:
            task = asyncio.get_running_loop().create_task(write)
        except RuntimeError:
            # No running loop means nothing can be deferred onto it. Closing the
            # coroutine explicitly is what keeps this from surfacing later as a
            # "coroutine was never awaited" warning attributed to the model call.
            write.close()
            _LOGGER.debug(
                "No running event loop for a deferred context occupancy write; "
                "dropping the measurement."
            )
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        """Await every outstanding write. Never raises.

        Loops rather than gathering once because a write scheduled while the
        drain is in flight would otherwise outlive it and reach a torn-down
        store.
        """

        while self._pending:
            outstanding = tuple(self._pending)
            await asyncio.gather(*outstanding, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class ContextOccupancyRuntimeBinding:
    """Where a run's occupancy rows go, and what window they are measured against.

    ``provider`` / ``model_family`` / ``context_window_tokens`` are the three
    facts the F10 path reads off the dispatched ``ModelRouteEntry`` and that no
    route exists to supply when F10 is OFF. They come from the run's own verified
    model profile instead, which on this path is exactly what was dispatched:
    without F10 there is no alternate-route recovery, so the primary profile is
    the only model the call can reach.

    ``model_family`` doubles as the tokenizer selector, so it must be the
    provider-native model name rather than a display label.
    """

    sink: ContextOccupancySink
    org_id: str
    provider: str
    model_family: str
    context_window_tokens: int | None = None
    deferred: DeferredContextOccupancyWrites = field(
        default_factory=DeferredContextOccupancyWrites
    )

    def __post_init__(self) -> None:
        if not self.org_id.strip():
            raise ValueError("context occupancy scope is incomplete")
        if not self.provider.strip() or not self.model_family.strip():
            raise ValueError("context occupancy model identity is incomplete")


__all__ = (
    "ContextOccupancyRuntimeBinding",
    "DeferredContextOccupancyWrites",
)
