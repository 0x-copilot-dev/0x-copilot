"""Machine-readable Python half of the Canvas lifecycle differential corpus.

This is intentionally test-only: it lets the TypeScript suite execute the real
Python fold over the same fixture without creating a deployable dependency
between the chat surface and the AI backend.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[6]
for _path in (
    _ROOT / "services" / "ai-backend" / "src",
    _ROOT / "packages" / "service-contracts" / "src",
    _ROOT / "packages" / "audit-chain" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_runtime.presentation.lifecycle import (  # noqa: E402
    CanvasLifecycleProjection,
)

_CORPUS_PATH = (
    _ROOT
    / "packages"
    / "service-contracts"
    / "src"
    / "copilot_service_contracts"
    / "canvas_lifecycle_corpus.json"
)


def load_corpus() -> Mapping[str, object]:
    raw = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Canvas lifecycle corpus must be a JSON object")
    return raw


def project_corpus(corpus: Mapping[str, object] | None = None) -> dict[str, object]:
    source = corpus or load_corpus()
    cases = source.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Canvas lifecycle corpus requires a cases array")
    return {
        "schema_version": source.get("schema_version"),
        "cases": [project_case(case) for case in cases],
    }


def project_case(raw_case: object) -> dict[str, object]:
    if not isinstance(raw_case, Mapping):
        raise ValueError("Canvas lifecycle corpus case must be an object")
    case_id = raw_case.get("id")
    events = raw_case.get("events")
    if not isinstance(case_id, str) or not isinstance(events, list):
        raise ValueError("Canvas lifecycle corpus case requires id and events")
    return {
        "id": case_id,
        "prefixes": [
            snapshot(CanvasLifecycleProjection.fold(events[:end]))
            for end in range(len(events) + 1)
        ],
    }


def snapshot(projection: CanvasLifecycleProjection) -> dict[str, object]:
    value = projection
    return {
        "lifecycle": str(value.lifecycle),
        "tabs": [subject_snapshot(subject) for subject in value.tabs],
        "activeSubjectKey": value.active_subject_key,
        "pendingSubjectKeys": list(value.pending_subject_keys),
        "terminalReceipt": (
            subject_snapshot(value.terminal_receipt)
            if value.terminal_receipt is not None
            else None
        ),
        "activityCount": value.activity_count,
        "failure": value.failure,
        "hasFinalResponse": value.has_final_response,
        "terminal": value.terminal,
        "terminalStatus": value.terminal_status,
    }


def subject_snapshot(subject: object) -> dict[str, object]:
    return {
        "key": subject.key,
        "kind": str(subject.kind),
        "subjectId": subject.subject_id,
        "title": subject.title,
        "revision": subject.revision,
        "lastSeq": subject.last_sequence_no,
        "priority": subject.priority,
        "rendererHint": subject.renderer_hint,
    }


if __name__ == "__main__":
    print(json.dumps(project_corpus(), separators=(",", ":"), sort_keys=True))
