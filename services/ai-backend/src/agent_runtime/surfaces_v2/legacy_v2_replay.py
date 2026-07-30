"""Versioned, read-only replay of historic Generative Surfaces v2 records.

This module is deliberately a *reader*, not a migration.  Historic run events
are append-only accountability records: replay must not rewrite their payloads,
turn an old staged write into a current executable effect, or invent a missing
surface body.  It exists for the finite E2 compatibility window while old v2
runs and their fixed renderer specs remain accessible.

The projection has three conservative inputs:

* old ``surface.created`` ledger subjects, hydrated only through the existing
  :class:`SurfaceContentProjection` declared-reference reader;
* historic ``payload.surface`` / flat surface envelopes, replayed with the
  same spec-only late-merge semantics as the pre-v2 canvas projector; and
* old ``surface_spec_generated`` records, never interpreted as executable
  code.

There are intentionally no persistence, queue, connector, worker, or runtime
API imports in this file.  The output is a versioned display model with every
surface marked ``read_only=True``.  Callers must never use it as input to a
writer or a canonical v2.1 store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Literal
from urllib.parse import quote

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class LegacyV2ReplayMode(StrEnum):
    """Version-aware selection result for a persisted event stream."""

    EMPTY = "empty"
    LEGACY_V2 = "legacy_v2"
    MIXED = "mixed"
    CANONICAL_V21 = "canonical_v21"


class LegacyV2QuarantineCode(StrEnum):
    """Safe, non-content-bearing reasons a historic record was withheld."""

    MALFORMED_SURFACE_CREATED = "malformed_surface_created"
    MISSING_SURFACE_SUBJECT = "missing_surface_subject"
    MALFORMED_SURFACE_ENVELOPE = "malformed_surface_envelope"
    INVALID_SURFACE_URI = "invalid_surface_uri"
    ORPHANED_SURFACE_SPEC = "orphaned_surface_spec"
    UNHYDRATED_PAYLOAD_REF = "unhydrated_payload_ref"


class LegacyV2ReplayQuarantine(RuntimeContract):
    """A safe pointer to data deliberately not reconstructed by the reader."""

    sequence_no: int | None
    event_type: str | None
    code: LegacyV2QuarantineCode
    subject_id: str | None = None


class LegacyV2SurfaceProjection(RuntimeContract):
    """One historic surface mounted through a fixed, pre-built renderer."""

    subject_id: str
    uri: str
    kind: str | None = None
    title: str | None = None
    source_connector: str | None = None
    source_op: str | None = None
    payload_ref: str | None = None
    state: dict[str, object] | None = None
    last_sequence_no: int
    origin: Literal["ledger", "envelope"]
    read_only: Literal[True] = True


class LegacyV2ReplayProjection(RuntimeContract):
    """The stable cross-language result of replaying a historic run stream."""

    reader_version: Literal[1] = 1
    mode: LegacyV2ReplayMode
    surfaces: tuple[LegacyV2SurfaceProjection, ...]
    quarantined: tuple[LegacyV2ReplayQuarantine, ...]


@dataclass(frozen=True)
class _ReplayEvent:
    event_type: str
    sequence_no: int
    payload: Mapping[str, object]
    index: int
    event_id: str | None


@dataclass
class _LegacySurface:
    subject_id: str
    uri: str
    kind: str | None
    title: str | None
    source_connector: str | None
    source_op: str | None
    payload_ref: str | None
    state: dict[str, object] | None
    last_sequence_no: int
    origin: Literal["ledger", "envelope"]


@dataclass(frozen=True)
class _ContentEvent:
    """Structural adapter allowing the existing content projector to be reused."""

    event_type: str
    sequence_no: int
    payload: Mapping[str, object]


_SURFACE_MUTATION_TYPES = frozenset(
    {"tool_result", "presentation_updated", "draft_updated"}
)
_LEGACY_PROOF_EVENT_TYPES = frozenset(
    {
        "action.classified",
        "read.executed",
        "write.staged",
        "revision.added",
        "decision.recorded",
        "write.applied",
        "gate.opened",
        "gate.resolved",
        "receipt.emitted",
        "usage.recorded",
    }
)
_CANONICAL_V21_PREFIXES = ("operation.", "artifact.", "effect.")
_CANONICAL_V21_EVENT_TYPES = frozenset(
    {
        LedgerEventType.GATE_OPENED_V2.value,
        LedgerEventType.GATE_RESOLVED_V2.value,
    }
)
_URI_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s]+$")


def project_legacy_v2_replay(
    events: Iterable[Mapping[str, object]],
) -> LegacyV2ReplayProjection:
    """Read historic surface records without changing the source event stream.

    The result is total for malformed persisted input.  Unknown records stay
    unknown; the projection only materializes an exact old surface declaration
    or an old presentation envelope.  Duplicate ``event_id`` values model SSE
    reconnect replay and are ignored after their first ordered occurrence.
    """

    ordered, quarantined = _ordered_events(events)
    seen_event_ids: set[str] = set()
    deduplicated: list[_ReplayEvent] = []
    for event in ordered:
        if event.event_id is not None:
            if event.event_id in seen_event_ids:
                continue
            seen_event_ids.add(event.event_id)
        deduplicated.append(event)

    legacy_seen = False
    canonical_seen = False
    ledger_surfaces: dict[str, _LegacySurface] = {}
    envelope_surfaces: dict[str, _LegacySurface] = {}
    legacy_stream_proven = _has_legacy_proof(deduplicated)

    for event in deduplicated:
        if _is_canonical_v21(event.event_type):
            canonical_seen = True
        if event.event_type in _LEGACY_PROOF_EVENT_TYPES:
            legacy_seen = True

        if event.event_type == "surface.created":
            # `surface.created` remains an intentional v2.1 bridge event.
            # Claim it for the historic renderer only when the stream (or the
            # connector/call identity itself) proves it is pre-v2.1. This
            # prevents an active canonical surface from being downgraded to a
            # read-only compatibility tab merely because the wire name lives
            # across the boundary.
            if _is_legacy_surface_created(event, legacy_stream_proven):
                legacy_seen = True
                _apply_surface_created(event, ledger_surfaces, quarantined)
            continue

        if event.event_type in _SURFACE_MUTATION_TYPES:
            envelope = _read_surface_envelope(event.payload)
            if envelope is not None:
                legacy_seen = True
                _apply_surface_envelope(event, envelope, envelope_surfaces, quarantined)
            elif "surface" in event.payload:
                legacy_seen = True
                _quarantine(
                    quarantined,
                    event,
                    LegacyV2QuarantineCode.MALFORMED_SURFACE_ENVELOPE,
                )
            continue

        if event.event_type == "surface_spec_generated":
            if _apply_surface_spec(
                event,
                ledger_surfaces,
                envelope_surfaces,
                quarantined,
            ):
                legacy_seen = True

    _hydrate_ledger_surfaces(deduplicated, ledger_surfaces, quarantined)
    surfaces = tuple(
        LegacyV2SurfaceProjection(
            subject_id=surface.subject_id,
            uri=surface.uri,
            kind=surface.kind,
            title=surface.title,
            source_connector=surface.source_connector,
            source_op=surface.source_op,
            payload_ref=surface.payload_ref,
            state=_copy_state(surface.state),
            last_sequence_no=surface.last_sequence_no,
            origin=surface.origin,
        )
        for surface in sorted(
            (*ledger_surfaces.values(), *envelope_surfaces.values()),
            key=lambda surface: (
                surface.last_sequence_no,
                surface.uri,
                surface.subject_id,
            ),
        )
    )
    return LegacyV2ReplayProjection(
        mode=_mode(legacy_seen=legacy_seen, canonical_seen=canonical_seen),
        surfaces=surfaces,
        quarantined=tuple(quarantined),
    )


def _ordered_events(
    events: Iterable[Mapping[str, object]],
) -> tuple[list[_ReplayEvent], list[LegacyV2ReplayQuarantine]]:
    normalized: list[_ReplayEvent] = []
    quarantined: list[LegacyV2ReplayQuarantine] = []
    for index, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            continue
        event_type = _text(raw.get("event_type"))
        sequence_no = raw.get("sequence_no")
        if event_type is None or not _valid_sequence(sequence_no):
            continue
        payload_raw = raw.get("payload")
        payload = payload_raw if isinstance(payload_raw, Mapping) else {}
        normalized.append(
            _ReplayEvent(
                event_type=event_type,
                sequence_no=sequence_no,
                payload=payload,
                index=index,
                event_id=_text(raw.get("event_id")),
            )
        )
    normalized.sort(key=lambda event: (event.sequence_no, event.index))
    return normalized, quarantined


def _has_legacy_proof(events: Iterable[_ReplayEvent]) -> bool:
    """Return whether an event batch unambiguously contains old-v2 facts.

    The `surface.created` name intentionally spans the v2 → v2.1 boundary.
    It is not evidence by itself: a live canonical surface must stay owned by
    the v2.1 projector. Old operation/effect/gate rows, historic presentation
    envelopes, `call:` payload references, and connector-scoped subjects are
    durable facts that make the legacy reader safe to select.
    """

    for event in events:
        if event.event_type in _LEGACY_PROOF_EVENT_TYPES:
            return True
        if event.event_type == "surface.created":
            subject_id = _text(event.payload.get("surface_id"))
            payload_ref = _text(event.payload.get("payload_ref"))
            if _is_connector_subject(subject_id) or _is_call_ref(payload_ref):
                return True
        if event.event_type in _SURFACE_MUTATION_TYPES and (
            "surface" in event.payload or "surface_uri" in event.payload
        ):
            return True
        if event.event_type == "surface_spec_generated" and (
            "surface_uri" in event.payload or "surface_id" in event.payload
        ):
            return True
    return False


def _is_legacy_surface_created(
    event: _ReplayEvent,
    legacy_stream_proven: bool,
) -> bool:
    """Select old subjects while preserving canonical `operation://` rows."""

    payload_ref = _text(event.payload.get("payload_ref"))
    if payload_ref is not None and payload_ref.startswith("operation://"):
        return False
    subject_id = _text(event.payload.get("surface_id"))
    return (
        legacy_stream_proven
        or _is_connector_subject(subject_id)
        or _is_call_ref(payload_ref)
    )


def _is_connector_subject(value: str | None) -> bool:
    return value is not None and value.startswith("connector:")


def _is_call_ref(value: str | None) -> bool:
    return value is not None and value.startswith("call:")


def _apply_surface_created(
    event: _ReplayEvent,
    surfaces: dict[str, _LegacySurface],
    quarantined: list[LegacyV2ReplayQuarantine],
) -> None:
    if not event.payload:
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.MALFORMED_SURFACE_CREATED,
        )
        return
    subject_id = _text(event.payload.get("surface_id"))
    if subject_id is None:
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.MISSING_SURFACE_SUBJECT,
        )
        return
    source = event.payload.get("source")
    source = source if isinstance(source, Mapping) else {}
    prior = surfaces.get(subject_id)
    state = prior.state if prior is not None else None
    surfaces[subject_id] = _LegacySurface(
        subject_id=subject_id,
        uri=_ledger_uri(subject_id, _text(event.payload.get("kind"))),
        kind=_text(event.payload.get("kind")),
        title=_text(event.payload.get("title")),
        source_connector=_text(source.get("connector")),
        source_op=_text(source.get("op")),
        payload_ref=_text(event.payload.get("payload_ref")),
        state=state,
        last_sequence_no=max(
            event.sequence_no,
            prior.last_sequence_no if prior is not None else event.sequence_no,
        ),
        origin="ledger",
    )


def _read_surface_envelope(
    payload: Mapping[str, object],
) -> tuple[str | None, str | None, Mapping[str, object] | None, bool] | None:
    nested = payload.get("surface")
    if isinstance(nested, Mapping):
        archetype = _text(nested.get("archetype"))
        state = nested.get("state")
        return (
            _text(nested.get("surface_uri")),
            archetype,
            state if isinstance(state, Mapping) else None,
            "state" in nested,
        )
    uri = _text(payload.get("surface_uri"))
    if uri is None:
        return None
    state = payload.get("state")
    return (
        uri,
        _text(payload.get("archetype")),
        state if isinstance(state, Mapping) else None,
        "state" in payload,
    )


def _apply_surface_envelope(
    event: _ReplayEvent,
    envelope: tuple[str | None, str | None, Mapping[str, object] | None, bool],
    surfaces: dict[str, _LegacySurface],
    quarantined: list[LegacyV2ReplayQuarantine],
) -> None:
    uri, archetype, state, has_state = envelope
    if uri is None or not _is_uri(uri):
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.INVALID_SURFACE_URI,
        )
        return
    prior = surfaces.get(uri)
    if prior is None:
        prior = _LegacySurface(
            subject_id=uri,
            uri=uri,
            kind=archetype,
            title=None,
            source_connector=None,
            source_op=None,
            payload_ref=None,
            state=None,
            last_sequence_no=event.sequence_no,
            origin="envelope",
        )
    if state is None and has_state:
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.MALFORMED_SURFACE_ENVELOPE,
            subject_id=uri,
        )
    surfaces[uri] = _LegacySurface(
        subject_id=prior.subject_id,
        uri=uri,
        kind=archetype if archetype is not None else prior.kind,
        title=prior.title,
        source_connector=prior.source_connector,
        source_op=prior.source_op,
        payload_ref=None,
        state=_merge_state(prior.state, state),
        last_sequence_no=max(prior.last_sequence_no, event.sequence_no),
        origin="envelope",
    )


def _apply_surface_spec(
    event: _ReplayEvent,
    ledger_surfaces: dict[str, _LegacySurface],
    envelope_surfaces: dict[str, _LegacySurface],
    quarantined: list[LegacyV2ReplayQuarantine],
) -> bool:
    spec = event.payload.get("spec")
    identity = _text(event.payload.get("surface_uri")) or _text(
        event.payload.get("surface_id")
    )
    if identity is None and not isinstance(spec, Mapping):
        return False
    if identity is None or not isinstance(spec, Mapping):
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.ORPHANED_SURFACE_SPEC,
        )
        return True
    ledger = ledger_surfaces.get(identity)
    if ledger is not None:
        ledger.state = _merge_state(ledger.state, {"spec": spec})
        ledger.last_sequence_no = max(ledger.last_sequence_no, event.sequence_no)
        return True
    envelope = envelope_surfaces.get(identity)
    if envelope is not None:
        envelope.state = _merge_state(envelope.state, {"spec": spec})
        envelope.last_sequence_no = max(envelope.last_sequence_no, event.sequence_no)
        return True
    if not _is_uri(identity):
        _quarantine(
            quarantined,
            event,
            LegacyV2QuarantineCode.ORPHANED_SURFACE_SPEC,
            subject_id=identity,
        )
        return True
    envelope_surfaces[identity] = _LegacySurface(
        subject_id=identity,
        uri=identity,
        kind=_text(event.payload.get("archetype")),
        title=None,
        source_connector=None,
        source_op=None,
        payload_ref=None,
        state=_merge_state(None, {"spec": spec}),
        last_sequence_no=event.sequence_no,
        origin="envelope",
    )
    return True


def _hydrate_ledger_surfaces(
    events: Iterable[_ReplayEvent],
    surfaces: Mapping[str, _LegacySurface],
    quarantined: list[LegacyV2ReplayQuarantine],
) -> None:
    refs = {
        subject_id: surface.payload_ref
        for subject_id, surface in surfaces.items()
        if surface.payload_ref is not None
    }
    if not refs:
        return
    content = SurfaceContentProjection.fold(
        [
            _ContentEvent(
                event_type=event.event_type,
                sequence_no=event.sequence_no,
                payload=event.payload,
            )
            for event in events
        ],
        surface_payload_refs=refs,
    )
    for subject_id, surface in surfaces.items():
        hydrated = content.get(subject_id)
        if hydrated:
            surface.state = _merge_state(surface.state, _reader_1_state(hydrated))
            continue
        if surface.state is None and surface.payload_ref is not None:
            _quarantine(
                quarantined,
                _ReplayEvent(
                    event_type="surface.created",
                    sequence_no=surface.last_sequence_no,
                    payload={},
                    index=-1,
                    event_id=None,
                ),
                LegacyV2QuarantineCode.UNHYDRATED_PAYLOAD_REF,
                subject_id=subject_id,
            )


def _ledger_uri(subject_id: str, kind: str | None) -> str:
    """Create a stable fixed-renderer URI without changing the subject id."""

    scheme = "record" if kind == "call" else kind
    if scheme not in {"record", "message", "table", "raw"}:
        scheme = "raw"
    # Match JavaScript's ``encodeURIComponent`` safe set exactly so Python and
    # TypeScript select the same tab after reconnect.
    encoded = quote(subject_id, safe="-_.!~*'()")
    return f"{scheme}://legacy-v2/{encoded}"


def _mode(*, legacy_seen: bool, canonical_seen: bool) -> LegacyV2ReplayMode:
    if legacy_seen and canonical_seen:
        return LegacyV2ReplayMode.MIXED
    if legacy_seen:
        return LegacyV2ReplayMode.LEGACY_V2
    if canonical_seen:
        return LegacyV2ReplayMode.CANONICAL_V21
    return LegacyV2ReplayMode.EMPTY


def _is_canonical_v21(event_type: str) -> bool:
    return event_type.startswith(_CANONICAL_V21_PREFIXES) or (
        event_type in _CANONICAL_V21_EVENT_TYPES
    )


def _is_uri(value: str) -> bool:
    return bool(_URI_PATTERN.fullmatch(value))


#: The surface-state keys ``reader_version: 1`` publishes. The reader's output is
#: a cross-language contract — the same projection is implemented in TypeScript
#: (``packages/api-types/src/legacyV2Replay.ts``) and both are pinned to the
#: shared ``legacy_v2_replay_corpus.json`` vectors. A key the content fold learns
#: to resolve later is therefore NOT automatically a key this reader publishes:
#: widening the projection is a reader-version change made on both sides at once,
#: not a side effect of an upstream fold gaining a field.
_READER_1_STATE_KEYS: frozenset[str] = frozenset({"spec", "data"})


def _reader_1_state(hydrated: Mapping[str, object]) -> dict[str, object]:
    """Narrow resolved content to the state shape reader 1 declares.

    ``SurfaceContentProjection`` also resolves ``source`` (the surface's
    connector/tool provenance) for the live canvas, which reads it to name the
    unmatched tool on a spec-less surface. Historic replay does not publish it:
    the legacy display model already carries the same two facts as first-class
    ``source_connector`` / ``source_op`` fields, and its wire shape is frozen.
    """

    return {
        key: value for key, value in hydrated.items() if key in _READER_1_STATE_KEYS
    }


def _merge_state(
    current: Mapping[str, object] | None,
    incoming: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if current is None and incoming is None:
        return None
    merged: dict[str, object] = {}
    if current is not None:
        merged.update(deepcopy(dict(current)))
    if incoming is not None:
        merged.update(deepcopy(dict(incoming)))
    return merged


def _copy_state(state: Mapping[str, object] | None) -> dict[str, object] | None:
    return None if state is None else deepcopy(dict(state))


def _quarantine(
    quarantined: list[LegacyV2ReplayQuarantine],
    event: _ReplayEvent,
    code: LegacyV2QuarantineCode,
    *,
    subject_id: str | None = None,
) -> None:
    candidate = LegacyV2ReplayQuarantine(
        sequence_no=event.sequence_no,
        event_type=event.event_type,
        code=code,
        subject_id=subject_id,
    )
    if candidate not in quarantined:
        quarantined.append(candidate)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _valid_sequence(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 9_007_199_254_740_991
    )


__all__ = [
    "LegacyV2QuarantineCode",
    "LegacyV2ReplayMode",
    "LegacyV2ReplayProjection",
    "LegacyV2ReplayQuarantine",
    "LegacyV2SurfaceProjection",
    "project_legacy_v2_replay",
]
