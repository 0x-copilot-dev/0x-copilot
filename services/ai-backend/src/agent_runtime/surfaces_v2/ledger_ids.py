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
from uuid import UUID

from pydantic import PositiveInt

from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.execution.contracts import RuntimeContract


class LedgerIdFormatError(ValueError):
    """Raised when a ledger id cannot be formatted or parsed.

    Carries only a safe, actionable message — never internal detail.
    """


class ArtifactEffectFormatError(ValueError):
    """Raised when a v2.1 id or opaque reference is malformed."""


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


class _ArtifactEffectSpec:
    """Identifier/reference constants read once from the Work Ledger SSOT."""

    _CONTRACT: ClassVar[dict[str, object]] = load_work_ledger_contract()
    IDENTIFIERS: ClassVar[dict[str, dict[str, object]]] = {
        str(key): dict(value)
        for key, value in dict(_CONTRACT.get("identifiers") or {}).items()
    }
    REFERENCES: ClassVar[dict[str, object]] = dict(_CONTRACT.get("references") or {})
    DIGESTS: ClassVar[dict[str, object]] = dict(_CONTRACT.get("digests") or {})
    MAX_REFERENCE_LENGTH: ClassVar[int] = int(REFERENCES["max_length"])  # type: ignore[arg-type]
    CLAIM_ID_MAX_LENGTH: ClassVar[int] = int(  # type: ignore[arg-type]
        REFERENCES["claim_id_max_length"]
    )
    MAX_SAFE_INTEGER: ClassVar[int] = int(DIGESTS["max_safe_integer"])  # type: ignore[arg-type]


_UUID_CANONICAL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CLAIM_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class _PrefixedUuidCodec:
    """Strict canonical UUID4/UUID7 codec with a contract-defined prefix."""

    CONTRACT_KEY: ClassVar[str]

    @classmethod
    def _prefix(cls) -> str:
        return str(_ArtifactEffectSpec.IDENTIFIERS[cls.CONTRACT_KEY]["prefix"])

    @classmethod
    def format(cls, uuid_value: str | UUID) -> str:
        raw = str(uuid_value)
        canonical = cls._parse_uuid(raw)
        return f"{cls._prefix()}{canonical}"

    @classmethod
    def parse(cls, text: str) -> str:
        prefix = cls._prefix()
        if not isinstance(text, str) or not text.startswith(prefix):
            raise ArtifactEffectFormatError(f"not a valid {cls.CONTRACT_KEY}: {text!r}")
        raw = text[len(prefix) :]
        return cls._parse_uuid(raw)

    @classmethod
    def _parse_uuid(cls, raw: str) -> str:
        if not _UUID_CANONICAL.fullmatch(raw):
            raise ArtifactEffectFormatError(
                f"{cls.CONTRACT_KEY} must contain a canonical lowercase UUID4 or UUID7"
            )
        try:
            parsed = UUID(raw)
        except ValueError as exc:  # pragma: no cover - regex is already strict
            raise ArtifactEffectFormatError(
                f"{cls.CONTRACT_KEY} must contain a valid UUID"
            ) from exc
        allowed = {
            int(value)
            for value in _ArtifactEffectSpec.IDENTIFIERS[cls.CONTRACT_KEY][
                "uuid_versions"
            ]  # type: ignore[index]
        }
        if parsed.version not in allowed or str(parsed) != raw:
            raise ArtifactEffectFormatError(
                f"{cls.CONTRACT_KEY} must contain a canonical lowercase UUID4 or UUID7"
            )
        return raw


class OperationIdCodec(_PrefixedUuidCodec):
    CONTRACT_KEY = "operation_id"


class ArtifactIdCodec(_PrefixedUuidCodec):
    CONTRACT_KEY = "artifact_id"


class EffectStageIdCodec(_PrefixedUuidCodec):
    CONTRACT_KEY = "effect_stage_id"


class ParsedArtifactContentRef(RuntimeContract):
    artifact_id: str
    revision: PositiveInt


class ArtifactContentRefCodec:
    _PATTERN = re.compile(r"^artifact://([^/]+)/revisions/([1-9][0-9]*)$")

    @classmethod
    def format(cls, artifact_id: str, revision: int) -> str:
        ArtifactIdCodec.parse(artifact_id)
        _require_positive_revision(revision)
        return f"artifact://{artifact_id}/revisions/{revision}"

    @classmethod
    def parse(cls, text: str) -> ParsedArtifactContentRef:
        match = _reference_match(cls._PATTERN, text, "artifact content reference")
        artifact_id = match.group(1)
        ArtifactIdCodec.parse(artifact_id)
        return ParsedArtifactContentRef(
            artifact_id=artifact_id, revision=_parse_revision(match.group(2))
        )


class ParsedOperationArgsRef(RuntimeContract):
    operation_id: str


