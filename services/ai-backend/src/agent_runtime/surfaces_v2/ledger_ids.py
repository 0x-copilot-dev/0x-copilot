"""Ledger-id codec: the user-visible ``r<short>·<seq>`` id (SDR §5, PRD-A1 D4).

Pure presentation over an existing ``(run_id, sequence_no)`` pair — the id is
never stored and never parsed back into a run lookup. Both the format and parse
rules read their constants from the SSOT ``ledger_id`` block in
``work_ledger.json`` so the ts codec (``formatLedgerId`` / ``parseLedgerId`` in
``packages/api-types``) and this one cannot drift.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import PositiveInt

from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.execution.contracts import RuntimeContract


class LedgerIdFormatError(ValueError):
    """Raised when a ledger id cannot be formatted or parsed.

    Carries only a safe, actionable message — never internal detail.
    """


class _Spec:
    """Ledger-id constants, read once from the SSOT ``ledger_id`` block."""

    _BLOCK: ClassVar[dict[str, object]] = {
        str(key): value
        for key, value in (load_work_ledger_contract().get("ledger_id") or {}).items()
    }
    PREFIX: ClassVar[str] = str(_BLOCK["prefix"])
    SHORT_LEN: ClassVar[int] = int(_BLOCK["short_len"])  # type: ignore[arg-type]
    SEPARATOR: ClassVar[str] = str(_BLOCK["separator"])
    SEQ_MIN_WIDTH: ClassVar[int] = int(_BLOCK["seq_min_width"])  # type: ignore[arg-type]


class _Patterns:
    """Pre-compiled parse pattern, built from the SSOT constants.

    Charset is ``[a-z0-9]`` (not hex-only): run ids follow ``_ID_PATTERN`` in
    ``execution/contracts.py`` and may carry non-hex chars.
    """

    LEDGER_ID: ClassVar[re.Pattern[str]] = re.compile(
        "^"
        + re.escape(_Spec.PREFIX)
        + r"([a-z0-9]{"
        + str(_Spec.SHORT_LEN)
        + r"})"
        + re.escape(_Spec.SEPARATOR)
        + r"([0-9]{"
        + str(_Spec.SEQ_MIN_WIDTH)
        + r",})"
        + "$"
    )


class _Messages:
    """Safe, actionable messages surfaced through ``LedgerIdFormatError``."""

    @staticmethod
    def sequence_below_one(sequence_no: object) -> str:
        return f"sequence_no must be >= 1 to form a ledger id; got {sequence_no!r}"

    @staticmethod
    def run_id_too_short(short_len: int) -> str:
        return (
            f"run_id must normalise to at least {short_len} characters "
            f"to form a ledger id"
        )

    @staticmethod
    def malformed(text: object) -> str:
        return f"not a valid ledger id: {text!r}"


class ParsedLedgerId(RuntimeContract):
    """The two parts a ledger id decodes to (never a run handle)."""

    run_short: str
    sequence_no: PositiveInt


class LedgerIdCodec:
    """Formats/parses the user-visible ledger id ``r<short>·<seq>``."""

    @classmethod
    def format(cls, run_id: str, sequence_no: int) -> str:
        """Render ``(run_id, sequence_no)`` as ``r<short>·<seq>``.

        ``short`` = first ``SHORT_LEN`` chars of ``run_id.lower()`` with ``-``
        stripped; ``seq`` = ``sequence_no`` zero-padded to ``SEQ_MIN_WIDTH``,
        growing beyond without truncation. ``sequence_no < 1`` or a normalised
        run id shorter than ``SHORT_LEN`` raises ``LedgerIdFormatError``.
        """

        if sequence_no < 1:
            raise LedgerIdFormatError(_Messages.sequence_below_one(sequence_no))
        normalized = run_id.lower().replace("-", "")
        if len(normalized) < _Spec.SHORT_LEN:
            raise LedgerIdFormatError(_Messages.run_id_too_short(_Spec.SHORT_LEN))
        short = normalized[: _Spec.SHORT_LEN]
        seq = f"{sequence_no:0{_Spec.SEQ_MIN_WIDTH}d}"
        return f"{_Spec.PREFIX}{short}{_Spec.SEPARATOR}{seq}"

    @classmethod
    def parse(cls, text: str) -> ParsedLedgerId:
        """Decode ``r<short>·<seq>`` into its parts.

        A value that does not match the SSOT format raises
        ``LedgerIdFormatError`` — never a silent pass.
        """

        match = _Patterns.LEDGER_ID.fullmatch(text) if isinstance(text, str) else None
        if match is None:
            raise LedgerIdFormatError(_Messages.malformed(text))
        return ParsedLedgerId(run_short=match.group(1), sequence_no=int(match.group(2)))


__all__ = [
    "LedgerIdCodec",
    "LedgerIdFormatError",
    "ParsedLedgerId",
]
