"""Canaries for the shared A5 terminal-effect producer boundary guard."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.effects.conformance import (
    CANONICAL_EFFECT_RESULT_PRODUCER,
    canonical_effect_result_producer_present,
    effect_applied_producer_violations,
)


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"


def test_effect_applied_is_restricted_to_the_effect_result_recorder() -> None:
    assert canonical_effect_result_producer_present(_SOURCE_ROOT)
    assert effect_applied_producer_violations(_SOURCE_ROOT) == ()


def test_sole_producer_guard_accepts_the_canonical_coordinator(tmp_path: Path) -> None:
    coordinator = tmp_path / CANONICAL_EFFECT_RESULT_PRODUCER
    coordinator.parent.mkdir(parents=True)
    coordinator.write_text(
        "class EffectResultRecorder: pass\n"
        "_EVENT_APPLIED = LedgerEventType.EFFECT_APPLIED.value\n",
        encoding="utf-8",
    )

    assert canonical_effect_result_producer_present(tmp_path)
    assert effect_applied_producer_violations(tmp_path) == ()


def test_sole_producer_guard_rejects_a_planted_rogue_production_module(
    tmp_path: Path,
) -> None:
    rogue = tmp_path / "runtime_worker" / "handlers" / "rogue_effect_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "RuntimeApiEventType(LedgerEventType.EFFECT_APPLIED.value)\n",
        encoding="utf-8",
    )

    assert effect_applied_producer_violations(tmp_path) == (
        "runtime_worker/handlers/rogue_effect_apply.py:1",
    )


def test_sole_producer_guard_rejects_a_planted_inline_event_literal(
    tmp_path: Path,
) -> None:
    rogue = tmp_path / "runtime_api" / "rogue_effect_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("event_type = 'effect.applied'\n", encoding="utf-8")

    assert effect_applied_producer_violations(tmp_path) == (
        "runtime_api/rogue_effect_apply.py:1",
    )