class OperationArgsRefCodec:
    _PATTERN = re.compile(r"^operation://([^/]+)/args$")

    @classmethod
    def format(cls, operation_id: str) -> str:
        OperationIdCodec.parse(operation_id)
        return f"operation://{operation_id}/args"

    @classmethod
    def parse(cls, text: str) -> ParsedOperationArgsRef:
        match = _reference_match(cls._PATTERN, text, "operation args reference")
        operation_id = match.group(1)
        OperationIdCodec.parse(operation_id)
        return ParsedOperationArgsRef(operation_id=operation_id)


class ParsedProposalRef(RuntimeContract):
    stage_id: str
    revision: PositiveInt


class ProposalUriCodec:
    _PATTERN = re.compile(r"^proposal://([^/]+)/revisions/([1-9][0-9]*)$")

    @classmethod
    def format(cls, stage_id: str, revision: int) -> str:
        EffectStageIdCodec.parse(stage_id)
        _require_positive_revision(revision)
        return f"proposal://{stage_id}/revisions/{revision}"

    @classmethod
    def parse(cls, text: str) -> ParsedProposalRef:
        match = _reference_match(cls._PATTERN, text, "proposal reference")
        stage_id = match.group(1)
        EffectStageIdCodec.parse(stage_id)
        return ParsedProposalRef(
            stage_id=stage_id, revision=_parse_revision(match.group(2))
        )


class ParsedEffectReceiptRef(RuntimeContract):
    stage_id: str
    claim_id: str


class EffectReceiptRefCodec:
    _PATTERN = re.compile(r"^receipt://effects/([^/]+)/([^/]+)$")

    @classmethod
    def format(cls, stage_id: str, claim_id: str) -> str:
        EffectStageIdCodec.parse(stage_id)
        _validate_claim_id(claim_id)
        return f"receipt://effects/{stage_id}/{claim_id}"

    @classmethod
    def parse(cls, text: str) -> ParsedEffectReceiptRef:
        match = _reference_match(cls._PATTERN, text, "effect receipt reference")
        stage_id, claim_id = match.groups()
        EffectStageIdCodec.parse(stage_id)
        _validate_claim_id(claim_id)
        return ParsedEffectReceiptRef(stage_id=stage_id, claim_id=claim_id)


class ParsedWorkspaceTargetRef(RuntimeContract):
    grant_id: str
    path_token: str


class WorkspaceTargetRefCodec:
    _PATTERN = re.compile(r"^workspace-target://([^/]+)/([^/]+)$")

    @classmethod
    def format(cls, grant_id: str, path_token: str) -> str:
        _validate_opaque_token(grant_id, "grant_id")
        _validate_opaque_token(path_token, "path_token")
        return f"workspace-target://{grant_id}/{path_token}"

    @classmethod
    def parse(cls, text: str) -> ParsedWorkspaceTargetRef:
        match = _reference_match(cls._PATTERN, text, "workspace target reference")
        grant_id, path_token = match.groups()
        _validate_opaque_token(grant_id, "grant_id")
        _validate_opaque_token(path_token, "path_token")
        return ParsedWorkspaceTargetRef(grant_id=grant_id, path_token=path_token)


def _reference_match(pattern: re.Pattern[str], text: str, label: str) -> re.Match[str]:
    if (
        not isinstance(text, str)
        or len(text) > _ArtifactEffectSpec.MAX_REFERENCE_LENGTH
        or text != text.strip()
    ):
        raise ArtifactEffectFormatError(f"not a valid {label}: {text!r}")
    match = pattern.fullmatch(text)
    if match is None or any(part in {".", ".."} for part in match.groups()):
        raise ArtifactEffectFormatError(f"not a valid {label}: {text!r}")
    return match


def _require_positive_revision(revision: int) -> None:
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or revision > _ArtifactEffectSpec.MAX_SAFE_INTEGER
    ):
        raise ArtifactEffectFormatError(
            "revision must be a positive cross-language safe integer"
        )


def _parse_revision(text: str) -> int:
    revision = int(text)
    _require_positive_revision(revision)
    return revision


def _validate_claim_id(claim_id: str) -> None:
    if (
        not isinstance(claim_id, str)
        or len(claim_id) > _ArtifactEffectSpec.CLAIM_ID_MAX_LENGTH
        or not _CLAIM_ID.fullmatch(claim_id)
        or ".." in claim_id
    ):
        raise ArtifactEffectFormatError("claim_id must be a safe opaque token")


def _validate_opaque_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _OPAQUE_TOKEN.fullmatch(value):
        raise ArtifactEffectFormatError(f"{field_name} must be a safe opaque token")


__all__ = [
    "ArtifactContentRefCodec",
    "ArtifactEffectFormatError",
    "ArtifactIdCodec",
    "EffectReceiptRefCodec",
    "EffectStageIdCodec",
    "LedgerIdCodec",
    "LedgerIdFormatError",
    "OperationArgsRefCodec",
    "OperationIdCodec",
    "ParsedArtifactContentRef",
    "ParsedEffectReceiptRef",
    "ParsedLedgerId",
    "ParsedOperationArgsRef",
    "ParsedProposalRef",
    "ParsedWorkspaceTargetRef",
    "ProposalUriCodec",
    "WorkspaceTargetRefCodec",
]
