"""Read-only adapters that feed existing projectors into E2 D2 comparison.

Nothing in this module owns state or invokes an authority.  It adapts already
persisted event/usage values into bounded semantic snapshots and hands them to
the pure :mod:`agent_runtime.rollout_shadow` service.  In particular, it never
opens a gateway, creates an artifact or stage, enqueues a command, or changes a
model-visible tool return.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.artifacts.projection import ArtifactProjection
from agent_runtime.observability.usage_meter import UsageMeter
from agent_runtime.rollout_shadow import (
    ShadowComparisonKind,
    ShadowComparisonResult,
    ShadowComparisonService,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.compatibility import project_legacy_ledger_for_read
from agent_runtime.surfaces_v2.pending_work import PendingWorkFold
from agent_runtime.surfaces_v2.pending_work_v2 import PendingWorkProjectionV2
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.receipt_v2 import ReceiptFoldV2


_LOGGER = logging.getLogger(__name__)


class _EventKey:
    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    CREATED_AT = "created_at"
    PAYLOAD = "payload"
    RUN_ID = "run_id"
    CONVERSATION_ID = "conversation_id"


class _LegacyEventType:
    SURFACE_CREATED = "surface.created"
    VIEW_DERIVED = "view.derived"
    VIEW_PREFERENCE = "view.preference"
    DRAFT_UPDATED = "draft_updated"


@runtime_checkable
class ShadowEventStorePort(Protocol):
    """The intentionally read-only event-store subset used by the observer."""

    async def get_latest_sequence(self, *, run_id: str) -> int:
        """Return a run's current monotonic event sequence."""

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[object]:
        """Read persisted events; no append/update/queue method is available."""


class _ComparisonBounds:
    """Bound a terminal observer before it reads or folds a large run."""

    MAX_EVENTS = 512


