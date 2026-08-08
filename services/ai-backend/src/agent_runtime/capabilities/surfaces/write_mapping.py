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

**Provenance is necessary and is NOT sufficient.** :class:`ArgProvenanceAudit`
answers *"is this value real?"* — it admits a leaf only if the user typed it or
the connector read it, so a paraphrase, a coerced type, a trimmed string and a
re-ordered list are all refused. But every field the connector returned in a row
is real, so provenance alone let a one-line diff compose a five-field write:

.. code-block:: text

    APPROVED           priority: 'high' -> 'low'
    SENT               id='PAR-9'  priority='low'
                       assignee='alice'   <- real, never in the diff
                       state='open'       <- real, never in the diff
                       notes='high'       <- real, the value edited AWAY from

Nothing there is fabricated and every one passed the audit legitimately, yet
*"the object the user approves is the object that is sent"* was false. Two harms
follow directly: a full-record overwrite silently clobbers a concurrent change
to ``assignee``; and ``notes='high'`` shows a value being **relocated into a
different field**. So :class:`WriteArgScope` asks the second question —
*"does this value belong in this write?"* — and confines ``target_args`` to two
disjoint, separately-bounded lanes:

* **payload** — one arg per column THIS row edited, carrying ``change.new``;
* **scope** — args the CONNECTOR declares it requires to address a record,
  carrying the value as read.

Nothing else composes. See :class:`WriteArgScope` for how "required" is read and
why a schema that marks everything required is treated as having declared
nothing.

**Two enforcement layers, not one.** The answer contract (:class:`ArgBinding`)
is source-referencing, so the ordinary path gives the model no slot to type a
value into. It may still emit :attr:`ArgSourceKind.LITERAL`, and that member
exists on purpose: models paraphrase, and a paraphrase rejected as *"this value
came from nowhere"* is diagnosable, where the same paraphrase rejected as a
schema error reads as a broken prompt. Every composed ``target_args`` is audited
first, so an invented literal is named as invented; a literal whose value IS
admissible is then refused by the scope rule instead, because a literal fills
neither lane — a value the model typed is not a cell the user edited, and it is
not a key the connector declared. Both halves are refused and NOTHING is staged.

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
from typing import ClassVar, NoReturn

from pydantic import Field, ValidationError, model_validator

