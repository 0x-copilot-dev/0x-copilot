"""Emit-side parity: every ledger event a producer can emit must be wire-legal.

The ledger crosses four boundaries — emit, persist, transport, project — and each
had its own partial guard except this one. Emitters convert a ``LedgerEventType``
*value* to the wire enum by value::

    event_type=RuntimeApiEventType(str(event_type_value))

A value present in one enum and absent from the other has no safe outcome. On the
emit side it raises ``ValueError`` deep inside a gateway, failing the operation it
was only supposed to describe. On the client side the mirror of this gap is worse
still: ``parseEnvelope`` drops the frame silently, so the projection that depends
on it becomes unreachable code that still compiles and still passes its own tests.

Both halves were real. ``gate.opened.v2`` / ``gate.resolved.v2`` were absent from
``RuntimeApiEventType`` while ``workspace/effects.py::_blocked`` emitted
``GATE_OPENED_V2`` unguarded, and ``artifact.presentation_decided`` was absent
from the TypeScript transport tuple while the canvas fold required it (PR #413).

``RuntimeApiEventType`` is legitimately the larger set — it also carries
``model_delta``, ``run_started`` and friends, which are not ledger events. The
invariant is therefore a subset relation, not equality.
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_api.schemas import RuntimeApiEventType


class TestLedgerEmitParity:
    def test_every_ledger_event_type_is_wire_convertible(self) -> None:
        """``LedgerEventType`` ⊆ ``RuntimeApiEventType``, by value."""

        wire_values = {event_type.value for event_type in RuntimeApiEventType}
        unconvertible = sorted(
            event_type.value
            for event_type in LedgerEventType
            if event_type.value not in wire_values
        )
        assert unconvertible == [], (
            "these ledger events cannot be emitted — RuntimeApiEventType(value) "
            f"raises for each: {unconvertible}"
        )

    def test_conversion_succeeds_for_every_ledger_event_type(self) -> None:
        """The invariant as the emitters actually exercise it.

        The subset check above compares value sets; this performs the exact call
        every emitter closure makes, so a future change to ``RuntimeApiEventType``
        construction (an alias map, a ``_missing_`` hook) cannot pass the first
        test while still raising here.
        """

        for event_type in LedgerEventType:
            assert RuntimeApiEventType(str(event_type.value)).value == event_type.value
