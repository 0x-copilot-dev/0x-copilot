"""Worker composition for replay-safe F10 metric projection.

This deliberately keeps the metrics projector out of the journal handler and
runtime ports.  The worker owns its lifecycle: one projector is retained per
active run, replayed from the validated store on restart, and sealed only after
the enclosing run has a durable terminal fact.
"""

from __future__ import annotations

from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.observability.model_invocation_metrics import (
    ModelInvocationMetricsPort,
    ModelInvocationMetricsProjector,
    ModelInvocationMetricsReplayCheckpoint,
)


class ModelInvocationMetricsProjectionCoordinator:
    """Own projectors by run without exposing identifiers as metric labels."""

    def __init__(
        self,
        *,
        journal: ModelInvocationStorePort,
        metrics: ModelInvocationMetricsPort | None = None,
        max_records: int = 4096,
    ) -> None:
        self._journal = journal
        self._metrics = metrics
        self._max_records = max_records
        self._projectors: dict[str, ModelInvocationMetricsProjector] = {}
        self._after_sequence: dict[str, int] = {}

    async def replay(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        outer_run_terminal: bool = False,
    ) -> ModelInvocationMetricsReplayCheckpoint | None:
        """Replay new validated facts and optionally seal after outer terminal.

        A fresh coordinator starts at sequence zero: the journal's record IDs
        are deterministic and the projector accepts overlapping replay exactly
        once.  We retain a sequence cursor only to avoid needless work while a
        worker remains alive; it is never trusted in place of journal replay.
        """

        projector = self._projectors.get(run_id)
        if projector is None:
            projector = ModelInvocationMetricsProjector(
                metrics=self._metrics,
                max_records=self._max_records,
            )
            self._projectors[run_id] = projector
        records = await self._journal.list_for_run(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
            after_sequence=self._after_sequence.get(run_id, 0),
        )
        if records:
            projector.project(records)
            self._after_sequence[run_id] = records[-1].sequence_no
        if not outer_run_terminal:
            return None
        checkpoint = projector.seal_terminal_replay()
        self._projectors.pop(run_id, None)
        self._after_sequence.pop(run_id, None)
        return checkpoint


__all__ = ("ModelInvocationMetricsProjectionCoordinator",)
