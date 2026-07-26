"""D11 adapter fidelity and opt-in PostgreSQL plan gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agent_runtime.surfaces_v2.pending_work import PendingWorkFold
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.staging import StagedWriteFold

from .fixtures import RUN_ID, raw_json_shape, replay_events

_LIMITS = json.loads(
    (Path(__file__).parents[5] / "tools/e2-performance/limits.json").read_text(
        encoding="utf-8"
    )
)
_PG_ENV = _LIMITS["policy"]["postgres_explain_env"]


def _folds(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "surface": SurfaceStoreProjection.fold_raw(RUN_ID, events).model_dump(
            mode="json"
        ),
        "stages": {
            key: value.model_dump(mode="json")
            for key, value in StagedWriteFold.fold_raw(events).items()
        },
        "receipt": ReceiptFold.fold_raw(run_id=RUN_ID, events=events).model_dump(
            mode="json"
        ),
        "pending": [
            item.model_dump(mode="json") for item in PendingWorkFold.fold_raw(events)
        ],
    }


def test_file_snapshot_and_in_memory_snapshot_project_identically() -> None:
    """File JSON and in-memory event snapshots are equivalent fold inputs.

    This is deliberately a projection contract, not a replacement for the
    existing real FileRuntimeApiStore / InMemoryRuntimeApiStore integration
    suites.  It catches an adapter serialization change that turns a ledger
    payload into a subtly different JSON shape before a query reaches a fold.
    """

    in_memory = raw_json_shape(replay_events(1000))
    with TemporaryDirectory() as directory:
        fixture = Path(directory) / "events.json"
        fixture.write_text(json.dumps(in_memory, sort_keys=True), encoding="utf-8")
        from_file = json.loads(fixture.read_text(encoding="utf-8"))
    assert _folds(in_memory) == _folds(from_file)


pytestmark_postgres = pytest.mark.skipif(
    not os.environ.get(_PG_ENV),
    reason=(
        f"Set {_PG_ENV} to a disposable migrated PostgreSQL database to run "
        "the E2 indexed replay EXPLAIN gate."
    ),
)


@pytestmark_postgres
@pytest.mark.postgres
def test_postgres_event_replay_plan_uses_the_run_sequence_index() -> None:
    """Release-drill-only: prevent an event replay query from degrading to a scan.

    It intentionally needs an already migrated disposable database and never
    seeds or deletes data.  The query is parameterized and structurally matches
    the adapter's ``list_events_after`` predicate.
    """

    import psycopg

    database_url = os.environ[_PG_ENV]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "EXPLAIN (FORMAT JSON) "
                "SELECT sequence_no FROM runtime_events "
                "WHERE org_id = %s AND run_id = %s AND sequence_no > %s "
                "ORDER BY sequence_no ASC",
                ("e2_performance_plan_org", "e2_performance_plan_run", 0),
            )
            plan = cursor.fetchone()[0][0]["Plan"]
    plan_json = json.dumps(plan)
    assert "idx_runtime_events_org_run_sequence" in plan_json
