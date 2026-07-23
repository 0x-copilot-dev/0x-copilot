"""Ledger-id codec: format/parse round-trip + adversarial malformed inputs.

The round-trip property is the DoD (asserted in both languages): for every
``(run_id, sequence_no, ledger_id)`` triple in the golden fixture,
``format(run_id, seq) == ledger_id`` and ``parse(ledger_id)`` decodes to the
normalised short + seq.
"""

from __future__ import annotations

import pytest

from copilot_service_contracts.work_ledger import load_ledger_golden_events

from agent_runtime.surfaces_v2.ledger_ids import (
    LedgerIdCodec,
    LedgerIdFormatError,
)


class GoldenIdMixin:
    """Resolves the golden ``(run_id, sequence_no, ledger_id)`` triples."""

    @staticmethod
    def golden_triples() -> list[dict[str, object]]:
        golden = load_ledger_golden_events()
        triples = golden["golden_ids"]
        assert isinstance(triples, list)
        return [t for t in triples if isinstance(t, dict)]

    @staticmethod
    def normalized_short(run_id: str) -> str:
        return run_id.lower().replace("-", "")[:3]


class TestLedgerIdFormat(GoldenIdMixin):
    def test_format_matches_golden_triples(self) -> None:
        triples = self.golden_triples()
        assert triples  # fixture is non-empty
        for triple in triples:
            run_id = triple["run_id"]
            seq = triple["sequence_no"]
            expected = triple["ledger_id"]
            assert isinstance(run_id, str) and isinstance(seq, int)
            assert LedgerIdCodec.format(run_id, seq) == expected

    def test_format_pads_to_three_and_grows(self) -> None:
        run_id = "a7f3c9d2e5b14f60a7f3c9d2e5b14f60"
        assert LedgerIdCodec.format(run_id, 7) == "ra7f·007"
        assert LedgerIdCodec.format(run_id, 42) == "ra7f·042"
        assert LedgerIdCodec.format(run_id, 1042) == "ra7f·1042"

    def test_format_strips_dashes_and_lowercases(self) -> None:
        # `-` stripped, upper-cased chars normalised, then first 3 taken.
        assert LedgerIdCodec.format("A-B-C-D-E-F", 1) == "rabc·001"

    def test_format_rejects_sequence_below_one(self) -> None:
        run_id = "a7f3c9d2e5b14f60a7f3c9d2e5b14f60"
        for bad in (0, -1, -1000):
            with pytest.raises(LedgerIdFormatError) as exc_info:
                LedgerIdCodec.format(run_id, bad)
            assert "sequence_no must be >= 1" in str(exc_info.value)

    def test_format_rejects_short_run_id(self) -> None:
        for short_run in ("", "ab", "a-", "--"):
            with pytest.raises(LedgerIdFormatError) as exc_info:
                LedgerIdCodec.format(short_run, 5)
            assert "at least 3 characters" in str(exc_info.value)


class TestLedgerIdParse(GoldenIdMixin):
    def test_parse_round_trips_golden_triples(self) -> None:
        for triple in self.golden_triples():
            run_id = triple["run_id"]
            seq = triple["sequence_no"]
            ledger_id = triple["ledger_id"]
            assert isinstance(run_id, str) and isinstance(ledger_id, str)
            parsed = LedgerIdCodec.parse(ledger_id)
            assert parsed.run_short == self.normalized_short(run_id)
            assert parsed.sequence_no == seq

    def test_parse_rejects_malformed(self) -> None:
        malformed = [
            "",  # empty
            "xa7f·007",  # wrong prefix
            "ra7f.007",  # ASCII dot for the middle dot
            "ra7f*007",  # ASCII star for the middle dot
            "rA7F·007",  # uppercase short
            "ra7·007",  # 2-char short
            "ra7f·07",  # 2-digit seq
            "ra7f·007xx",  # trailing junk
            "ra7f·007 ",  # trailing space
            "ra7f-007",  # dash instead of separator
            "ra7f·",  # missing seq
        ]
        for text in malformed:
            with pytest.raises(LedgerIdFormatError) as exc_info:
                LedgerIdCodec.parse(text)
            assert "not a valid ledger id" in str(exc_info.value)
