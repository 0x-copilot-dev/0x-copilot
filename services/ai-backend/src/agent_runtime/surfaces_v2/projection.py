"""SurfaceStore projection — a pure, rebuildable fold over the Work Ledger (D6).

The canvas state served by ``GET /v1/agent/runs/{run_id}/surfaces`` is not stored
anywhere: it is a deterministic, total fold over the run's ledger events (SDR §3,
§6). Replaying the same events — on reconnect, app restart, or a fresh store
instance over the same on-disk root — reconstructs identical state.

``fold`` accepts the typed transport envelopes the event store returns;
``fold_raw`` accepts plain JSON dicts so the A1 golden fixture drives both this
fold and (in PRD-B1) the TypeScript twin. To stay a clean sibling of the A1
contracts, this module reads events **structurally** (``event_type`` /
``sequence_no`` / ``payload``) and never imports ``runtime_api`` — a
``RuntimeEventEnvelope`` satisfies :class:`_LedgerEventLike` by shape.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.constants import Keys
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


@runtime_checkable
class _LedgerEventLike(Protocol):
    """Structural shape of a persisted event the fold reads (envelope-lite)."""

    event_type: object
    sequence_no: int
    payload: Mapping[str, object]


# ---------------------------------------------------------------------------
# Fold output contracts (Pydantic; served as-is by the surfaces endpoint)
# ---------------------------------------------------------------------------


class SurfaceViewState(RuntimeContract):
    """The derived-view state of a surface, folded from ``view.derived``.

    Carries ``generator_model`` (the A1 ``gen.model``) rather than a
    ``preference`` — this is the fold-projection twin of A1's ``SurfaceView``
    (2026-07-23 close-out; PRD-A3 Open questions item 1).
    """

    tier: str
    basis: str
    spec_ref: str | None = None
    generator_model: str | None = None


class SurfaceSnapshot(RuntimeContract):
    """One surface's folded metadata (no hydrated payload content).

    ``first_sequence_no`` / ``last_sequence_no`` are the fold bookkeeping A1's
    ``Surface`` entity lacks: ``first`` anchors ``ledger_id`` and dedupes repeat
    reads; ``last`` is the newest ledger position that touched the surface.
    """

    surface_id: str
    kind: str
    connector: str
    op: str
    title: str
    payload_ref: str
    view: SurfaceViewState | None = None
    first_sequence_no: int
    last_sequence_no: int
    ledger_id: str


class SurfaceStoreState(RuntimeContract):
    """The full SurfaceStore projection for one run."""

    run_id: str
    surfaces: tuple[SurfaceSnapshot, ...]
    latest_sequence_no: int


# ---------------------------------------------------------------------------
# Fold
# ---------------------------------------------------------------------------


@dataclass
class _SurfaceAccumulator:
    """Mutable per-surface fold state, frozen into a snapshot at the end."""

    surface_id: str
    kind: str
    connector: str
    op: str
    title: str
    payload_ref: str
    first_sequence_no: int
    last_sequence_no: int
    ledger_id: str
    view: SurfaceViewState | None = None

    def to_snapshot(self) -> SurfaceSnapshot:
        return SurfaceSnapshot(
            surface_id=self.surface_id,
            kind=self.kind,
            connector=self.connector,
            op=self.op,
            title=self.title,
            payload_ref=self.payload_ref,
            view=self.view,
            first_sequence_no=self.first_sequence_no,
            last_sequence_no=self.last_sequence_no,
            ledger_id=self.ledger_id,
        )


@dataclass
class _FoldCarry:
    """Insertion-ordered surface accumulators + the run's sequence watermark."""

    surfaces: dict[str, _SurfaceAccumulator] = field(default_factory=dict)
    latest_sequence_no: int = 0


