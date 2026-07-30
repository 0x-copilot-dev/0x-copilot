"""E2 D3: historic v2 surface replay remains a pure, read-only path."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from copilot_service_contracts.work_ledger import (
    load_ledger_golden_events,
    load_legacy_v2_replay_corpus,
)

from agent_runtime.surfaces_v2.legacy_v2_replay import (
    LegacyV2ReplayMode,
    project_legacy_v2_replay,
)


_REPO_ROOT = Path(__file__).resolve().parents[6]
_SPEC_FIXTURE_DIR = (
    _REPO_ROOT / "services/ai-backend/tests/unit/agent_runtime/surfaces/fixtures"
)
_FIXTURE_SOURCE_PATHS = {
    "packages/service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json",
    "services/ai-backend/tests/unit/agent_runtime/surfaces_v2/fixtures/surface_store_golden_state.json",
    "services/ai-backend/tests/evals/surfaces/baselines/baseline_replay.json",
    "services/ai-backend/tests/unit/agent_runtime/fixtures/e2_shadow_comparison_parity_vectors.json",
}


def _cases() -> list[dict[str, object]]:
    raw = load_legacy_v2_replay_corpus().get("cases")
    assert isinstance(raw, list)
    assert all(isinstance(item, dict) for item in raw)
    return raw


def _case(case_id: str) -> dict[str, object]:
    return next(item for item in _cases() if item["id"] == case_id)


def _events(case: dict[str, object]) -> list[dict[str, object]]:
    events = case["events"]
    assert isinstance(events, list)
    assert all(isinstance(event, dict) for event in events)
    return events


def _expected(case: dict[str, object]) -> dict[str, object]:
    expected = case["expected"]
    assert isinstance(expected, dict)
    return expected


def test_shared_sanitized_corpus_has_exact_read_only_projection() -> None:
    for case in _cases():
        events = _events(case)
        original = deepcopy(events)
        projection = project_legacy_v2_replay(events)
        assert projection.model_dump(mode="json") == _expected(case), case["id"]
        # Projection owns all copies it returns; append-only source rows stay
        # byte-for-byte untouched for later receipt/export verification.
        assert events == original, case["id"]
        assert all(surface.read_only is True for surface in projection.surfaces)


def test_every_checked_in_legacy_fixture_is_inventory_backed() -> None:
    corpus = load_legacy_v2_replay_corpus()
    sources = corpus.get("checked_in_sources")
    assert isinstance(sources, list)
    for source in sources:
        assert isinstance(source, dict)
        path = source.get("path")
        assert isinstance(path, str)
        assert (_REPO_ROOT / path).is_file(), source.get("id")

    # Pin the whole historic-fixture inventory rather than merely asserting
    # that a hand-picked subset still exists. New `*.spec.json` fixtures must
    # be added to the declarative corpus before they can be considered covered.
    inventory_paths = {source["path"] for source in sources}
    expected_paths = _FIXTURE_SOURCE_PATHS | {
        str(path.relative_to(_REPO_ROOT))
        for path in _SPEC_FIXTURE_DIR.glob("*.spec.json")
    }
    assert inventory_paths == expected_paths

    # The sole historic event stream is replayed directly, while its old
    # SurfaceStore output is used as an independent subject/metadata referee.
    golden = load_ledger_golden_events()
    events = golden.get("events")
    assert isinstance(events, list)
    projection = project_legacy_v2_replay(deepcopy(events))
    assert projection.mode is LegacyV2ReplayMode.LEGACY_V2
    expected_store = json.loads(
        (
            _REPO_ROOT
            / "services/ai-backend/tests/unit/agent_runtime/surfaces_v2/fixtures/surface_store_golden_state.json"
        ).read_text(encoding="utf-8")
    )
    expected_surfaces = expected_store["surfaces"]
    assert isinstance(expected_surfaces, list)
    by_subject = {surface.subject_id: surface for surface in projection.surfaces}
    assert set(by_subject) == {surface["surface_id"] for surface in expected_surfaces}
    for expected in expected_surfaces:
        surface = by_subject[expected["surface_id"]]
        assert surface.kind == expected["kind"]
        assert surface.title == expected["title"]
        assert surface.source_connector == expected["connector"]
        assert surface.source_op == expected["op"]
        assert surface.payload_ref == expected["payload_ref"]
        # Old opaque payload refs remain honest blanks. The compatibility
        # reader must not infer data from unrelated history.
        assert surface.state is None


def test_every_checked_in_legacy_spec_remains_renderable_as_exact_data() -> None:
    for spec_path in sorted(_SPEC_FIXTURE_DIR.glob("*.spec.json")):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        assert isinstance(spec, dict)
        archetype = spec.get("archetype")
        assert isinstance(archetype, str)
        subject_id = f"connector:historic:{spec_path.stem}"
        events = [
            {
                "event_id": f"tool-{spec_path.stem}",
                "event_type": "tool_result",
                "sequence_no": 1,
                "payload": {
                    "call_id": subject_id,
                    "output": {"fixture": spec_path.stem},
                },
            },
            {
                "event_id": f"surface-{spec_path.stem}",
                "event_type": "surface.created",
                "sequence_no": 2,
                "payload": {
                    "surface_id": subject_id,
                    "kind": archetype,
                    "payload_ref": f"call:{subject_id}",
                },
            },
            {
                "event_id": f"spec-{spec_path.stem}",
                "event_type": "surface_spec_generated",
                "sequence_no": 3,
                "payload": {"surface_uri": subject_id, "spec": spec},
            },
        ]
        projection = project_legacy_v2_replay(events)
        assert len(projection.surfaces) == 1
        surface = projection.surfaces[0]
        assert surface.read_only is True
        assert surface.kind == archetype
        assert surface.state == {"spec": spec, "data": {"fixture": spec_path.stem}}


def test_reconnect_duplicate_is_deduplicated_after_sequence_ordering() -> None:
    case = _case("connector_subject_declared_reference_hydration")
    original = _events(case)
    replayed = [*deepcopy(original), deepcopy(original[0])]
    replayed[-1]["sequence_no"] = 99
    assert project_legacy_v2_replay(replayed) == project_legacy_v2_replay(original)


def test_legacy_writes_are_not_upgraded_to_a_canonical_writable_effect() -> None:
    events = load_ledger_golden_events().get("events")
    assert isinstance(events, list)
    projection = project_legacy_v2_replay(deepcopy(events))
    # Legacy write/gate rows remain visible to the existing semantic reader,
    # but this *surface* reader never returns stage ids, approval controls, or
    # an apply/enqueue capability that could accidentally execute an old run.
    dumped = projection.model_dump(mode="json")
    encoded = json.dumps(dumped, sort_keys=True)
    assert "stage_id" not in encoded
    assert "apply" not in encoded
    assert "enqueue" not in encoded
    assert all(surface.read_only is True for surface in projection.surfaces)


def test_evaluation_metadata_is_explicitly_quarantined_from_event_replay() -> None:
    corpus = load_legacy_v2_replay_corpus()
    sources = corpus.get("checked_in_sources")
    assert isinstance(sources, list)
    baseline = next(
        source for source in sources if source["id"] == "surface_eval_baseline"
    )
    assert baseline["replay"] == "quarantined_not_run_events"
    # It has no event array, so callers cannot accidentally treat evaluator
    # aggregate output as a durable run stream or fabricate a surface from it.
    assert "events" not in json.loads(
        (_REPO_ROOT / str(baseline["path"])).read_text(encoding="utf-8")
    )

    shadow_vectors = next(
        source for source in sources if source["id"] == "e2_shadow_comparison_vectors"
    )
    assert shadow_vectors["replay"] == "quarantined_not_run_events"
    assert "events" not in json.loads(
        (_REPO_ROOT / str(shadow_vectors["path"])).read_text(encoding="utf-8")
    )


def test_reader_1_state_shape_is_not_widened_by_an_upstream_fold() -> None:
    """The reader publishes ``{spec?, data}`` and nothing the fold learns later.

    ``SurfaceContentProjection`` now also resolves ``source`` (a surface's
    connector/tool provenance) so the live canvas can name the tool on a
    spec-less surface. This reader must not inherit it: its projection is a
    cross-language contract implemented twice (Python here, TypeScript in
    ``packages/api-types/src/legacyV2Replay.ts``) and pinned to the shared
    ``legacy_v2_replay_corpus.json`` vectors, so widening the shape is a
    ``reader_version`` change made on both sides at once — never a side effect.

    The same two facts are already published as first-class
    ``source_connector`` / ``source_op``, so nothing is lost by withholding them
    from ``state``.
    """

    case = _case("connector_subject_declared_reference_hydration")
    projection = project_legacy_v2_replay(_events(case))

    assert projection.reader_version == 1
    surface = projection.surfaces[0]
    assert surface.state is not None
    assert set(surface.state) <= {"spec", "data"}
    # The provenance the source event carried is published, just not in `state`.
    assert (surface.source_connector, surface.source_op) == ("linear", "get_issue")