class ShadowProjectionComparators:
    """Pure adapters for every E2 D2 comparison family."""

    def __init__(self, service: ShadowComparisonService) -> None:
        self._service = service

    def compare_classification(
        self,
        *,
        legacy_class: str | None,
        canonical_effect_class: str | None,
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare the legacy classifier's closed verdict to the canonical one."""

        if legacy_class is None or canonical_effect_class is None:
            return self._service.uncomparable(kind=ShadowComparisonKind.CLASSIFICATION)
        return self._service.compare(
            kind=ShadowComparisonKind.CLASSIFICATION,
            legacy={"effect_class": legacy_class},
            canonical={"effect_class": canonical_effect_class},
            sample_key=sample_key,
        )

    def compare_disposition(
        self,
        *,
        legacy_disposition: str | None,
        canonical_disposition: str | None,
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare display disposition only; neither side is applied here."""

        if legacy_disposition is None or canonical_disposition is None:
            return self._service.uncomparable(kind=ShadowComparisonKind.DISPOSITION)
        return self._service.compare(
            kind=ShadowComparisonKind.DISPOSITION,
            legacy={"disposition": legacy_disposition},
            canonical={"disposition": canonical_disposition},
            sample_key=sample_key,
        )

    def compare_surface_tabs(
        self,
        *,
        run_id: str,
        events: Iterable[object],
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare legacy presentation IDs with the SurfaceStore tab identity set."""

        rows = _event_rows(events, run_id=run_id)
        presentation_rows = tuple(
            row
            for row in rows
            if row[_EventKey.EVENT_TYPE]
            in {
                _LegacyEventType.SURFACE_CREATED,
                _LegacyEventType.VIEW_DERIVED,
                _LegacyEventType.VIEW_PREFERENCE,
            }
        )
        try:
            legacy = project_legacy_ledger_for_read(
                row
                for row in presentation_rows
                if row[_EventKey.EVENT_TYPE]
                in {
                    _LegacyEventType.SURFACE_CREATED,
                    _LegacyEventType.VIEW_DERIVED,
                }
            )
            canonical = SurfaceStoreProjection.fold_raw(run_id, presentation_rows)
            legacy_tabs = tuple(
                sorted({item.surface_id for item in legacy.presentation_events})
            )
            canonical_tabs = tuple(
                sorted(item.surface_id for item in canonical.surfaces)
            )
        except Exception:
            return self._service.uncomparable(
                kind=ShadowComparisonKind.SURFACE_TAB_PROJECTION
            )
        return self._service.compare(
            kind=ShadowComparisonKind.SURFACE_TAB_PROJECTION,
            legacy={"tabs": legacy_tabs},
            canonical={"tabs": canonical_tabs},
            sample_key=sample_key,
        )

    def compare_receipt(
        self,
        *,
        run_id: str,
        events: Iterable[object],
        run_status: object | None,
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare only receipt facts represented by both legacy and v2.1 folds."""

        rows = _event_rows(events, run_id=run_id)
        try:
            legacy = ReceiptFold.fold_raw(run_id=run_id, events=rows)
            canonical = ReceiptFoldV2.fold_raw(
                run_id=run_id,
                events=rows,
                run_status=run_status,
            )
        except Exception:
            return self._service.uncomparable(kind=ShadowComparisonKind.RECEIPT_FOLD)
        return self._service.compare(
            kind=ShadowComparisonKind.RECEIPT_FOLD,
            legacy={
                "reads": legacy.tiles.reads_auto_ran,
                "proposed": legacy.tiles.writes_proposed,
                "approved": legacy.tiles.writes_approved,
                "held": legacy.tiles.holds_untouched,
            },
            canonical={
                "reads": canonical.reads.completed,
                "proposed": canonical.effects.proposed,
                "approved": canonical.effects.approved,
                "held": canonical.effects.held,
            },
            sample_key=sample_key,
        )

    def compare_pending(
        self,
        *,
        run_id: str,
        events: Iterable[object],
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare pending subject identities, never approval/commit authority."""

        rows = _event_rows(events, run_id=run_id)
        try:
            legacy = PendingWorkFold.fold_raw(rows)
            canonical = PendingWorkProjectionV2.fold_raw(run_id, rows)
            legacy_items = tuple(
                sorted(
                    (
                        {
                            "kind": "gate" if item.gate_id is not None else "effect",
                            "id": item.gate_id or item.stage_id or "",
                        }
                        for item in legacy
                    ),
                    key=lambda item: (str(item["kind"]), str(item["id"])),
                )
            )
            canonical_items = tuple(
                sorted(
                    (
                        {
                            "kind": item.subject_kind.value,
                            "id": item.subject_id,
                        }
                        for item in canonical.items
                    ),
                    key=lambda item: (str(item["kind"]), str(item["id"])),
                )
            )
        except Exception:
            return self._service.uncomparable(kind=ShadowComparisonKind.PENDING_FOLD)
        return self._service.compare(
            kind=ShadowComparisonKind.PENDING_FOLD,
            legacy={"items": legacy_items},
            canonical={"items": canonical_items},
            sample_key=sample_key,
        )

    def compare_usage_folds(
        self,
        *,
        run_id: str,
        usage_rows: Iterable[object],
        events: Iterable[object],
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare existing durable usage rows to the ledger's in-contract totals.

        Rows whose ``Purpose`` has no ledger equivalent deliberately make the
        comparison uncomparable rather than being silently dropped.  This is
        how the observer avoids claiming parity for background usage that the
        run ledger never promised to represent.
        """

        try:
            legacy, unsupported = _usage_totals_from_rows(usage_rows)
            if unsupported:
                return self._service.uncomparable(kind=ShadowComparisonKind.USAGE_FOLD)
            receipt = ReceiptFoldV2.fold_raw(
                run_id=run_id,
                events=_event_rows(events, run_id=run_id),
            )
            canonical = {
                item.purpose.value: {
                    "records": item.records,
                    "tokens_in": item.tokens_in,
                    "tokens_out": item.tokens_out,
                }
                for item in receipt.usage.totals_by_purpose
            }
        except Exception:
            return self._service.uncomparable(kind=ShadowComparisonKind.USAGE_FOLD)
        return self._service.compare(
            kind=ShadowComparisonKind.USAGE_FOLD,
            legacy=legacy,
            canonical=canonical,
            sample_key=sample_key,
        )

    def compare_artifact_draft_metadata(
        self,
        *,
        legacy_drafts: Iterable[object],
        canonical_events: Iterable[object],
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare metadata only; bodies and references never leave this adapter."""

        try:
            legacy = _legacy_draft_metadata(legacy_drafts)
            artifacts = ArtifactProjection.fold(_event_rows(canonical_events))
            canonical = tuple(
                {
                    "id": artifact.artifact_id,
                    "revision": artifact.current_revision,
                    "content_digest": artifact.revisions[-1].content_digest,
                }
                for artifact in artifacts.artifacts
            )
        except Exception:
            return self._service.uncomparable(
                kind=ShadowComparisonKind.ARTIFACT_DRAFT_METADATA
            )
        return self._service.compare(
            kind=ShadowComparisonKind.ARTIFACT_DRAFT_METADATA,
            legacy={"items": legacy},
            canonical={"items": canonical},
            sample_key=sample_key,
        )

    def compare_artifact_draft_metadata_from_events(
        self,
        *,
        events: Iterable[object],
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Use the current ``draft_updated`` projection for terminal observation."""

        rows = _event_rows(events)
        legacy_drafts = tuple(
            row[_EventKey.PAYLOAD]
            for row in rows
            if row[_EventKey.EVENT_TYPE] == _LegacyEventType.DRAFT_UPDATED
        )
        return self.compare_artifact_draft_metadata(
            legacy_drafts=legacy_drafts,
            canonical_events=rows,
            sample_key=sample_key,
        )

    def compare_proposal_canonicalization(
        self,
        *,
        legacy_arguments: Mapping[str, object],
        canonical_args_digest: str,
        sample_key: str,
    ) -> ShadowComparisonResult:
        """Compare canonical argument digests without resolving or dispatching anything."""

        try:
            legacy_digest = sha256_hex(canonical_json_bytes(dict(legacy_arguments)))
        except Exception:
            return self._service.uncomparable(
                kind=ShadowComparisonKind.PROPOSAL_CANONICALIZATION
            )
        return self._service.compare(
            kind=ShadowComparisonKind.PROPOSAL_CANONICALIZATION,
            legacy={"args_digest": legacy_digest},
            canonical={"args_digest": canonical_args_digest},
            sample_key=sample_key,
        )


@dataclass(frozen=True)
class ShadowRunProjectionObserver:
    """Bounded terminal observer over the existing read-only event-store port.

    Per-run usage rows do not have a bounded reader in the current persistence
    contract, so usage comparison remains an explicit call-in adapter above.
    The observer records ``uncomparable`` instead of issuing an unbounded scan.
    """

    service: ShadowComparisonService

    async def observe(
        self,
        *,
        event_store: ShadowEventStorePort,
        org_id: str,
        run_id: str,
        run_status: object | None,
    ) -> tuple[ShadowComparisonResult, ...]:
        """Read at most one bounded ledger prefix and compare safe projections."""

        try:
            latest_sequence = await event_store.get_latest_sequence(run_id=run_id)
            if latest_sequence > _ComparisonBounds.MAX_EVENTS:
                return self._too_large()
            events = await event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=0,
            )
            if len(events) > _ComparisonBounds.MAX_EVENTS:
                return self._too_large()
            comparators = ShadowProjectionComparators(self.service)
            return (
                comparators.compare_surface_tabs(
                    run_id=run_id, events=events, sample_key=run_id
                ),
                comparators.compare_receipt(
                    run_id=run_id,
                    events=events,
                    run_status=run_status,
                    sample_key=run_id,
                ),
                comparators.compare_pending(
                    run_id=run_id, events=events, sample_key=run_id
                ),
                comparators.compare_artifact_draft_metadata_from_events(
                    events=events, sample_key=run_id
                ),
            )
        except asyncio.CancelledError:
            if _task_is_cancelling():
                raise
            _LOGGER.debug("e2_shadow_comparison_observer_cancelled")
            return ()
        except Exception:
            _LOGGER.debug("e2_shadow_comparison_observer_failed")
            return ()

    def _too_large(self) -> tuple[ShadowComparisonResult, ...]:
        return tuple(
            self.service.uncomparable(kind=kind)
            for kind in (
                ShadowComparisonKind.SURFACE_TAB_PROJECTION,
                ShadowComparisonKind.RECEIPT_FOLD,
                ShadowComparisonKind.PENDING_FOLD,
                ShadowComparisonKind.ARTIFACT_DRAFT_METADATA,
            )
        )


def _event_rows(
    events: Iterable[object], *, run_id: str | None = None
) -> tuple[dict[str, object], ...]:
    """Copy the structural event subset a pure projector accepts; never mutate it."""

    rows: list[dict[str, object]] = []
    for event in events:
        event_type = _event_field(event, _EventKey.EVENT_TYPE, "")
        event_type = getattr(event_type, "value", event_type)
        payload = _event_field(event, _EventKey.PAYLOAD, {})
        rows.append(
            {
                _EventKey.EVENT_TYPE: event_type if isinstance(event_type, str) else "",
                _EventKey.SEQUENCE_NO: _event_field(event, _EventKey.SEQUENCE_NO, 0),
                _EventKey.CREATED_AT: _event_field(event, _EventKey.CREATED_AT, ""),
                _EventKey.PAYLOAD: payload if isinstance(payload, Mapping) else {},
                _EventKey.RUN_ID: _event_field(event, _EventKey.RUN_ID, run_id or ""),
                _EventKey.CONVERSATION_ID: _event_field(
                    event, _EventKey.CONVERSATION_ID, ""
                ),
            }
        )
    return tuple(rows)


def _usage_totals_from_rows(
    rows: Iterable[object],
) -> tuple[dict[str, dict[str, int]], bool]:
    """Use the existing A2 purpose mapping and rollup arithmetic, read-only."""

    normalized: list[dict[str, object]] = []
    unsupported = False
    for row in rows:
        purpose = _field(row, "purpose")
        if not isinstance(purpose, str):
            unsupported = True
            continue
        ledger_purpose = UsageMeter.ledger_purpose_for(purpose)
        if ledger_purpose is None:
            unsupported = True
            continue
        normalized.append(
            {
                "purpose": ledger_purpose.value,
                "input_tokens": _nonnegative_int(_field(row, "input_tokens")),
                "output_tokens": _nonnegative_int(_field(row, "output_tokens")),
                "cached_input_tokens": 0,
                "total_tokens": _nonnegative_int(_field(row, "total_tokens")),
            }
        )

    # Reuse the product's canonical summation helper rather than duplicating
    # per-field addition.  Grouping is only by the closed ledger purpose.
    buckets: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in normalized:
        buckets[str(row["purpose"])].append(row)
    totals: dict[str, dict[str, int]] = {}
    for purpose, entries in sorted(buckets.items()):
        summed = UsageQueryService.sum_totals(entries)
        totals[purpose] = {
            "records": len(entries),
            "tokens_in": summed["input_tokens"],
            "tokens_out": summed["output_tokens"],
        }
    return totals, unsupported


def _legacy_draft_metadata(drafts: Iterable[object]) -> tuple[dict[str, object], ...]:
    """Extract the common metadata only; body, connector target, and title stay out."""

    items: list[dict[str, object]] = []
    for draft in drafts:
        identifier = _field(draft, "draft_id") or _field(draft, "artifact_id")
        revision = _field(draft, "version")
        if not isinstance(identifier, str) or not isinstance(revision, int):
            raise ValueError("legacy draft metadata is incomplete")
        item: dict[str, object] = {"id": identifier, "revision": revision}
        content_digest = _field(draft, "content_digest")
        if isinstance(content_digest, str):
            item["content_digest"] = content_digest
        items.append(item)
    return tuple(sorted(items, key=lambda item: str(item["id"])))


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _event_field(event: object, name: str, default: object) -> object:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _nonnegative_int(value: object | None) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _task_is_cancelling() -> bool:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    return task is not None and task.cancelling() > 0


__all__ = (
    "ShadowEventStorePort",
    "ShadowProjectionComparators",
    "ShadowRunProjectionObserver",
)
