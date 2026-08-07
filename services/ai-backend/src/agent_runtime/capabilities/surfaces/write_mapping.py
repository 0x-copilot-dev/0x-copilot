"""Write mapping — the model picks the tool and the field NAMES, never a value.

The read path's shaping question is *"how should this payload be drawn?"*. This
module asks the write path's mirror question: *"which connector op writes this
record, and which of its schema fields does each edited column correspond to?"*
Both are answered by the same nano model through the same
:class:`~.generator.SpecCompletionPort` seam and the same
:class:`~agent_runtime.surfaces_v2.shaping_policy.ShapingModelResolver` — there
is deliberately no second model resolution in the tree.

**The one property that makes an LLM-selected write safe.** ``StagedRow``'s
``target_args`` are *"the EXACT connector-op args the shared dispatcher sends for
THIS row, verbatim"*. There is no re-composition between the diff the user
approves and the request that leaves the machine, so a model cannot paraphrase a
value in between. That property is worth nothing, though, if the model is what
*produced* the value in the first place — so this module bounds the answer to
three things:

* which **op** writes the record,
* which **arg name** each edited column maps onto,
* where each arg's value **comes from** — never what it *is*.

That is the write side of the render side's select-vs-generate line: the model
chooses shape, never content.

**Two enforcement layers, not one.** The answer contract (:class:`ArgBinding`)
is source-referencing, so the ordinary path gives the model no slot to type a
value into. It may still emit :attr:`ArgSourceKind.LITERAL`, and that member
exists on purpose: models paraphrase, and a paraphrase rejected as *"this value
came from nowhere"* is diagnosable, where the same paraphrase rejected as a
schema error reads as a broken prompt. Every composed ``target_args`` — literal,
referenced, or nested — is then audited by :class:`ArgProvenanceAudit`, which
admits a value only if the user typed it or the connector read it. A literal
that is byte-identical to the user's own value passes (the property under guard
is the VALUE, not the citation); anything else is rejected and NOTHING is
staged. Only an OBJECT may be assembled out of admissible parts, because the
envelope a connector op wants is shape; a LIST is data, in the order and with
the membership it was read or typed in, and must match exactly.

**Failure is loud here, and that inverts the read path deliberately.** A shaping
call that cannot run degrades to the deterministic floor because the worst case
is a plainer table. A *write* mapping that cannot run must raise: the user
pressed Save, and the failure mode of a quiet degrade is a user who believes a
connector was updated when nothing was sent. Every path out of this module is
either a validated set of :class:`StagedRow`s or a typed
:class:`WriteMappingError`.

Nothing here stages, emits, approves, or dispatches — see
:mod:`.write_back` for the lane that carries the rows to the staging engine.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, ValidationError, model_validator

from agent_runtime.capabilities.surfaces.generator import (
    ShapingCredentials,
    ShapingModelBuild,
    SpecCompletionPort,
)
from agent_runtime.execution.contracts import JsonObject, JsonValue, RuntimeContract
from agent_runtime.surfaces_v2.rowset import RowFieldChange, StagedRow
from agent_runtime.surfaces_v2.shaping_policy import ShapingModelResolver

_LOGGER = logging.getLogger(__name__)

#: Log prefix for the write-mapping lane, matching ``[surfaces.shaping]``'s form.
_MAPPER_PREFIX = "[surfaces.writeback]"


class _Limits:
    """Bounds on an untrusted answer and on the batch it is asked about.

    Each number is pinned to one that already exists in the row-set contracts
    rather than invented here, so a batch this module accepts is a batch the
    staging engine can already carry.
    """

    # == ``rowset._Limits.MAX_ROWS`` — the staging engine's own ceiling.
    MAX_EDITS = 200
    # == ``rowset._Limits.MAX_CHANGES_PER_ROW``.
    MAX_CHANGES_PER_ROW = 20
    # == ``rowset._Limits.FIELD_MAX`` — an arg name is the same kind of thing.
    NAME_MAX = 200
    # An op name is a tool name; the MCP descriptor caps its own at 200.
    OP_MAX = 200
    # Distinct args one write may carry. Wider than a surface's column cap (12)
    # because a write op also takes identity + scoping args the user never saw.
    MAX_ARGS = 40
    # Candidate ops shown to the model. A connector with more write ops than
    # this is one whose catalogue needs narrowing before a model sees it.
    MAX_CANDIDATE_OPS = 40
    # Safe rejection summaries — logs + typed errors, never raw model output.
    REASON_MAX = 200


class _Messages:
    """Safe public messages. Every one is a CONSTANT — never model output."""

    NO_EDITS = "There are no edits to save."
    TOO_MANY_EDITS = "This save exceeds the maximum number of edited rows."
    TOO_MANY_CHANGES = "A row exceeds the maximum number of edited fields."
    DUPLICATE_ROW_KEY = "Row keys must be unique within one save."
    NO_CHANGES = "An edited row carries no field changes."
    NO_CANDIDATE_OPS = "This connector exposes no write operation to save into."

    UNAVAILABLE = (
        "Saving to this connector needs a configured model provider, and none "
        "is available for this run. Nothing was staged and nothing was sent."
    )
    MODEL_FAILED = "The save could not be prepared. Nothing was staged."
    ANSWER_MALFORMED = "The proposed write mapping is malformed. Nothing was staged."
    UNKNOWN_OP = "The proposed write operation is not one this connector offers."
    UNMAPPED_COLUMN = (
        "The proposed write mapping does not cover every edited field. "
        "Nothing was staged."
    )
    UNKNOWN_ROW_FIELD = (
        "The proposed write mapping reads a field that this row does not have."
    )
    DUPLICATE_ARG = (
        "The proposed write mapping binds one argument twice, so one of your "
        "edits would not be sent. Nothing was staged."
    )
    INVENTED_VALUE = (
        "The proposed write contains a value that you did not enter and that "
        "was not read from the connector. Nothing was staged."
    )
    NO_ARGS = "The proposed write would send no field for an edited row."


# ---------------------------------------------------------------------------
# Typed errors — every one is loud; none of them degrades
# ---------------------------------------------------------------------------


class WriteMappingError(Exception):
    """A save could not be turned into staged rows. Carries a SAFE message only.

    Deliberately an exception rather than an ``| None`` return. The read path's
    shaping seam returns ``None`` on every failure because the floor still
    draws something; a save has no floor — a caller that treats "no mapping" as
    "nothing to do" reports success for a write that never happened.
    """

    safe_message: str = _Messages.MODEL_FAILED

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message[: _Limits.REASON_MAX]


class WriteMappingUnavailable(WriteMappingError):
    """No shaping model resolved or none could be built (BYOK posture, SDR §13 #1).

    The read path treats this as "shaping is off"; the write path cannot. The
    caller maps it to a 422 the user reads as *"configure a provider key"*.
    """

    safe_message: str = _Messages.UNAVAILABLE


class WriteMappingRejected(WriteMappingError):
    """The model answered, and the answer failed a fail-closed check.

    Raised for a malformed answer, an op the connector does not offer, an
    edited column the answer never mapped, a row field the read does not carry,
    and — the one this whole module exists for — a value with no provenance.
    """

    safe_message: str = _Messages.ANSWER_MALFORMED


# ---------------------------------------------------------------------------
# Inputs — what the client batched, and what the connector offers
# ---------------------------------------------------------------------------


class SurfaceRowEdit(RuntimeContract):
    """One row's batched cell edits, plus that row exactly as it was read.

    ``row`` is the provenance half the user did not type: an arg bound to a
    connector-read value (a record id, a scoping key) is admissible precisely
    because it appears here. It is the row as the surface rendered it, so a
    binding that reads a field the surface never showed is rejected rather than
    silently resolving to ``None``.
    """

    row_key: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    title: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    row: JsonObject = Field(default_factory=dict)
    changes: tuple[RowFieldChange, ...] = ()


class WriteOpCandidate(RuntimeContract):
    """One write op the model may choose, as the connector describes it.

    Structurally a subset of ``McpToolDescriptor`` (same attribute names) so a
    real descriptor drops in unchanged. Only ops a caller has already decided
    are *writes* belong here — this module does not classify.
    """

    name: str = Field(min_length=1, max_length=_Limits.OP_MAX)
    description: str = ""
    input_schema: JsonObject = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# The model answer — source references, never content
# ---------------------------------------------------------------------------


class ArgSourceKind(StrEnum):
    """Where one arg's value comes from. The model names the source, not the value."""

    #: The user's NEW value for a column they edited in this row.
    EDITED = "edited"
    #: A value read from the connector for this row (identity, scoping, context).
    ROW = "row"
    #: A value the model typed. Admitted by the schema, then held to the same
    #: provenance rule as every other leaf — see :class:`ArgProvenanceAudit`.
    LITERAL = "literal"


class ArgBinding(RuntimeContract):
    """One write-op arg and where its value comes from.

    ``key`` names the edited column (:attr:`ArgSourceKind.EDITED`) or the
    read-row field (:attr:`ArgSourceKind.ROW`); ``value`` is populated only for
    :attr:`ArgSourceKind.LITERAL`. The validator enforces that pairing so an
    answer cannot smuggle a value alongside a reference and have the reference
    quietly win.
    """

    arg: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    source: ArgSourceKind
    key: str | None = Field(default=None, max_length=_Limits.NAME_MAX)
    value: JsonValue | None = None

    @model_validator(mode="after")
    def _key_matches_source(self) -> "ArgBinding":
        if self.source is ArgSourceKind.LITERAL:
            if self.key is not None:
                raise ValueError("A literal binding may not also name a key.")
            return self
        if not self.key:
            raise ValueError("A referencing binding must name its key.")
        if self.value is not None:
            raise ValueError("A referencing binding may not also carry a value.")
        return self


class WriteMappingAnswer(RuntimeContract):
    """The whole answer: one op for the batch, and the arg bindings to build it.

    One op for the batch rather than one per row, because a batch of cell edits
    on one surface is a batch of edits to one KIND of record. A row that needs a
    different op is a different gesture (the design names row create/delete as
    exactly that open question) and is not expressible here by design.
    """

    op: str = Field(min_length=1, max_length=_Limits.OP_MAX)
    args: tuple[ArgBinding, ...] = Field(min_length=1, max_length=_Limits.MAX_ARGS)

    @model_validator(mode="after")
    def _args_are_distinct(self) -> "WriteMappingAnswer":
        """One argument, one binding — a repeat is a silently dropped edit.

        Composition assigns ``target_args[binding.arg]``, so a second binding on
        the same ``arg`` overwrites the first. Both columns still satisfy the
        coverage rule and both still appear in the row's displayed ``changes``,
        which is the one failure a WYSIWYG diff cannot show: the row stages
        looking correct while one of its cells is never sent. Refused at the
        contract so the shape is unrepresentable rather than checked twice.
        """

        names = [binding.arg for binding in self.args]
        if len(set(names)) != len(names):
            raise ValueError(_Messages.DUPLICATE_ARG)
        return self


class WriteMappingAnswerParser:
    """Parse + bound an untrusted answer. Total: it raises only the typed error."""

    @classmethod
    def parse(cls, raw: object) -> WriteMappingAnswer:
        """Return the validated answer, or raise :class:`WriteMappingRejected`."""

        if not isinstance(raw, Mapping):
            raise WriteMappingRejected(_Messages.ANSWER_MALFORMED)
        try:
            answer = WriteMappingAnswer.model_validate(cls._compact(raw))
        except ValidationError as exc:
            _LOGGER.warning(
                "%s answer_malformed errors=%d", _MAPPER_PREFIX, exc.error_count()
            )
            raise WriteMappingRejected(_Messages.ANSWER_MALFORMED) from exc
        return answer

    @staticmethod
    def _compact(raw: Mapping[str, object]) -> dict[str, object]:
        """Drop null-valued top-level keys before validation.

        Same reason ``ShapingAnswerValidator`` does it: a provider forced to
        emit every declared key answers optional members as ``null``, and under
        ``extra="forbid"`` that read as malformed for an answer that was fine.
        Bindings are NOT compacted — ``value: null`` inside one is data.
        """

        return {key: value for key, value in raw.items() if value is not None}


# ---------------------------------------------------------------------------
# The provenance audit — the enforcement point
# ---------------------------------------------------------------------------


class _ValueFingerprint:
    """Type-tagged canonical form of a JSON value, for exact set membership.

    Plain ``==`` is the wrong test twice over: ``True == 1`` and ``1 == 1.0``
    both hold in Python, so a model could substitute a boolean for a count and
    the audit would call it the user's own value. Tagging the type and
    canonicalising the JSON closes both, and makes nested containers comparable
    without a recursive walk at compare time.
    """

    @classmethod
    def of(cls, value: object) -> str:
        return f"{type(value).__name__}:{cls._canonical(value)}"

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - default= covers these
            return repr(value)


@dataclass(frozen=True)
class ArgProvenanceAudit:
    """Every leaf of a composed ``target_args`` must be a value we can account for.

    The admissible set for a row is the union of:

    * every ``new`` the user typed in this row's changes,
    * every ``old`` those changes carry (the value as read — a write op that
      echoes the prior value back is sending the connector its own datum), and
    * every value reachable inside the row AS READ, at any depth.

    An arg bound to a whole value — scalar, list or object — passes when that
    value itself is admissible; fingerprints compare structurally, so a nested
    object matches without a recursive walk. Failing that, an OBJECT may still
    earn admission from its leaves: the model composes the envelope a connector
    op wants (``{"input": {...}}``), and the envelope is shape, which is what
    the model is here to choose. Arg NAMES are never audited for the same
    reason.

    **A LIST does not get that second chance, and an empty container does not
    either.** Both were vacuous holes. ``all()`` over an empty container is
    ``True``, so a model-authored ``[]`` was admitted against every row — and
    ``labels: []`` is not a null edit, it is how a connector is told to clear a
    field. Leaf-wise admission of a list is worse, because :meth:`_collect`
    walks the row AS READ to any depth: a connector that returned
    ``assignees: ["alice", "bob"]`` put both names in ``allowed`` individually,
    so ``["bob", "alice"]`` and ``["alice"]`` were composable from them and were
    both admitted — a re-ordered and a truncated list the user never entered.
    A list's ORDER and MEMBERSHIP are data, not shape, so a list is admissible
    only as an exact structural match, which is what the module claims for every
    value it stages.

    Total and pure. It returns the offending arg's name for the log and nothing
    of the value itself, which may be user content.
    """

    allowed: frozenset[str]

    @classmethod
    def for_edit(cls, edit: SurfaceRowEdit) -> "ArgProvenanceAudit":
        """Build the admissible fingerprint set for one edited row."""

        allowed: set[str] = set()
        for change in edit.changes:
            allowed.add(_ValueFingerprint.of(change.new))
            allowed.add(_ValueFingerprint.of(change.old))
        cls._collect(edit.row, allowed)
        return cls(allowed=frozenset(allowed))

    def offending_arg(self, target_args: Mapping[str, object]) -> str | None:
        """Return the first arg whose value is not accounted for, else ``None``."""

        for name, value in target_args.items():
            if not self._admissible(value):
                return name
        return None

    def _admissible(self, value: object) -> bool:
        if _ValueFingerprint.of(value) in self.allowed:
            return True
        if isinstance(value, Mapping):
            # ``bool(value)`` first: an empty object has no leaves to vouch for
            # it, so ``all()`` would admit it vacuously.
            return bool(value) and all(
                self._admissible(item) for item in value.values()
            )
        # A list is data, in the order and with the membership it was read or
        # typed in. It has no leaf-wise second chance — see the class docstring.
        return False

    @classmethod
    def _collect(cls, value: object, into: set[str]) -> None:
        """Record ``value`` and, recursively, everything reachable inside it."""

        into.add(_ValueFingerprint.of(value))
        if isinstance(value, Mapping):
            for item in value.values():
                cls._collect(item, into)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                cls._collect(item, into)


# ---------------------------------------------------------------------------
# Composition — answer + edits ⇒ StagedRows
# ---------------------------------------------------------------------------


class RowWriteComposer:
    """Turn one validated answer plus the batch into ``StagedRow``s, fail-closed.

    Pure and synchronous — the model call is the caller's job, so every rule
    below is testable without a completion. Four rules, each of which rejects
    the WHOLE batch rather than dropping a row, because a partially mapped save
    is a save whose diff no longer describes what the user asked for:

    1. **Every edited column is mapped.** A column the user changed somewhere in
       the batch and the answer never bound is a silently dropped edit — the one
       failure a WYSIWYG diff cannot show, since the row would stage looking
       correct while one of its cells was never sent.
    2. **Every ``row`` reference resolves.** A binding naming a field the read
       does not carry would compose to ``None`` and send a null the user never
       chose.
    3. **Every row sends at least one edited value.** An args set that is all
       identity is a write with nothing to write.
    4. **Every leaf survives the audit.** :class:`ArgProvenanceAudit`, above.

    A binding for a column *this* row did not edit is skipped rather than
    rejected: one answer covers a batch in which different rows changed
    different cells, and an unedited cell has no new value to send.
    """

    @classmethod
    def compose(
        cls,
        *,
        answer: WriteMappingAnswer,
        edits: Sequence[SurfaceRowEdit],
    ) -> tuple[StagedRow, ...]:
        """Return the staged rows, or raise :class:`WriteMappingRejected`."""

        cls._require_full_coverage(answer=answer, edits=edits)
        return tuple(cls._row(answer=answer, edit=edit) for edit in edits)

    @classmethod
    def _require_full_coverage(
        cls,
        *,
        answer: WriteMappingAnswer,
        edits: Sequence[SurfaceRowEdit],
    ) -> None:
        bound = {
            binding.key
            for binding in answer.args
            if binding.source is ArgSourceKind.EDITED and binding.key is not None
        }
        edited = {change.field for edit in edits for change in edit.changes}
        missing = edited - bound
        if missing:
            _LOGGER.warning(
                "%s answer_incomplete unmapped_columns=%d", _MAPPER_PREFIX, len(missing)
            )
            raise WriteMappingRejected(_Messages.UNMAPPED_COLUMN)

    @classmethod
    def _row(cls, *, answer: WriteMappingAnswer, edit: SurfaceRowEdit) -> StagedRow:
        by_column = {change.field: change for change in edit.changes}
        target_args: dict[str, JsonValue] = {}
        carried_edit = False

        for binding in answer.args:
            if binding.source is ArgSourceKind.EDITED:
                change = by_column.get(binding.key or "")
                if change is None:
                    # This row did not edit that column — nothing to send for it.
                    continue
                target_args[binding.arg] = change.new
                carried_edit = True
            elif binding.source is ArgSourceKind.ROW:
                key = binding.key or ""
                if key not in edit.row:
                    raise WriteMappingRejected(_Messages.UNKNOWN_ROW_FIELD)
                target_args[binding.arg] = edit.row[key]
            else:
                target_args[binding.arg] = binding.value

        if not carried_edit:
            raise WriteMappingRejected(_Messages.NO_ARGS)

        offending = ArgProvenanceAudit.for_edit(edit).offending_arg(target_args)
        if offending is not None:
            # The arg NAME is safe to log (the model chose it); the value is
            # user or connector content and never appears in a log line.
            _LOGGER.warning(
                "%s invented_value arg=%s row_key=%s: nothing staged",
                _MAPPER_PREFIX,
                offending,
                edit.row_key,
            )
            raise WriteMappingRejected(_Messages.INVENTED_VALUE)

        return StagedRow(
            row_key=edit.row_key,
            title=edit.title,
            target_args=target_args,
            changes=edit.changes,
        )


class EditBatchValidator:
    """Bound the batch fail-closed BEFORE a model is asked anything.

    Runs first so an over-cap or malformed save costs nothing: no model call, no
    ledger event, and a typed error the route maps to a 422. Mirrors
    ``RowsetValidator``'s posture, and stops short of duplicating it — the
    staging engine still validates the rows it is finally handed.
    """

    @classmethod
    def validate(cls, edits: Sequence[SurfaceRowEdit]) -> None:
        if not edits:
            raise WriteMappingRejected(_Messages.NO_EDITS)
        if len(edits) > _Limits.MAX_EDITS:
            raise WriteMappingRejected(_Messages.TOO_MANY_EDITS)
        seen: set[str] = set()
        for edit in edits:
            if edit.row_key in seen:
                raise WriteMappingRejected(_Messages.DUPLICATE_ROW_KEY)
            seen.add(edit.row_key)
            if not edit.changes:
                raise WriteMappingRejected(_Messages.NO_CHANGES)
            if len(edit.changes) > _Limits.MAX_CHANGES_PER_ROW:
                raise WriteMappingRejected(_Messages.TOO_MANY_CHANGES)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


class WriteMappingPrompt:
    """The system + user prompts, and the JSON schema the answer is forced into.

    **The user prompt carries no cell values.** The model is choosing an op and
    a set of arg names; the values are the user's and the connector's, and are
    composed in afterwards. Sending them would buy nothing and would hand the
    model the exact strings it is most likely to paraphrase — the failure this
    module is built to refuse. It sees column NAMES, row field NAMES, and the
    candidate ops' schemas.
    """

    SYSTEM: ClassVar[str] = (
        "You map a user's edits on a table to one connector write operation.\n"
        "\n"
        "Choose exactly ONE operation from the candidates, then bind its "
        "arguments. For each argument say WHERE its value comes from:\n"
        '  - {"arg": "<name>", "source": "edited", "key": "<edited column>"}\n'
        '  - {"arg": "<name>", "source": "row", "key": "<row field as read>"}\n'
        "\n"
        "Rules:\n"
        "1. Bind EVERY edited column to an argument. An unmapped column is a "
        "lost edit and the whole answer is refused.\n"
        "2. Bind the arguments that identify the record (its id, and any "
        'required scope) with source "row".\n'
        "3. NEVER write a value. You are given names, not data, because the "
        "values belong to the user and to the connector. An answer containing "
        "a value that neither of them supplied is refused.\n"
        "4. Answer with JSON only."
    )

    #: Forced-structured-output schema. Mirrors :class:`WriteMappingAnswer`.
    SCHEMA: ClassVar[Mapping[str, object]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "args"],
        "properties": {
            "op": {"type": "string"},
            "args": {
                "type": "array",
                "minItems": 1,
                "maxItems": _Limits.MAX_ARGS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["arg", "source"],
                    "properties": {
                        "arg": {"type": "string"},
                        "source": {"enum": ["edited", "row", "literal"]},
                        "key": {"type": ["string", "null"]},
                        "value": {},
                    },
                },
            },
        },
    }

    @classmethod
    def user(
        cls,
        *,
        connector: str,
        read_op: str,
        candidates: Sequence[WriteOpCandidate],
        edited_columns: Sequence[str],
        row_fields: Sequence[str],
    ) -> str:
        """Render the user prompt. Names only — never a cell value."""

        payload = {
            "connector": connector,
            "read_operation": read_op,
            "edited_columns": list(edited_columns),
            "row_fields_available": list(row_fields),
            "candidate_write_operations": [
                {
                    "name": candidate.name,
                    "description": candidate.description,
                    "input_schema": dict(candidate.input_schema),
                }
                for candidate in candidates
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# The mapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteMapping:
    """The staged-write proposal a save resolved to: one op and its rows."""

    op: str
    rows: tuple[StagedRow, ...]


@dataclass(frozen=True)
class SurfaceWriteMapper:
    """One model call per save; a validated set of ``StagedRow``s or a raised error.

    The completion is injected (the same seam the spec generator uses), so every
    rule in this module is exercised without a live model. Construction is
    :func:`build_surface_write_mapper`, which is the only place a provider key
    is composed in.
    """

    completion: SpecCompletionPort
    model_id: str = ""

    async def map_edits(
        self,
        *,
        connector: str,
        read_op: str,
        candidates: Sequence[WriteOpCandidate],
        edits: Sequence[SurfaceRowEdit],
    ) -> WriteMapping:
        """Map a batch of cell edits onto one connector write op + per-row args."""

        EditBatchValidator.validate(edits)
        candidate_list = tuple(candidates)[: _Limits.MAX_CANDIDATE_OPS]
        if not candidate_list:
            raise WriteMappingRejected(_Messages.NO_CANDIDATE_OPS)

        raw = await self._complete(
            connector=connector,
            read_op=read_op,
            candidates=candidate_list,
            edits=edits,
        )
        answer = WriteMappingAnswerParser.parse(raw)
        if answer.op not in {candidate.name for candidate in candidate_list}:
            # An op the connector never offered is not a mapping — it is the
            # model naming a capability. Refuse before anything is composed.
            _LOGGER.warning("%s answer_unknown_op", _MAPPER_PREFIX)
            raise WriteMappingRejected(_Messages.UNKNOWN_OP)
        rows = RowWriteComposer.compose(answer=answer, edits=edits)
        return WriteMapping(op=answer.op, rows=rows)

    async def _complete(
        self,
        *,
        connector: str,
        read_op: str,
        candidates: Sequence[WriteOpCandidate],
        edits: Sequence[SurfaceRowEdit],
    ) -> object:
        """One completion. A provider failure is a raised, typed, SAFE error.

        Deliberately not retried here: a retry belongs to the caller that knows
        whether the user is still waiting, and a silent second call on a write
        path is the sort of thing that turns one save into two.
        """

        user = WriteMappingPrompt.user(
            connector=connector,
            read_op=read_op,
            candidates=candidates,
            edited_columns=self._edited_columns(edits),
            row_fields=self._row_fields(edits),
        )
        try:
            result = await self.completion.complete(
                system=WriteMappingPrompt.SYSTEM, user=user
            )
        except Exception as exc:  # noqa: BLE001 - never leak provider detail
            _LOGGER.warning(
                "%s completion_failed model=%s error=%s",
                _MAPPER_PREFIX,
                self.model_id or "unset",
                type(exc).__name__,
            )
            raise WriteMappingError(_Messages.MODEL_FAILED) from exc
        return result.candidate

    @staticmethod
    def _edited_columns(edits: Sequence[SurfaceRowEdit]) -> tuple[str, ...]:
        """Every column edited anywhere in the batch, first-seen order.

        Delegates rather than repeating :func:`edited_columns_of`: the same list
        names the columns the MODEL is shown and the columns the staged surface
        is TITLED after, and two copies of that rule is how the title stops
        describing what was sent.
        """

        return edited_columns_of(edits)

    @staticmethod
    def _row_fields(edits: Sequence[SurfaceRowEdit]) -> tuple[str, ...]:
        """Every field name the read rows carry, first-seen order (names only)."""

        names: dict[str, None] = {}
        for edit in edits:
            for key in edit.row:
                names.setdefault(key, None)
        return tuple(names)


def build_surface_write_mapper(
    *,
    environ: Mapping[str, str],
    run_provider: str | None = None,
    credentials: ShapingCredentials | None = None,
    completion: SpecCompletionPort | None = None,
) -> SurfaceWriteMapper:
    """Build the mapper, or RAISE — a save never degrades to "no model, no write".

    Same two-step resolution the read path uses and no third one:
    :class:`ShapingModelResolver` decides the id (so the BYOK/default-provider
    ladder is stated once), then :meth:`ShapingModelBuild.attempt` composes the
    run's credential into the construction. ``credentials`` is not optional in
    practice — a packaged desktop install holds no provider key in its process
    env by design, so a caller that omits it builds a model with no credential.

    Where ``build_read_path_shaper`` returns ``None`` on each of these failures,
    this raises :class:`WriteMappingUnavailable`. That inversion is the whole
    point: an unshaped surface is a plainer table, whereas an unstaged save that
    reported success is a user who believes a connector was updated.
    """

    if completion is not None:
        return SurfaceWriteMapper(completion=completion)

    model_id = ShapingModelResolver.resolve(environ=environ, run_provider=run_provider)
    if not model_id:
        _LOGGER.warning(
            "%s mapping_model_unresolved: save refused (no provider configured)",
            _MAPPER_PREFIX,
        )
        raise WriteMappingUnavailable()

    build = ShapingModelBuild.attempt(model_id=model_id, credentials=credentials)
    if not build.ok:
        # ``describe()`` carries the failure CLASS and the exception type name
        # only — never the composed kwargs, which hold key material.
        _LOGGER.warning(
            "%s mapping_model_unavailable model=%s %s: save refused",
            _MAPPER_PREFIX,
            model_id,
            build.describe(),
        )
        raise WriteMappingUnavailable()

    from agent_runtime.capabilities.surfaces.generator import (  # noqa: PLC0415
        LangChainSpecCompletion,
    )

    return SurfaceWriteMapper(
        completion=LangChainSpecCompletion(
            model=build.model, model_id=model_id, schema=WriteMappingPrompt.SCHEMA
        ),
        model_id=model_id,
    )


def edited_columns_of(edits: Iterable[SurfaceRowEdit]) -> tuple[str, ...]:
    """Every column edited across a batch — the client's own summary, reusable."""

    return tuple(
        dict.fromkeys(change.field for edit in edits for change in edit.changes)
    )


__all__ = [
    "ArgBinding",
    "ArgProvenanceAudit",
    "ArgSourceKind",
    "EditBatchValidator",
    "RowWriteComposer",
    "SurfaceRowEdit",
    "SurfaceWriteMapper",
    "WriteMapping",
    "WriteMappingAnswer",
    "WriteMappingAnswerParser",
    "WriteMappingError",
    "WriteMappingPrompt",
    "WriteMappingRejected",
    "WriteMappingUnavailable",
    "WriteOpCandidate",
    "build_surface_write_mapper",
    "edited_columns_of",
]
