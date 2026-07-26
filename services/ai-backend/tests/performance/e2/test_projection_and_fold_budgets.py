"""D11 deterministic replay and large-rowset capacity gates.

There are intentionally no asserted durations here.  CI machines vary; an
operation-count cap catches the actual production risk (nested replay / N+1)
while the report command records timings only as diagnostic evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.surfaces_v2.pending_work import PendingWorkFold
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.staging import StagedWriteFold

from .fixtures import CountingEventSequence, RUN_ID, replay_events, rowset_events

_LIMITS = json.loads(
    (Path(__file__).parents[5] / "tools/e2-performance/limits.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("event_count", _LIMITS["fixtures"]["replay_events"])
def test_replay_folds_have_bounded_source_passes(event_count: int) -> None:
    """Every public fold has a fixed, linear number of source traversals."""

    limits = _LIMITS["operation_limits"]
    cases = (
        (
            "surface_source_passes",
            lambda source: SurfaceStoreProjection.fold_raw(RUN_ID, source),
        ),
        ("staged_source_passes", StagedWriteFold.fold_raw),
        (
            "receipt_source_passes",
            lambda source: ReceiptFold.fold_raw(run_id=RUN_ID, events=source),
        ),
        ("pending_source_passes", PendingWorkFold.fold_raw),
    )
    for limit_name, fold in cases:
        source = CountingEventSequence(replay_events(event_count))
        fold(source)
        assert source.iterated <= event_count * limits[limit_name], (
            f"{limit_name}: expected <= {limits[limit_name]} full source passes, "
            f"got {source.iterated / event_count:.2f}"
        )


def test_large_csv_rowset_is_one_materialized_revision_not_per_row_fetch() -> None:
    """10k CSV rows fold from one revision with bounded result cardinality.

    The rowset contains distinct target arguments for every row: a regression
    which fetches or refolds the full event stream per row would make the source
    pass test above fail once row events are expanded, while this test makes the
    required large-data shape explicit and pins all rows through the actual
    staged-write reducer.
    """

    expected_rows = _LIMITS["fixtures"]["csv_rows"]
    state = StagedWriteFold.fold_raw(rowset_events(expected_rows))["stage_csv_capacity"]

    assert len(state.rows) == _LIMITS["operation_limits"]["rowset_rows_materialized"]
    assert state.row_counts.total == expected_rows
    assert state.row_counts.will_apply == expected_rows
    assert (
        len(state.will_apply_keys())
        == _LIMITS["operation_limits"]["rowset_result_rows"]
    )
    # The fold intentionally retains only render/decision state, not the
    # target arguments (which remain in the immutable revision payload).
    assert state.rows[0].row_key == "row_00000"
    assert state.rows[-1].row_key == f"row_{expected_rows - 1:05d}"


def test_nested_rescan_canary_is_detected_by_the_source_pass_contract() -> None:
    """Prove the metric itself rejects a classic per-event N+1 shape."""

    source = CountingEventSequence(replay_events(1000))
    # Deliberately hostile code: exactly the algorithm D11 must prevent.
    for _event in source:
        for _inner in source:
            break
    assert source.iterated > 1000 * _LIMITS["operation_limits"]["surface_source_passes"]