class SurfaceStoreProjection:
    """Pure fold from a run's ledger events to its :class:`SurfaceStoreState`."""

    @staticmethod
    def fold(run_id: str, events: Iterable[_LedgerEventLike]) -> SurfaceStoreState:
        """Fold typed transport envelopes (or any object with the three fields)."""

        return SurfaceStoreProjection.fold_raw(
            run_id,
            (
                {
                    _RawKey.EVENT_TYPE: SurfaceStoreProjection._event_type_value(
                        event.event_type
                    ),
                    _RawKey.SEQUENCE_NO: event.sequence_no,
                    _RawKey.PAYLOAD: event.payload,
                }
                for event in events
            ),
        )

    @staticmethod
    def fold_raw(
        run_id: str, events: Iterable[Mapping[str, object]]
    ) -> SurfaceStoreState:
        """Fold plain ``{event_type, sequence_no, payload}`` dicts.

        Deterministic + total: events are processed in ``sequence_no`` order;
        ``surface.created`` upserts by ``surface_id`` (repeat reads refresh
        ``title`` / ``payload_ref`` / ``last_sequence_no``, keeping the first
        ``first_sequence_no`` / ``ledger_id``); ``view.derived`` updates the
        matching surface's view (ignored if the surface is unseen); every other
        event type — including all future vocabulary — is skipped without error.
        """

        ordered = sorted(
            events, key=lambda event: SurfaceStoreProjection._seq_of(event)
        )
        carry = _FoldCarry()
        for event in ordered:
            seq = SurfaceStoreProjection._seq_of(event)
            carry.latest_sequence_no = max(carry.latest_sequence_no, seq)
            event_type = str(event.get(_RawKey.EVENT_TYPE, ""))
            payload = event.get(_RawKey.PAYLOAD)
            payload = payload if isinstance(payload, Mapping) else {}
            if event_type == LedgerEventType.SURFACE_CREATED.value:
                SurfaceStoreProjection._apply_surface_created(
                    carry, run_id=run_id, seq=seq, payload=payload
                )
            elif event_type == LedgerEventType.VIEW_DERIVED.value:
                SurfaceStoreProjection._apply_view_derived(
                    carry, seq=seq, payload=payload
                )
            # All other event types (present + future) are intentionally skipped.
        return SurfaceStoreState(
            run_id=run_id,
            surfaces=tuple(
                accumulator.to_snapshot() for accumulator in carry.surfaces.values()
            ),
            latest_sequence_no=carry.latest_sequence_no,
        )

    # -- reducers -----------------------------------------------------------

    @staticmethod
    def _apply_surface_created(
        carry: _FoldCarry,
        *,
        run_id: str,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        surface_id = payload.get(Keys.Field.SURFACE_ID)
        if not isinstance(surface_id, str) or not surface_id:
            return
        title = SurfaceStoreProjection._str_or(payload.get(Keys.Field.TITLE), "")
        payload_ref = SurfaceStoreProjection._str_or(
            payload.get(Keys.Field.PAYLOAD_REF), ""
        )
        existing = carry.surfaces.get(surface_id)
        if existing is not None:
            # Upsert: refresh the mutable projection, keep the first anchor.
            existing.title = title
            existing.payload_ref = payload_ref
            existing.last_sequence_no = seq
            return
        kind = SurfaceStoreProjection._str_or(payload.get(Keys.Field.KIND), "")
        source = payload.get(Keys.Field.SOURCE)
        source = source if isinstance(source, Mapping) else {}
        connector = SurfaceStoreProjection._str_or(source.get(Keys.Field.CONNECTOR), "")
        op = SurfaceStoreProjection._str_or(source.get(Keys.Field.OP), "")
        carry.surfaces[surface_id] = _SurfaceAccumulator(
            surface_id=surface_id,
            kind=kind,
            connector=connector,
            op=op,
            title=title,
            payload_ref=payload_ref,
            first_sequence_no=seq,
            last_sequence_no=seq,
            ledger_id=LedgerIdCodec.format(run_id, seq),
        )

    @staticmethod
    def _apply_view_derived(
        carry: _FoldCarry,
        *,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        surface_id = payload.get(Keys.Field.SURFACE_ID)
        if not isinstance(surface_id, str):
            return
        accumulator = carry.surfaces.get(surface_id)
        if accumulator is None:
            # Defensive + pure: a view for an unseen surface is ignored.
            return
        gen = payload.get(Keys.Field.GEN)
        generator_model = None
        if isinstance(gen, Mapping):
            model = gen.get(Keys.Field.MODEL)
            generator_model = model if isinstance(model, str) and model else None
        spec_ref = payload.get(Keys.Field.SPEC_REF)
        accumulator.view = SurfaceViewState(
            tier=SurfaceStoreProjection._str_or(payload.get(Keys.Field.TIER), ""),
            basis=SurfaceStoreProjection._str_or(payload.get(Keys.Field.BASIS), ""),
            spec_ref=spec_ref if isinstance(spec_ref, str) and spec_ref else None,
            generator_model=generator_model,
        )
        accumulator.last_sequence_no = max(accumulator.last_sequence_no, seq)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _seq_of(event: Mapping[str, object]) -> int:
        raw = event.get(_RawKey.SEQUENCE_NO, 0)
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", None)
        if isinstance(value, str):
            return value
        return str(event_type)

    @staticmethod
    def _str_or(value: object, default: str) -> str:
        return value if isinstance(value, str) else default


class _RawKey:
    """Keys of the plain-dict event shape ``fold_raw`` consumes."""

    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    PAYLOAD = "payload"


__all__ = [
    "SurfaceSnapshot",
    "SurfaceStoreProjection",
    "SurfaceStoreState",
    "SurfaceViewState",
]
