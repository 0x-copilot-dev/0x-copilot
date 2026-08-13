"""Queued steer command handling.

Cancel's sibling, and deliberately the smaller of the two.  The cancel handler
has to *make a run terminal* — status write, receipt, terminal event, budget
charge — because the command is the only thing that will.  A steer changes no
run state at all: the transcript fact was already appended by
``RunCoordinator.steer_run`` before the command was enqueued, so everything left
for this handler is the delivery, and delivery is a mailbox deposit.

That ordering is the seal rule (:mod:`agent_runtime.api.ledger_seal`) applied
rather than restated.  A ``run_steered`` event emitted *here* would race the
run's own terminal event: the steer claim runs concurrently with the run claim,
so an event appended on this side could land after the seal and become durable
but invisible — the exact failure the seal module was written about.  Appending
at accept time, under the coordinator's own non-terminal check, keeps the steer
inside the sealed prefix by construction.  This handler therefore emits nothing.
"""

from __future__ import annotations

import logging

from agent_runtime.api.ports import PersistencePort
from runtime_api.schemas import (
    ACTIVE_RUN_STATUSES,
    RuntimeSteerCommand,
)
from runtime_worker.run_cancellation import LiveRunRegistry


_LOGGER = logging.getLogger(__name__)


class RuntimeSteerHandler:
    """Deliver one queued user steer into the run executing in this process."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        live_runs: LiveRunRegistry | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        # The same registry cancellation joins through: a run is reachable for
        # steering exactly while this process is inside its claim.
        self._live_runs = live_runs

    async def handle(self, command: RuntimeSteerCommand) -> None:
        """Deposit the steer if the run is still ours to steer; otherwise no-op.

        Every early return is an *undeliverable* steer, never a failed command.
        Raising would send the claim back through retry, and a replayed steer is
        a message the user sent once being handed to the model twice — worse
        than the message arriving too late to matter.
        """

        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None or run.user_id != command.requested_by_user_id:
            return
        if run.status not in ACTIVE_RUN_STATUSES:
            # The run ended between accept and claim. The coordinator refuses
            # steers against terminal runs, so reaching here means the race, not
            # a bypass: the transcript already records that the user steered, and
            # there is no longer a model step to deliver it to.
            return
        if not self._deliver(command):
            _LOGGER.info(
                "steer not delivered in this process run_id=%s steer_id=%s",
                command.run_id,
                command.steer.steer_id,
            )

    def _deliver(self, command: RuntimeSteerCommand) -> bool:
        """Post the message to the run's mailbox; report whether it was taken.

        Two distinct falsy outcomes collapse here on purpose, because they are
        the same fact to the caller: the message is not going to reach a model
        step. Either this process is not executing the run (the ordinary
        multi-worker miss), or it is and the mailbox is already full — a user
        who typed seventeen corrections into one turn has been throttled, and
        the sixteen ahead of this one carry the intent.
        """

        if self._live_runs is None:
            return False
        inbox = self._live_runs.steering_for(command.run_id)
        if inbox is None:
            return False
        return inbox.deposit(command.steer)


__all__ = ("RuntimeSteerHandler",)