from agent_runtime.capabilities.surfaces.generator import (
    ShapingCredentials,
    ShapingModelBuild,
    SpecCompletionPort,
)
from agent_runtime.capabilities.surfaces.write_ops_capture import WriteOpCandidate
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
    # ``rowset._Limits.MAX_CHANGES_PER_ROW`` (20) MINUS the scope args a row may
    # additionally disclose. A composed row's ``changes`` is the user's own diff
    # plus one entry per scoping arg (see ``RowWriteComposer._disclosed``), and
    # that sum has to stay inside the staging engine's per-row ceiling — a save
    # that passed here and was refused two layers down would be a refusal with
    # no diagnosis attached to the thing the user actually did.
    MAX_CHANGES_PER_ROW = 16
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
    # Scoping args ONE write may carry beyond the user's own edits. A record is
    # addressed by an id, sometimes a parent/tenant key, sometimes a version —
    # ``update_cell(spreadsheet_id, sheet, row, column)`` is the widest honest
    # shape we know of. An op that needs more than this to ADDRESS a record is
    # one whose descriptor is not discriminating enough to bound a write, and
    # the save refuses rather than composing a record-shaped overwrite.
    MAX_SCOPE_ARGS = 4
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
    UNEDITED_COLUMN = (
        "The proposed write mapping binds a column you did not edit. "
        "Nothing was staged."
    )
    UNKNOWN_ROW_FIELD = (
        "The proposed write mapping reads a field that this row does not have."
    )
    OUT_OF_SCOPE_ARG = (
        "The proposed write would send a field you did not edit. A save sends "
        "your edits and the fields this operation needs to find the record, "
        "and nothing else. Nothing was staged."
    )
    UNBOUNDED_OP = (
        "This connector does not say which arguments identify a record, so "
        "this save cannot be limited to the fields you edited. Nothing was "
        "staged."
    )
    UNDECLARED_ARG = (
        "The proposed write would send a field this operation does not accept, "
        "so what you approved and what would be sent are not the same write. "
        "Nothing was staged."
    )
    RELOCATED_ROW_FIELD = (
        "The proposed write would fill one of this record's identifying fields "
        "from a different field of the record. Nothing was staged."
    )
    EDIT_INTO_RECORD_KEY = (
        "The proposed write would put one of your edits into a field this "
        "operation uses to find the record. Nothing was staged."
    )
    DUPLICATE_FIELD = (
        "A row lists the same field twice, so only one of the two values would "
        "be sent. Nothing was staged."
    )
    MODEL_TYPED_VALUE = (
        "The proposed write types a value in directly instead of taking it "
        "from your edit or from the record. Nothing was staged."
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

    ``row`` is the provenance half the user did not type: it is where a record
    id or a scoping key gets its VALUE. Appearing here is necessary and is not
    sufficient — :class:`WriteArgScope` decides which of these fields a write
    may carry at all, and it is the connector's schema, not this payload, that
    answers that. It is the row as the surface rendered it, so a binding that
    reads a field the surface never showed is rejected rather than silently
    resolving to ``None``.
    """

    row_key: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    title: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    row: JsonObject = Field(default_factory=dict)
    changes: tuple[RowFieldChange, ...] = ()


# ``WriteOpCandidate`` is defined in :mod:`.write_ops_capture` and re-exported
# here. The capture side has to speak this contract to write a descriptor onto
# the ledger, and it must not drag the shaping model ladder in to do it — but a
# second, structurally identical model would be exactly the twin that drifts.
# One definition, imported by whichever side needs it.


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
    earn admission from its leaves. Arg NAMES are never audited here at all:
    naming is :class:`WriteArgScope`'s question, not this one's.

    **What this catches now that it did not before, and what it no longer has
    to.** Every value a payload or scope arg carries is one composition took
    from ``change.new`` or from ``edit.row[key]``, so it is admissible by
    construction and this audit cannot fire on it. The one binding whose value
    the MODEL supplies is a literal, and a literal is refused by
    :class:`WriteArgScope` regardless — so the audit is what turns *"a field
    that is out of scope"* into the sharper *"a value that came from nowhere"*
    for the case that deserves it, and it remains the standing guard should a
    value-carrying source ever be re-admitted. Its object rule is what makes an
    invented leaf inside an envelope visible rather than averaged away.

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
# The scope confinement — what the write is ALLOWED to carry
# ---------------------------------------------------------------------------


class _SchemaKeys:
    """The two JSON-Schema keywords a candidate op's declaration is read by."""

    PROPERTIES = "properties"
    REQUIRED = "required"


@dataclass(frozen=True)
class WriteArgScope:
    """The args ONE write may carry: the user's edits, plus DECLARED scoping keys.

    :class:`ArgProvenanceAudit` proves a value is real. This proves it belongs.
    Without it a one-line diff composed a five-field write out of values that
    were every one of them admissible — see the module docstring — so the rule
    here is about the SET of args, not about any value in it:

    * **payload** — an arg an ``EDITED`` binding fills. The model names it
      freely (mapping a column onto an arg name is exactly the naming job it is
      here for) and cannot choose its value: composition takes ``change.new``.
    * **scope** — an arg a ``ROW`` binding fills. The model does NOT name these
      freely; the name must appear in :attr:`scope_args`.
    * **nothing else.** A ``LITERAL`` fills neither lane, so it never composes.

    **Where the scope allow-list comes from, and why that source.** From the
    chosen op's own ``input_schema.required`` — the CONNECTOR's statement of
    *"you cannot call me without this"*. That is the property "a record id, a
    tenant key" actually has, and, decisively, it is authored by the connector
    rather than by the model: an ``ArgSourceKind.SCOPE`` the model declares for
    itself, or a bare cap, would both let it relabel ``assignee`` as scoping and
    buy nothing. A required arg that an ``EDITED`` binding already fills is
    subtracted, so the user's own edit wins the slot rather than being shadowed
    by the value as read.

    **A declaration only counts when it discriminates, and "discriminates" is
    a SIZE test, not only a subset one.** ``required`` is trusted only if the op
    also declares ``properties``, requires a strict subset of them, AND requires
    no more than :attr:`_Limits.MAX_SCOPE_ARGS` of them. A schema that marks
    every property required — the shape an MCP server emits to satisfy strict
    function-calling — has said nothing about which args address a record, and
    reading it as an allow-list would re-open the exact hole: every unedited
    column becomes "scoping". Such an op is refused
    (:attr:`_Messages.UNBOUNDED_OP`), as is one that declares no schema at all.

    The size test is not redundant with the strict-subset one, and leaving it to
    the post-subtraction cap was a hole. Adding ONE optional property to an
    otherwise all-required schema satisfies "strict subset" — and the cap was
    applied to ``required - payload``, so a five-required op with one edited
    column left four scope args and passed. That reproduced the module
    docstring's own five-field exploit verbatim. An op needing more than four
    args to ADDRESS a record is already the case this module says it refuses, so
    the cap belongs on ``required`` itself, before anything is subtracted.

    **Every arg the write carries must be one the op DECLARES.** ``properties``
    bounds the whole answer, both lanes. Nothing checked the payload lane's arg
    names at all: coverage proved a binding's ``key`` named a column the user
    edited, and scope only ever looked at ``ROW`` bindings — so an ``EDITED``
    binding could name any arg it liked. The edit displayed as ``body`` was
    dispatched as ``bcc``, and ``totally_made_up_arg`` staged cleanly. Both
    directions of that lie (a field shown but not sent, a field sent but not
    shown) are closed by requiring :attr:`declared_args` membership for every
    binding, and an op that declares no ``properties`` is refused outright.

    **The stated bound on nesting.** The model chooses the op and one flat arg
    name per value; it does not choose a value, and it no longer chooses a
    *path* for one either. The old escape hatch was a model-authored object
    (``{"input": {...}}``) admitted leaf-wise — shape and content in one
    untyped blob, which is how ``notes='high'`` relocated a value into a field
    the user never touched. An op whose args genuinely nest cannot be written
    through this lane today and refuses loudly; restoring it means a declared
    ``ArgBinding.path`` that composition fills from a source, never a literal.

    Total and pure. Rejections name the ARG (the model chose it) and never a
    value, which may be user content.
    """

    #: Args an ``EDITED`` binding fills — one per column the batch edited.
    payload_args: frozenset[str]
    #: Args a ``ROW`` binding may fill. Empty when the op's declaration is
    #: unusable, which is a REFUSAL and never a licence to send anything.
    scope_args: frozenset[str]
    #: Every arg the op DECLARES (``input_schema.properties``). Bounds both
    #: lanes: an arg outside this set is one the connector never offered, so no
    #: binding of any source may name it. Empty when the op declares no
    #: properties, which refuses the whole answer.
    declared_args: frozenset[str]
    #: Whether the op's own declaration bounded this write at all — false for a
    #: missing schema, an everything-is-required one, one that requires more
    #: than :attr:`_Limits.MAX_SCOPE_ARGS` args, and one whose surviving scope
    #: exceeds the same cap. Chooses the message only; both answers refuse.
    bounded: bool

    @classmethod
    def for_op(
        cls, *, answer: "WriteMappingAnswer", candidate: WriteOpCandidate
    ) -> "WriteArgScope":
        """Derive the two lanes for one answer against the op it chose."""

        payload = frozenset(
            binding.arg
            for binding in answer.args
            if binding.source is ArgSourceKind.EDITED
        )
        declared = cls._declared_properties(candidate)
        required = cls._declared_required(candidate)
        scope = frozenset(required - payload)
        return cls(
            payload_args=payload,
            scope_args=scope if len(scope) <= _Limits.MAX_SCOPE_ARGS else frozenset(),
            declared_args=declared,
            bounded=bool(required) and len(scope) <= _Limits.MAX_SCOPE_ARGS,
        )

    def reject_out_of_scope(self, bindings: Sequence[ArgBinding]) -> None:
        """Raise on the first binding that fills neither lane, else return.

        Every binding is checked against :attr:`declared_args` first, whatever
        its source. The ``EDITED`` lane used to be skipped outright — coverage
        had proved the binding's ``key`` was a column the user edited, and
        nobody asked where its ``arg`` pointed — which is how a ``body`` edit
        was dispatched as ``bcc``.
        """

        for binding in bindings:
            # Ordered most-specific-first, the rule the audit already follows:
            # a literal is refused as "you did not type this value" even when it
            # is also out of scope and also undeclared, because that is the
            # diagnosis a reader can act on.
            if binding.source is ArgSourceKind.LITERAL:
                self._refuse(binding, _Messages.MODEL_TYPED_VALUE, "literal_value")
            if binding.arg not in self.declared_args:
                self._refuse(
                    binding,
                    (
                        _Messages.UNDECLARED_ARG
                        if self.declared_args
                        else _Messages.UNBOUNDED_OP
                    ),
                    "undeclared_arg",
                )
            if binding.source is ArgSourceKind.EDITED:
                continue
            if binding.key != binding.arg:
                # A scoping key is the record's own field, under the
                # connector's own name for it. Letting the model choose WHICH
                # row field fills a declared scope arg left the value source
                # unbounded even once the arg NAME was bounded: ``issue_id``
                # was filled from ``row['parent_id']`` (the write lands on a
                # different record than the row the diff is titled after) and
                # from ``row['_internal_token']`` (a field the surface never
                # rendered as a column). Identity-mapping closes both without
                # needing to know which row fields were columns.
                self._refuse(binding, _Messages.RELOCATED_ROW_FIELD, "relocated_row")
            if binding.arg not in self.scope_args:
                self._refuse(
                    binding,
                    (
                        _Messages.OUT_OF_SCOPE_ARG
                        if self.bounded
                        else _Messages.UNBOUNDED_OP
                    ),
                    "out_of_scope_arg",
                )

    @classmethod
    def _declared_required(cls, candidate: WriteOpCandidate) -> frozenset[str]:
        """The op's required args, or EMPTY when its declaration says nothing.

        Three conditions, all of them the connector's own statement: it declares
        ``properties``; it requires a STRICT SUBSET of them; and it requires no
        more than :attr:`_Limits.MAX_SCOPE_ARGS` of them. The last is what an
        inflated schema defeats otherwise — see the class docstring.
        """

        schema = candidate.input_schema
        required = cls._names(schema.get(_SchemaKeys.REQUIRED))
        properties = cls._declared_properties(candidate)
        if not required or not properties or not required < properties:
            return frozenset()
        if len(required) > _Limits.MAX_SCOPE_ARGS:
            return frozenset()
        return required

    @classmethod
    def _declared_properties(cls, candidate: WriteOpCandidate) -> frozenset[str]:
        """Every arg name the op declares, or EMPTY when it declares none."""

        declared = candidate.input_schema.get(_SchemaKeys.PROPERTIES)
        return cls._names(tuple(declared) if isinstance(declared, Mapping) else None)

    @staticmethod
    def _names(value: object) -> frozenset[str]:
        """Coerce an untrusted schema member into a set of plain arg names."""

        if not isinstance(value, (list, tuple)):
            return frozenset()
        return frozenset(item for item in value if isinstance(item, str) and item)

    @staticmethod
    def _refuse(binding: ArgBinding, message: str, reason: str) -> NoReturn:
        _LOGGER.warning(
            "%s %s arg=%s source=%s: nothing staged",
            _MAPPER_PREFIX,
            reason,
            binding.arg,
            binding.source.value,
        )
        raise WriteMappingRejected(message)


# ---------------------------------------------------------------------------
# Composition — answer + edits ⇒ StagedRows
# ---------------------------------------------------------------------------


class RowWriteComposer:
    """Turn one validated answer plus the batch into ``StagedRow``s, fail-closed.

    Pure and synchronous — the model call is the caller's job, so every rule
    below is testable without a completion. Five rules, each of which rejects
    the WHOLE batch rather than dropping a row, because a partially mapped save
    is a save whose diff no longer describes what the user asked for:

    1. **The edited columns and the bound columns are the SAME set.** A column
       the user changed and the answer never bound is a silently dropped edit —
       the one failure a WYSIWYG diff cannot show, since the row would stage
       looking correct while one of its cells was never sent. The mirror
       direction is the one that used to pass: an ``EDITED`` binding naming a
       column nobody in the batch edited resolved to no change and was skipped
       row by row, so a fabricated column name simply vanished instead of
       failing. Coverage is now equality in both directions.
    2. **Every ``row`` reference resolves.** A binding naming a field the read
       does not carry would compose to ``None`` and send a null the user never
       chose.
    3. **Every row sends at least one edited value.** An args set that is all
       identity is a write with nothing to write.
    4. **Every leaf survives the audit.** :class:`ArgProvenanceAudit` — is this
       value real?
    5. **Every arg is in scope.** :class:`WriteArgScope` — does it belong in
       THIS write? Deliberately after the audit: both refuse an invented
       literal, and *"you did not enter this value"* is the more specific
       diagnosis than *"this field is out of scope"* when both apply.

    A binding for a column *this* row did not edit is still skipped rather than
    rejected: rule 1 has already proved the column is one the BATCH edited, and
    one answer covers a batch in which different rows changed different cells.
    """

    @classmethod
    def compose(
        cls,
        *,
        answer: WriteMappingAnswer,
        candidate: WriteOpCandidate,
        edits: Sequence[SurfaceRowEdit],
    ) -> tuple[StagedRow, ...]:
        """Return the staged rows, or raise :class:`WriteMappingRejected`.

        ``candidate`` is the op the answer chose, as the CONNECTOR describes it.
        It is the only non-model source of "which args address a record", so
        composition cannot be asked to bound a write without it.
        """

        cls._require_exact_coverage(answer=answer, edits=edits)
        scope = WriteArgScope.for_op(answer=answer, candidate=candidate)
        return tuple(cls._row(answer=answer, edit=edit, scope=scope) for edit in edits)

    @classmethod
    def _require_exact_coverage(
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
        invented = bound - edited
        if invented:
            _LOGGER.warning(
                "%s answer_overreaches unedited_columns=%d",
                _MAPPER_PREFIX,
                len(invented),
            )
            raise WriteMappingRejected(_Messages.UNEDITED_COLUMN)

    @classmethod
    def _row(
        cls, *, answer: WriteMappingAnswer, edit: SurfaceRowEdit, scope: WriteArgScope
    ) -> StagedRow:
        by_column = {change.field: change for change in edit.changes}
        target_args: dict[str, JsonValue] = {}
        carried_edit = False

        for binding in answer.args:
            if binding.source is ArgSourceKind.EDITED:
                change = by_column.get(binding.key or "")
                if change is None:
                    # This row did not edit that column — nothing to send for it.
                    continue
                cls._reject_edit_into_record_key(binding=binding, edit=edit)
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

        scope.reject_out_of_scope(answer.args)

        return StagedRow(
            row_key=edit.row_key,
            title=edit.title,
            target_args=target_args,
            changes=cls._disclosed(edit=edit, target_args=target_args, scope=scope),
        )

    @staticmethod
    def _reject_edit_into_record_key(
        *, binding: ArgBinding, edit: SurfaceRowEdit
    ) -> None:
        """Refuse an edit routed into a field the RECORD already has.

        ``WriteArgScope`` bounds which args may be named; this bounds what an
        edit may be named ONTO. Renaming a column to the connector's own arg
        name is the mapper's job, so ``arg != key`` is ordinary — but when the
        target arg is also a field of the row as read, the model is not renaming
        a column, it is overwriting a *different* field of the record with the
        value from this one. That is how ``issue_id`` came to be sent as the
        user's new ``priority``, and how a subject edit could be dispatched as
        the message ``body``. The row's own field names are the only signal
        available here that says "this arg addresses something that already
        exists", and it costs nothing: it is the payload the provenance audit
        already reads.
        """

        if binding.arg == binding.key or binding.arg not in edit.row:
            return
        _LOGGER.warning(
            "%s edit_into_record_key arg=%s row_key=%s: nothing staged",
            _MAPPER_PREFIX,
            binding.arg,
            edit.row_key,
        )
        raise WriteMappingRejected(_Messages.EDIT_INTO_RECORD_KEY)

    @staticmethod
    def _disclosed(
        *,
        edit: SurfaceRowEdit,
        target_args: Mapping[str, JsonValue],
        scope: WriteArgScope,
    ) -> tuple[RowFieldChange, ...]:
        """The user's diff, plus one entry per value they would NOT otherwise see.

        ``target_args`` is *"the EXACT connector-op args the shared dispatcher
        sends for THIS row, verbatim"* and is server-only: ``StageRowView``
        deliberately never carries it, and the client ledger projection reads
        ``changes`` alone. So the cell diff is the ONLY thing a human sees
        before a write leaves the machine — and the scope lane put values in
        ``target_args`` that appeared in no change at all. On a mail op whose
        ``required`` set is ``[to, subject, body]``, editing one field
        dispatched the recipient and the whole message body with a one-line
        diff that named neither.

        The fix is disclosure, not removal: those args are genuinely required to
        address the record, so refusing them would make the op unwritable.
        Each one is appended as an ``old == new`` entry — *this is also being
        sent, unchanged* — so the object the user approves is the object that is
        sent. Payload args are already disclosed by the user's own change for
        that column, and are not repeated.

        Bounded by construction: the scope lane carries at most
        :attr:`_Limits.MAX_SCOPE_ARGS` args and
        :class:`EditBatchValidator` caps a row's edits low enough that the sum
        stays inside the staging engine's own per-row ceiling.
        """

        named = {change.field for change in edit.changes}
        carried = tuple(
            RowFieldChange(field=arg, old=value, new=value)
            for arg, value in target_args.items()
            if arg in scope.scope_args and arg not in named
        )
        return edit.changes + carried


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
            fields = {change.field for change in edit.changes}
            if len(fields) != len(edit.changes):
                # Composition assigns one arg per column, so a second change on
                # the same field silently wins: the diff renders both values and
                # exactly one is sent. Same failure ``_args_are_distinct``
                # refuses on the answer side, arriving from the client instead.
                raise WriteMappingRejected(_Messages.DUPLICATE_FIELD)


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
        "1. Bind EVERY edited column to an argument, and bind NO other column. "
        "An unmapped column is a lost edit; a column nobody edited is a field "
        "the user never approved. Either refuses the whole answer.\n"
        '2. Use source "row" ONLY for arguments the chosen operation lists in '
        'its schema\'s "required" — the id, and any key it needs to find the '
        "record. Every other argument the operation offers is left out: a "
        "field the user did not edit must not be sent back, because sending it "
        "would overwrite whatever it holds now.\n"
        "3. NEVER write a value. You are given names, not data, because the "
        "values belong to the user and to the connector. A literal is refused "
        "even when its value happens to be correct.\n"
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
        chosen = next((item for item in candidate_list if item.name == answer.op), None)
        if chosen is None:
            # An op the connector never offered is not a mapping — it is the
            # model naming a capability. Refuse before anything is composed.
            _LOGGER.warning("%s answer_unknown_op", _MAPPER_PREFIX)
            raise WriteMappingRejected(_Messages.UNKNOWN_OP)
        rows = RowWriteComposer.compose(answer=answer, candidate=chosen, edits=edits)
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
    "WriteArgScope",
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
