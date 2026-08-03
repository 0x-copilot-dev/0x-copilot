"""Machine-readable Python half of the assistant-turn parts differential corpus.

This is intentionally test-only: it lets the TypeScript suite execute the real
Python fold over the same fixture without creating a deployable dependency
between the chat surface and the AI backend.

The fold exists in both runtimes by necessity -- Python persists the turn at
seal time, TypeScript renders it live -- so this corpus is what stops the two
implementations drifting apart. Every case is projected at EVERY replay prefix,
because the open/close part logic is exactly the kind of state machine that
agrees on the final answer while disagreeing mid-stream.
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

from agent_runtime.presentation.turn_parts import (  # noqa: E402
    TurnPart,
    TurnPartsProjection,
)

_CORPUS_PATH = (
    _ROOT
    / "packages"
    / "service-contracts"
    / "src"
    / "copilot_service_contracts"
    / "turn_parts_corpus.json"
)

#: Must match the TypeScript twin's synthetic clock exactly -- the corpus carries
#: no timestamps, and a differential over derived time is only meaningful when
#: both sides derive it identically.
_EPOCH_MS = 1_700_000_000_000
_STEP_MS = 1_000


def load_corpus() -> Mapping[str, object]:
    raw = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Turn parts corpus must be a JSON object")
    return raw


def hydrate(events: list[object]) -> list[Mapping[str, object]]:
    """Give each transport-lite event the identity fields the fold reads."""
    hydrated: list[Mapping[str, object]] = []
    for index, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise ValueError("Turn parts corpus event must be an object")
        sequence_no = raw.get("sequence_no")
        if not isinstance(sequence_no, int):
            raise ValueError("Turn parts corpus event requires sequence_no")
        hydrated.append(
            {
                **raw,
                "event_id": f"evt-{sequence_no}-{index}",
                "created_at_ms": _EPOCH_MS + sequence_no * _STEP_MS,
            }
        )
    return hydrated


def project_corpus(corpus: Mapping[str, object] | None = None) -> dict[str, object]:
    source = corpus or load_corpus()
    cases = source.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Turn parts corpus requires a cases array")
    return {
        "schema_version": source.get("schema_version"),
        "cases": [project_case(case) for case in cases],
    }


def project_case(raw_case: object) -> dict[str, object]:
    if not isinstance(raw_case, Mapping):
        raise ValueError("Turn parts corpus case must be an object")
    case_id = raw_case.get("id")
    events = raw_case.get("events")
    if not isinstance(case_id, str) or not isinstance(events, list):
        raise ValueError("Turn parts corpus case requires id and events")
    hydrated = hydrate(events)
    return {
        "id": case_id,
        "prefixes": [
            [snapshot(part) for part in TurnPartsProjection.fold(hydrated[:end])]
            for end in range(len(hydrated) + 1)
        ],
    }


def snapshot(part: TurnPart) -> dict[str, object]:
    return {
        "type": str(part.kind),
        "text": part.text,
        "seq": part.seq,
        "status": str(part.status),
        "startedAtMs": part.started_at_ms,
        "updatedAtMs": part.updated_at_ms,
    }


if __name__ == "__main__":
    print(json.dumps(project_corpus(), separators=(",", ":"), sort_keys=True))
