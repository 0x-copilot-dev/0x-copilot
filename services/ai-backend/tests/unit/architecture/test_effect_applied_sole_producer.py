"""Repository guard for the A5 terminal-effect producer boundary.

``effect.applied`` is a side-effect receipt.  It must be constructed by the
worker-side ``EffectResultRecorder`` only, rather than being emitted by a
route, stager, adapter, or a second worker path.  The coordinator lands later
in A5, so a zero-producer repository is valid while that implementation is
still absent; the first producer must use the canonical module below.
"""

from __future__ import annotations

import ast
from pathlib import Path


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_CANONICAL_PRODUCER = Path("agent_runtime/effects/coordinator.py")
_EVENT_TYPE_LITERAL = "effect.applied"


def _is_effect_applied_enum_expression(node: ast.AST) -> bool:
    """Return whether ``node`` is ``LedgerEventType.EFFECT_APPLIED.value``."""

    return (
        isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "EFFECT_APPLIED"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "LedgerEventType"
    )


def _is_canonical_transport_construction(node: ast.Call) -> bool:
    """Recognise the transport-enum construction used for a terminal receipt."""

    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "RuntimeApiEventType"
        and any(_is_effect_applied_enum_expression(arg) for arg in node.args)
    )


class _EffectAppliedMarkerVisitor(ast.NodeVisitor):
    """Find producer markers while permitting the contract enum declaration."""

    def __init__(self) -> None:
        self._class_stack: list[str] = []
        self.lines: set[int] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if (
            node.value == _EVENT_TYPE_LITERAL
            and "LedgerEventType" not in self._class_stack
        ):
            self.lines.add(node.lineno)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _is_canonical_transport_construction(node):
            self.lines.add(node.lineno)
        self.generic_visit(node)


def _effect_applied_markers(source: str) -> list[int]:
    """Return line numbers that construct or inline the terminal event value.

    Deliberately inspect the AST, not raw text: comments and prose cannot
    create a false production producer.  A direct literal is prohibited in
    production because the Work Ledger contract is the event-value authority.
    """

    visitor = _EffectAppliedMarkerVisitor()
    visitor.visit(ast.parse(source))
    return sorted(visitor.lines)


def _forbidden_effect_applied_producers(source_root: Path) -> list[str]:
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        relative_path = path.relative_to(source_root)
        if relative_path == _CANONICAL_PRODUCER:
            continue
        for line in _effect_applied_markers(path.read_text(encoding="utf-8")):
            violations.append(f"{relative_path.as_posix()}:{line}")
    return sorted(violations)


def test_effect_applied_is_restricted_to_the_effect_result_recorder() -> None:
    """No production module outside the coordinator may construct the receipt."""

    assert _forbidden_effect_applied_producers(_SOURCE_ROOT) == []


def test_sole_producer_guard_accepts_the_canonical_coordinator(tmp_path: Path) -> None:
    coordinator = tmp_path / _CANONICAL_PRODUCER
    coordinator.parent.mkdir(parents=True)
    coordinator.write_text(
        "RuntimeApiEventType(LedgerEventType.EFFECT_APPLIED.value)\n",
        encoding="utf-8",
    )

    assert _forbidden_effect_applied_producers(tmp_path) == []


def test_sole_producer_guard_rejects_a_planted_rogue_production_module(
    tmp_path: Path,
) -> None:
    rogue = tmp_path / "runtime_worker" / "handlers" / "rogue_effect_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "RuntimeApiEventType(LedgerEventType.EFFECT_APPLIED.value)\n",
        encoding="utf-8",
    )

    assert _forbidden_effect_applied_producers(tmp_path) == [
        "runtime_worker/handlers/rogue_effect_apply.py:1"
    ]


def test_sole_producer_guard_rejects_a_planted_inline_event_literal(
    tmp_path: Path,
) -> None:
    rogue = tmp_path / "runtime_api" / "rogue_effect_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "event_type = 'effect.applied'\n",
        encoding="utf-8",
    )

    assert _forbidden_effect_applied_producers(tmp_path) == [
        "runtime_api/rogue_effect_apply.py:1"
    ]
