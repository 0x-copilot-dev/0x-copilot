"""Write-mapping tests — the model picks the tool and the arg NAMES, never a value.

This is the one module in the tree that can compose a request to a user's real
Linear, so its safety property is asserted by attack rather than by reading the
docstring. There are two independent properties and each has its own attack set.

**Is the value real?** A fake model returns a paraphrase, a coerced type, a
trimmed string, a re-ordered list, a re-cased key, an empty container and a
duplicate arg binding, and every one of them must be REFUSED with a typed error
and nothing staged. That is ``ArgProvenanceAudit``.

**Does the value belong?** Provenance alone is not enough, and the case that
proves it is ``TestTargetArgsAreConfinedToTheDiff`` below: a one-line diff
composing a five-field write out of values the connector really did return in
that row. Nothing is fabricated, every leaf passes the audit, and the write is
still not the write the user approved. That is ``WriteArgScope``.

The mirror half is just as load-bearing and is asserted first: three ordinary
cell edits must arrive in ``StagedRow.target_args`` as the exact objects the user
typed, with their exact Python types — the property the whole staging engine
rests on (``rowset.StagedRow``: *"the EXACT connector-op args the shared
dispatcher sends for THIS row, verbatim"*).

Every candidate op here carries a real ``input_schema``, because the connector's
own ``required`` list is the ONLY non-model source of "which args address a
record" and a fixture without one is a fixture testing an unbounded write.

Nothing here is async and nothing here builds a model: ``RowWriteComposer`` is
pure, and ``SurfaceWriteMapper`` takes the completion as an injected seam.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult
from agent_runtime.capabilities.surfaces.write_mapping import (
    ArgBinding,
    ArgProvenanceAudit,
    ArgSourceKind,
    EditBatchValidator,
    RecordAddressAudit,
    RowWriteComposer,
    SurfaceRowEdit,
    SurfaceWriteMapper,
    WriteArgScope,
    WriteMappingAnswer,
    WriteMappingAnswerParser,
    WriteMappingError,
    WriteMappingPrompt,
    WriteMappingRejected,
    WriteMappingUnavailable,
    WriteOpCandidate,
    build_surface_write_mapper,
)
from agent_runtime.surfaces_v2.rowset import (
    ArgOrigin,
    RowFieldChange,
    RowsetValidator,
    StagedArg,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_OP = "update_issue"

_OUT_OF_SCOPE = (
    "The proposed write would send a field you did not edit. A save sends your "
    "edits and the fields this operation needs to find the record, and nothing "
    "else. Nothing was staged."
)
_UNBOUNDED = (
    "This connector does not say which arguments identify a record, so this "
    "save cannot be limited to the fields you edited. Nothing was staged."
)
_MODEL_TYPED = (
    "The proposed write types a value in directly instead of taking it from "
    "your edit or from the record. Nothing was staged."
)
_INVENTED = (
    "The proposed write contains a value that you did not enter and that was "
    "not read from the connector. Nothing was staged."
)
_UNEDITED_COLUMN = (
    "The proposed write mapping binds a column you did not edit. Nothing was staged."
)


class WriteMappingFixtureMixin:
    """The batch under test, the connector's catalogue, and answer builders.

    One shape reused everywhere so a rejection can only come from the rule being
    probed: a Linear-ish issue row read with an id + three edited cells whose
    values span the three scalar types a coercion attack can confuse (``int``,
    ``str``, ``bool``).
    """

    # The user's three cell edits. Deliberately: an int that a model may retype
    # as "3", a string with meaningful trailing whitespace, and a bool that
    # ``True == 1`` would let a naive audit confuse with a count.
    NEW_PRIORITY = 3
    NEW_TITLE = "Ship the thing "
    NEW_BLOCKED = True

    def edit(self, *, row_key: str = "ISS-1") -> SurfaceRowEdit:
        return SurfaceRowEdit(
            row_key=row_key,
            title="Ship the thing",
            row={"id": row_key, "team": "core", "priority": 1},
            changes=(
                RowFieldChange(field="priority", old=1, new=self.NEW_PRIORITY),
                RowFieldChange(field="title", old="Ship it", new=self.NEW_TITLE),
                RowFieldChange(field="blocked", old=None, new=self.NEW_BLOCKED),
            ),
        )

    def assignee_edit(self) -> SurfaceRowEdit:
        """A row whose READ payload carries a list — the container attack shape.

        ``_collect`` walks the read row to any depth, so every element of
        ``assignees`` is individually admissible; only the exactness rule stops
        the model recomposing them into a different list.
        """

        return SurfaceRowEdit(
            row_key="ISS-3",
            title="Reviewers",
            row={"id": "ISS-3", "assignees": ["alice", "bob"]},
            changes=(RowFieldChange(field="reviewers", old=None, new="carol"),),
        )

    @staticmethod
    def op(
        *,
        properties: tuple[str, ...] = (
            "id",
            "priority",
            "title",
            "blocked",
            "team",
            "assignees",
            "reviewers",
            "labels",
        ),
        required: tuple[str, ...] = ("id",),
        name: str = _OP,
    ) -> WriteOpCandidate:
        """A candidate op as an MCP descriptor really describes it.

        ``required`` is the connector's own statement of which args address a
        record, and it is the ONLY thing ``WriteArgScope`` will read as an
        allow-list — so a test that wants a different scope changes the SCHEMA,
        never the answer.
        """

        return WriteOpCandidate(
            name=name,
            description="Update one issue.",
            input_schema={
                "type": "object",
                "properties": {key: {"type": "string"} for key in properties},
                "required": list(required),
            },
        )

    def candidates(self) -> tuple[WriteOpCandidate, ...]:
        return (self.op(), WriteOpCandidate(name="create_issue", description="Create."))

    def answer(self, *extra: ArgBinding) -> WriteMappingAnswer:
        """The honest answer: id from the row, one arg per edited column."""

        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
                *extra,
            ),
        )

    def literal_answer(self, *, arg: str, value: object) -> WriteMappingAnswer:
        """The honest answer plus ONE model-typed literal — the attack vector."""

        return self.answer(
            ArgBinding(arg=arg, source=ArgSourceKind.LITERAL, value=value)
        )

    def compose(
        self,
        answer: WriteMappingAnswer,
        *edits: SurfaceRowEdit,
        candidate: WriteOpCandidate | None = None,
    ) -> tuple[object, ...]:
        return RowWriteComposer.compose(
            answer=answer,
            candidate=candidate or self.op(),
            edits=edits,
        )

    def compose_rejects(
        self,
        answer: WriteMappingAnswer,
        edit: SurfaceRowEdit,
        *,
        candidate: WriteOpCandidate | None = None,
    ) -> str:
        """Compose, require a typed rejection, and return its safe message."""

        with pytest.raises(WriteMappingRejected) as caught:
            self.compose(answer, edit, candidate=candidate)
        return caught.value.safe_message


class FakeCompletionMixin:
    """A completion seam that answers with whatever the test hands it."""

    class _Fake:
        def __init__(self, candidate: object) -> None:
            self.candidate = candidate
            self.prompts: list[tuple[str, str]] = []

        async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
            self.prompts.append((system, user))
            return SpecCompletionResult(candidate=self.candidate, raw_text="")

    class _Exploding:
        def __init__(self, error: Exception) -> None:
            self._error = error

        async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
            raise self._error

    def mapper_for(self, candidate: object) -> tuple[SurfaceWriteMapper, object]:
        fake = self._Fake(candidate)
        return SurfaceWriteMapper(completion=fake), fake


# ---------------------------------------------------------------------------
# Property 1 — the user's values reach ``target_args`` verbatim
# ---------------------------------------------------------------------------


class TestValuesPassThroughVerbatim(WriteMappingFixtureMixin):
    def test_three_cell_edits_land_in_target_args_unchanged(self) -> None:
        edit = self.edit()

        rows = self.compose(self.answer(), edit)

        assert len(rows) == 1
        assert rows[0].target_args == {
            "id": "ISS-1",
            "priority": self.NEW_PRIORITY,
            "title": self.NEW_TITLE,
            "blocked": self.NEW_BLOCKED,
        }

    def test_scalar_types_survive_composition(self) -> None:
        # ``==`` alone would pass on ``True``/``1`` and ``3``/``3.0``; the args
        # are a connector request body, so the TYPE is part of the value.
        rows = self.compose(self.answer(), self.edit())
        args = rows[0].target_args

        assert type(args["priority"]) is int
        assert type(args["title"]) is str
        assert type(args["blocked"]) is bool

    def test_trailing_whitespace_is_not_trimmed(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        assert rows[0].target_args["title"] == "Ship the thing "

    def test_row_read_values_pass_through_from_the_read_not_the_model(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        assert rows[0].target_args["id"] == "ISS-1"

    def test_one_answer_maps_a_batch_whose_rows_edited_different_cells(self) -> None:
        # A binding for a column THIS row did not edit is skipped, not rejected:
        # batch-wide coverage has already proved the column is one the BATCH
        # edited, so the skip can never hide a fabricated column name.
        partial = SurfaceRowEdit(
            row_key="ISS-2",
            title="Second",
            row={"id": "ISS-2", "team": "core", "priority": 4},
            changes=(RowFieldChange(field="priority", old=4, new=self.NEW_PRIORITY),),
        )

        rows = self.compose(self.answer(), self.edit(), partial)

        assert rows[1].target_args == {"id": "ISS-2", "priority": self.NEW_PRIORITY}
        assert "title" not in rows[1].target_args

    def test_a_list_the_user_typed_lands_verbatim(self) -> None:
        listy = SurfaceRowEdit(
            row_key="ISS-7",
            title="Reviewers",
            row={"id": "ISS-7"},
            changes=(
                RowFieldChange(field="reviewers", old=None, new=["alice", "bob"]),
            ),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
            ),
        )

        rows = self.compose(answer, listy)

        assert rows[0].target_args == {"id": "ISS-7", "reviewers": ["alice", "bob"]}

    def test_an_empty_list_the_user_actually_typed_lands_verbatim(self) -> None:
        # Clearing a multi-select IS a legitimate edit — ``labels: []`` is how a
        # connector is told to clear a field, so the empty container must reach
        # ``target_args`` when it came from the DIFF.
        cleared = SurfaceRowEdit(
            row_key="ISS-8",
            title="Cleared",
            row={"id": "ISS-8"},
            changes=(RowFieldChange(field="labels", old=["x"], new=[]),),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="labels", source=ArgSourceKind.EDITED, key="labels"),
            ),
        )

        rows = self.compose(answer, cleared)

        assert rows[0].target_args == {"id": "ISS-8", "labels": []}


# ---------------------------------------------------------------------------
# Property 2 — is the value REAL? (ArgProvenanceAudit)
# ---------------------------------------------------------------------------


class TestModelAuthoredValuesAreRefused(WriteMappingFixtureMixin):
    """Every attack the design names, one test each. None may be absorbed."""

    def test_paraphrase_is_rejected(self) -> None:
        answer = self.literal_answer(arg="summary", value="Ship the thing soon")

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_stringified_number_is_rejected(self) -> None:
        # The user typed ``3``; a provider that JSON-encodes everything as text
        # returns "3", which is a different value to a connector schema.
        answer = self.literal_answer(arg="rank", value="3")

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_float_for_int_is_rejected(self) -> None:
        answer = self.literal_answer(arg="rank", value=3.0)

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_bool_for_int_is_rejected(self) -> None:
        # ``True == 1`` in Python — the fingerprint's type tag is what stops it.
        one_edit = SurfaceRowEdit(
            row_key="ISS-9",
            title="Counts",
            row={"id": "ISS-9"},
            changes=(RowFieldChange(field="count", old=0, new=1),),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="count", source=ArgSourceKind.EDITED, key="count"),
                ArgBinding(arg="flag", source=ArgSourceKind.LITERAL, value=True),
            ),
        )

        assert self.compose_rejects(answer, one_edit) == _INVENTED

    def test_trimmed_string_is_rejected(self) -> None:
        answer = self.literal_answer(arg="name", value=self.NEW_TITLE.strip())

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_reordered_list_is_rejected(self) -> None:
        # The list lives in the row AS READ, which ``_collect`` walks to any
        # depth — so "alice" and "bob" are each admissible on their own, and a
        # leaf-wise rule admits any permutation of them.
        answer = self._reviewers_plus(
            ArgBinding(
                arg="order", source=ArgSourceKind.LITERAL, value=["bob", "alice"]
            )
        )

        assert self.compose_rejects(answer, self.assignee_edit()) == _INVENTED

    def test_truncated_list_is_rejected(self) -> None:
        # Same shape, worse outcome: dropping an element silently un-assigns a
        # person while the displayed diff still shows both.
        answer = self._reviewers_plus(
            ArgBinding(arg="assignees", source=ArgSourceKind.LITERAL, value=["alice"])
        )

        assert self.compose_rejects(answer, self.assignee_edit()) == _INVENTED

    def test_list_extended_with_an_admissible_value_is_rejected(self) -> None:
        # "carol" is the user's own new value and "alice"/"bob" are the
        # connector's — every leaf is accounted for, and the LIST still is not.
        answer = self._reviewers_plus(
            ArgBinding(
                arg="assignees",
                source=ArgSourceKind.LITERAL,
                value=["alice", "bob", "carol"],
            )
        )

        assert self.compose_rejects(answer, self.assignee_edit()) == _INVENTED

    def test_empty_list_is_rejected(self) -> None:
        # ``labels: []`` is not an absent edit — it is how a connector is told
        # to clear a field, and the user never asked for it.
        answer = self.literal_answer(arg="labels", value=[])

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_empty_object_is_rejected(self) -> None:
        answer = self.literal_answer(arg="patch", value={})

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_nested_empty_list_is_rejected(self) -> None:
        answer = self.literal_answer(arg="patch", value={"labels": []})

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_object_envelope_with_one_invented_leaf_is_named_as_invented(self) -> None:
        # The audit runs BEFORE the scope rule precisely so this reads as "you
        # did not enter this value" rather than the blunter "out of scope".
        answer = self.literal_answer(
            arg="input", value={"team": "core", "headline": "invented"}
        )

        assert self.compose_rejects(answer, self.edit()) == _INVENTED

    def test_recased_edited_column_is_rejected(self) -> None:
        # A re-cased key resolves to no column, so the edit would vanish; the
        # coverage rule refuses the WHOLE batch rather than dropping the cell.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="Priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

        assert self.compose_rejects(answer, self.edit()) == (
            "The proposed write mapping does not cover every edited field. "
            "Nothing was staged."
        )

    def test_recased_row_field_is_rejected(self) -> None:
        answer = self.answer(
            ArgBinding(arg="team", source=ArgSourceKind.ROW, key="Team")
        )

        assert self.compose_rejects(answer, self.edit()) == (
            "The proposed write mapping reads a field that this row does not have."
        )

    def test_duplicate_arg_binding_is_unrepresentable(self) -> None:
        # Two columns bound to one arg: coverage passes, the diff still shows
        # both changes, and composition would send only the last one.
        with pytest.raises(ValidationError):
            WriteMappingAnswer(
                op=_OP,
                args=(
                    ArgBinding(
                        arg="value", source=ArgSourceKind.EDITED, key="priority"
                    ),
                    ArgBinding(arg="value", source=ArgSourceKind.EDITED, key="title"),
                ),
            )

    def test_duplicate_arg_answer_is_rejected_by_the_parser(self) -> None:
        with pytest.raises(WriteMappingRejected) as caught:
            WriteMappingAnswerParser.parse(
                {
                    "op": _OP,
                    "args": [
                        {"arg": "value", "source": "edited", "key": "priority"},
                        {"arg": "value", "source": "edited", "key": "title"},
                    ],
                }
            )

        assert caught.value.safe_message == (
            "The proposed write mapping is malformed. Nothing was staged."
        )

    def test_unmapped_column_rejects_the_whole_batch(self) -> None:
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
            ),
        )

        assert self.compose_rejects(answer, self.edit())

    def test_every_row_in_a_covered_batch_sends_at_least_one_edited_value(
        self,
    ) -> None:
        """Pins why ``NO_ARGS`` is defence in depth and not a live branch.

        Coverage is batch-wide: a row's changed columns are all in ``edited``,
        so all are bound, so every row finds at least one matching binding. The
        "no edited value" guard is therefore unreachable *through* ``compose``,
        which is worth pinning — a future per-row coverage rule would make it
        live, and a future coverage rule that stopped being total would make it
        the last line of defence against a silently identity-only write.
        """

        identity_only = SurfaceRowEdit(
            row_key="ISS-5",
            title="Untouched",
            row={"id": "ISS-5", "priority": 1},
            changes=(RowFieldChange(field="owner", old="a", new="b"),),
        )
        second = SurfaceRowEdit(
            row_key="ISS-6",
            title="Also untouched",
            row={"id": "ISS-6"},
            changes=(RowFieldChange(field="other", old=1, new=2),),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="owner", source=ArgSourceKind.EDITED, key="owner"),
                ArgBinding(arg="other", source=ArgSourceKind.EDITED, key="other"),
            ),
        )

        rows = self.compose(
            answer,
            identity_only,
            second,
            # Every arg the answer names has to be one the op DECLARES, so the
            # candidate has to declare the two columns this batch edits.
            candidate=self.op(properties=("id", "owner", "other")),
        )

        assert rows[0].target_args == {"id": "ISS-5", "owner": "b"}
        assert rows[1].target_args == {"id": "ISS-6", "other": 2}

    @staticmethod
    def _reviewers_plus(extra: ArgBinding) -> WriteMappingAnswer:
        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                extra,
            ),
        )


class TestProvenanceAuditUnit(WriteMappingFixtureMixin):
    """The audit in isolation — it names the arg and never the value."""

    def test_offending_arg_names_the_argument_only(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.edit())

        assert audit.offending_arg({"id": "ISS-1", "note": "invented"}) == "note"

    def test_admissible_args_return_none(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.edit())

        assert audit.offending_arg({"id": "ISS-1", "team": "core"}) is None


# ---------------------------------------------------------------------------
# Property 3 — does the value BELONG? (WriteArgScope)
# ---------------------------------------------------------------------------


class TestTargetArgsAreConfinedToTheDiff:
    """The case provenance could not catch, reproduced exactly.

    The user changes ONE cell. Every arg the model then proposes is a value the
    connector genuinely returned in that row, so :class:`ArgProvenanceAudit`
    admits all five — and the object sent stops being the object approved::

        APPROVED   priority: 'high' -> 'low'
        SENT       id='PAR-9' priority='low'
                   assignee='alice'  state='open'  notes='high'

    ``assignee`` and ``state`` make the save a full-record overwrite that
    silently clobbers a concurrent change; ``notes='high'`` relocates the value
    the user edited AWAY from into a field they never touched. All three must be
    refused, and the two that remain must be exactly the diff plus the one key
    the connector declared it needs to find the record.
    """

    ROW = {"id": "PAR-9", "priority": "high", "assignee": "alice", "state": "open"}

    def edit(self) -> SurfaceRowEdit:
        return SurfaceRowEdit(
            row_key="PAR-9",
            title="Parity run",
            row=dict(self.ROW),
            changes=(RowFieldChange(field="priority", old="high", new="low"),),
        )

    @staticmethod
    def candidate() -> WriteOpCandidate:
        """``update_issue``: ``id`` addresses the record, the rest are fields."""

        return WriteOpCandidate(
            name=_OP,
            description="Update one issue.",
            input_schema={
                "type": "object",
                "properties": {
                    key: {"type": "string"}
                    for key in ("id", "priority", "assignee", "state", "notes")
                },
                "required": ["id"],
            },
        )

    @staticmethod
    def answer(*extra: ArgBinding) -> WriteMappingAnswer:
        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                *extra,
            ),
        )

    def compose(self, answer: WriteMappingAnswer) -> tuple[object, ...]:
        return RowWriteComposer.compose(
            answer=answer, candidate=self.candidate(), edits=(self.edit(),)
        )

    def rejects(self, answer: WriteMappingAnswer) -> str:
        with pytest.raises(WriteMappingRejected) as caught:
            self.compose(answer)
        return caught.value.safe_message

    # -- the whole attack, then each arg on its own ---------------------------

    def test_the_five_field_write_a_one_line_diff_produced_is_rejected(self) -> None:
        five_fields = self.answer(
            ArgBinding(arg="assignee", source=ArgSourceKind.ROW, key="assignee"),
            ArgBinding(arg="state", source=ArgSourceKind.ROW, key="state"),
            ArgBinding(arg="notes", source=ArgSourceKind.LITERAL, value="high"),
        )

        with pytest.raises(WriteMappingRejected):
            self.compose(five_fields)

    def test_an_untouched_row_field_is_out_of_scope(self) -> None:
        assignee = self.answer(
            ArgBinding(arg="assignee", source=ArgSourceKind.ROW, key="assignee")
        )

        assert self.rejects(assignee) == _OUT_OF_SCOPE

    def test_a_second_untouched_row_field_is_out_of_scope(self) -> None:
        state = self.answer(
            ArgBinding(arg="state", source=ArgSourceKind.ROW, key="state")
        )

        assert self.rejects(state) == _OUT_OF_SCOPE

    def test_the_value_the_user_edited_away_from_cannot_be_relocated(self) -> None:
        # ``'high'`` is ``change.old`` — provenance-clean, which is exactly why
        # the audit cannot be the thing that stops it. Assert the SCOPE rule is
        # what refuses it, or this test would pass on the broken code.
        relocated = self.answer(
            ArgBinding(arg="notes", source=ArgSourceKind.LITERAL, value="high")
        )

        assert self.rejects(relocated) == _MODEL_TYPED

    def test_each_out_of_scope_arg_passes_the_provenance_audit(self) -> None:
        """The premise of this whole class, asserted rather than assumed.

        If any of the three were fabricated, ``ArgProvenanceAudit`` would refuse
        them and ``WriteArgScope`` would be redundant. They are not: all three
        are values the connector returned in this very row.
        """

        audit = ArgProvenanceAudit.for_edit(self.edit())

        assert (
            audit.offending_arg({"assignee": "alice", "state": "open", "notes": "high"})
            is None
        )

    # -- and what a confined write actually carries ---------------------------

    def test_the_confined_write_is_the_diff_plus_the_declared_key(self) -> None:
        rows = self.compose(self.answer())

        assert rows[0].target_args == {"id": "PAR-9", "priority": "low"}

    def test_the_row_as_read_is_wider_than_the_write(self) -> None:
        # The provenance half stays wide on purpose — the row is where a scoping
        # key gets its VALUE. Being in ``row`` must not be what authorises a
        # field to be sent, or a padded ``row`` would widen the write.
        rows = self.compose(self.answer())

        assert set(self.ROW) - set(rows[0].target_args) == {"assignee", "state"}


class TestScopeIsDeclaredByTheConnectorNotTheModel(WriteMappingFixtureMixin):
    """How a scoping key is declared, and every way the declaration is bounded.

    The allow-list is read from the chosen op's ``input_schema.required`` — the
    CONNECTOR's own words. A cap, or a source kind the model declares for
    itself, would both let it relabel ``assignee`` as "scoping" and buy nothing.
    """

    def test_a_declared_required_key_composes(self) -> None:
        two_keys = self.op(
            properties=("id", "team_id", "priority", "title", "blocked"),
            required=("id", "team_id"),
        )
        edit = SurfaceRowEdit(
            row_key="ISS-1",
            title="Ship the thing",
            row={"id": "ISS-1", "team_id": "T-7", "team": "core"},
            changes=(RowFieldChange(field="priority", old=1, new=3),),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="team_id", source=ArgSourceKind.ROW, key="team_id"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
            ),
        )

        rows = self.compose(answer, edit, candidate=two_keys)

        assert rows[0].target_args == {
            "id": "ISS-1",
            "team_id": "T-7",
            "priority": 3,
        }

    def test_an_op_that_declares_no_schema_cannot_be_scoped(self) -> None:
        # The honest fail-closed edge: a connector that will not say which args
        # address a record cannot have its writes bounded, and an unbounded
        # write is the thing this rule exists to prevent.
        bare = WriteOpCandidate(name=_OP, description="Update one issue.")

        assert self.compose_rejects(self.answer(), self.edit(), candidate=bare) == (
            _UNBOUNDED
        )

    def test_an_op_that_marks_every_property_required_declares_nothing(self) -> None:
        # The strict-function-calling artifact. Read as an allow-list it would
        # re-open the whole hole: every unedited column becomes "scoping".
        strict = self.op(
            properties=("id", "priority", "title", "blocked"),
            required=("id", "priority", "title", "blocked"),
        )

        assert self.compose_rejects(self.answer(), self.edit(), candidate=strict) == (
            _UNBOUNDED
        )

    def test_more_scoping_keys_than_the_cap_is_refused(self) -> None:
        wide = self.op(
            properties=("a", "b", "c", "d", "e", "id", "priority", "title", "blocked"),
            required=("a", "b", "c", "d", "e"),
        )
        answer = self.answer(ArgBinding(arg="a", source=ArgSourceKind.ROW, key="team"))

        assert self.compose_rejects(self.answer(), self.edit(), candidate=wide) == (
            _UNBOUNDED
        )
        assert self.compose_rejects(answer, self.edit(), candidate=wide) == _UNBOUNDED

    def test_an_edit_into_a_required_arg_is_refused_not_shadow_resolved(self) -> None:
        # ``priority`` is required AND edited. This USED to resolve in the
        # user's favour: the required set minus the payload args became the
        # scope, so the edit won the slot. That subtraction is what made
        # ``bounded`` unsound — an answer that bound every required key as an
        # edit left the scope EMPTY and still reported bounded, a write with no
        # record-addressing argument at all. So the rule is now stated by NAME:
        # an edit may never fill a slot the connector declared required-to-call,
        # and the two lanes are disjoint by rule rather than by arithmetic.
        also_required = self.op(
            properties=("id", "priority", "title", "blocked", "team"),
            required=("id", "priority"),
        )
        shadowed = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
            ),
        )
        assert (
            self.compose_rejects(shadowed, self.edit(), candidate=also_required)
            == _EDIT_INTO_KEY
        )

    def test_a_declared_but_unrequired_arg_is_out_of_scope(self) -> None:
        # ``team`` is DECLARED by the op and is not required, so the only rule
        # that can refuse it is the scope one — which is the point. (An arg the
        # op does not declare at all is refused one step earlier, as
        # ``UNDECLARED_ARG``; see ``TestEveryArgIsOneTheOpDeclares``.)
        stolen = self.answer(
            ArgBinding(arg="team", source=ArgSourceKind.ROW, key="team")
        )
        assert self.compose_rejects(stolen, self.edit()) == _OUT_OF_SCOPE

    def test_a_row_container_the_user_did_not_edit_is_out_of_scope(self) -> None:
        # Echoing the connector's own ``assignees`` list back is the concurrent-
        # clobber shape, however verbatim the list is.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(arg="assignees", source=ArgSourceKind.ROW, key="assignees"),
            ),
        )

        assert self.compose_rejects(answer, self.assignee_edit()) == _OUT_OF_SCOPE

    def test_a_malformed_required_member_is_ignored_not_trusted(self) -> None:
        junk = WriteOpCandidate(
            name=_OP,
            input_schema={
                "properties": {"id": {}, "priority": {}},
                "required": [{"$ref": "#/id"}, 7, ""],
            },
        )

        assert self.compose_rejects(self.answer(), self.edit(), candidate=junk) == (
            _UNBOUNDED
        )

    def test_scope_unit_reads_only_the_connectors_declaration(self) -> None:
        # No answer is passed at all: scope is a property of the OP. Making it a
        # function of what the model emitted is exactly how an unbounded op
        # still staged a write whenever the answer carried no ROW binding.
        scope = WriteArgScope.for_op(
            candidate=self.op(
                properties=("id", "assignee", "priority", "title", "blocked"),
                required=("id",),
            ),
        )

        assert scope.scope_args == frozenset({"id"})
        assert scope.declared_args == frozenset(
            {"id", "assignee", "priority", "title", "blocked"}
        )
        assert scope.bounded is True


class TestLiteralsFillNeitherLane(WriteMappingFixtureMixin):
    """A literal is refused even when its value is beyond reproach.

    ``ArgSourceKind.LITERAL`` stays in the answer contract because a paraphrase
    refused as *"this value came from nowhere"* is diagnosable where a schema
    error is not. It no longer composes anything: a value the model typed is
    neither a cell the user edited nor a key the connector declared.
    """

    def test_a_literal_holding_the_users_own_value_is_refused(self) -> None:
        answer = self.literal_answer(arg="echo", value=self.NEW_TITLE)

        assert self.compose_rejects(answer, self.edit()) == _MODEL_TYPED

    def test_a_literal_holding_a_connector_read_value_is_refused(self) -> None:
        answer = self.literal_answer(arg="team", value="core")

        assert self.compose_rejects(answer, self.edit()) == _MODEL_TYPED

    def test_a_literal_may_not_supply_a_declared_scoping_key(self) -> None:
        """The worst residue confinement alone would have left behind.

        ``id`` IS in scope and ``"ISS-1"`` IS the row's own value — so an
        arg-set rule that only checked NAMES would let the model author the
        value that decides WHICH RECORD is written.
        """

        authored_id = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.LITERAL, value="ISS-1"),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

        assert self.compose_rejects(authored_id, self.edit()) == _MODEL_TYPED

    def test_an_object_envelope_of_admissible_leaves_is_refused(self) -> None:
        """The stated bound on finding 3 — the model no longer places values.

        The envelope was the one place the model chose WHERE a value landed, and
        both halves of ``{"input": {"team": "core", ...}}`` are the problem: an
        arg name nothing can check, wrapped around fields the diff never showed.
        The bound is that it is not expressible. An op whose args genuinely nest
        refuses loudly here; restoring it means a declared ``ArgBinding.path``
        that composition fills from a source, never a model-typed blob.
        """

        answer = self.literal_answer(
            arg="input", value={"team": "core", "headline": self.NEW_TITLE}
        )

        assert self.compose_rejects(answer, self.edit()) == _MODEL_TYPED


class TestBindingAColumnNobodyEditedIsRejected(WriteMappingFixtureMixin):
    """Coverage is equality, not containment.

    The missing direction was already refused. The extra direction was SILENTLY
    DROPPED: ``_row`` looked the column up in this row's changes, found nothing,
    and skipped — so a fabricated column name vanished without a word instead of
    failing the answer that invented it.
    """

    def test_an_edited_binding_naming_a_column_nobody_edited_is_rejected(self) -> None:
        answer = self.answer(
            ArgBinding(arg="owner", source=ArgSourceKind.EDITED, key="owner")
        )
        # Pins WHY it used to be invisible: ``owner`` resolves to no change in
        # the only row of the batch, which is the same condition a legitimate
        # per-row skip has — so the two cases were indistinguishable row by row
        # and only a BATCH-wide rule can tell them apart.
        assert "owner" not in {change.field for change in self.edit().changes}

        assert self.compose_rejects(answer, self.edit()) == _UNEDITED_COLUMN

    def test_a_column_another_row_in_the_batch_edited_is_still_skipped(self) -> None:
        partial = SurfaceRowEdit(
            row_key="ISS-2",
            title="Second",
            row={"id": "ISS-2"},
            changes=(RowFieldChange(field="priority", old=4, new=9),),
        )

        rows = self.compose(self.answer(), self.edit(), partial)

        assert rows[1].target_args == {"id": "ISS-2", "priority": 9}


# ---------------------------------------------------------------------------
# The answer contract + batch bounds
# ---------------------------------------------------------------------------


class TestAnswerContract:
    def test_literal_binding_may_not_name_a_key(self) -> None:
        with pytest.raises(ValidationError):
            ArgBinding(arg="x", source=ArgSourceKind.LITERAL, key="priority", value=1)

    def test_referencing_binding_must_name_a_key(self) -> None:
        with pytest.raises(ValidationError):
            ArgBinding(arg="x", source=ArgSourceKind.EDITED)

    def test_referencing_binding_may_not_carry_a_value(self) -> None:
        with pytest.raises(ValidationError):
            ArgBinding(arg="x", source=ArgSourceKind.ROW, key="id", value="smuggled")

    def test_non_mapping_answer_is_rejected(self) -> None:
        with pytest.raises(WriteMappingRejected):
            WriteMappingAnswerParser.parse(["not", "a", "mapping"])

    def test_null_top_level_keys_are_compacted_not_fatal(self) -> None:
        answer = WriteMappingAnswerParser.parse(
            {
                "op": _OP,
                "args": [{"arg": "id", "source": "row", "key": "id", "value": None}],
            }
        )

        assert answer.op == _OP

    def test_unknown_source_kind_is_rejected(self) -> None:
        with pytest.raises(WriteMappingRejected):
            WriteMappingAnswerParser.parse(
                {"op": _OP, "args": [{"arg": "id", "source": "invented", "key": "id"}]}
            )


class TestEditBatchBounds:
    def test_empty_batch_is_rejected(self) -> None:
        with pytest.raises(WriteMappingRejected) as caught:
            EditBatchValidator.validate(())

        assert caught.value.safe_message == "There are no edits to save."

    def test_duplicate_row_key_is_rejected(self) -> None:
        edit = SurfaceRowEdit(
            row_key="dup",
            title="t",
            changes=(RowFieldChange(field="f", old=1, new=2),),
        )

        with pytest.raises(WriteMappingRejected) as caught:
            EditBatchValidator.validate((edit, edit))

        assert caught.value.safe_message == "Row keys must be unique within one save."

    def test_row_with_no_changes_is_rejected(self) -> None:
        with pytest.raises(WriteMappingRejected):
            EditBatchValidator.validate(
                (SurfaceRowEdit(row_key="r", title="t", changes=()),)
            )

    def test_over_cap_batch_is_rejected(self) -> None:
        edits = tuple(
            SurfaceRowEdit(
                row_key=f"r{index}",
                title="t",
                changes=(RowFieldChange(field="f", old=1, new=2),),
            )
            for index in range(201)
        )

        with pytest.raises(WriteMappingRejected) as caught:
            EditBatchValidator.validate(edits)

        assert caught.value.safe_message == (
            "This save exceeds the maximum number of edited rows."
        )


# ---------------------------------------------------------------------------
# The mapper — one model call, names only, loud on every failure
# ---------------------------------------------------------------------------


class TestMapperSendsNamesNotValues(WriteMappingFixtureMixin, FakeCompletionMixin):
    HONEST_ANSWER = {
        "op": _OP,
        "args": [
            {"arg": "id", "source": "row", "key": "id"},
            {"arg": "priority", "source": "edited", "key": "priority"},
            {"arg": "title", "source": "edited", "key": "title"},
            {"arg": "blocked", "source": "edited", "key": "blocked"},
        ],
    }

    async def test_no_cell_value_reaches_the_prompt(self) -> None:
        mapper, fake = self.mapper_for(self.HONEST_ANSWER)

        await mapper.map_edits(
            connector="linear",
            read_op="list_issues",
            candidates=self.candidates(),
            edits=(self.edit(),),
        )

        _system, user = fake.prompts[0]
        payload = json.loads(user)
        assert payload["edited_columns"] == ["priority", "title", "blocked"]
        assert payload["row_fields_available"] == ["id", "team", "priority"]
        # Not a substring check on names — the VALUES must be absent entirely.
        assert self.NEW_TITLE.strip() not in user
        assert "ISS-1" not in user
        assert "core" not in user

    async def test_prompt_forbids_writing_a_value(self) -> None:
        assert "NEVER write a value" in WriteMappingPrompt.SYSTEM

    async def test_prompt_states_the_scope_rule_it_will_be_held_to(self) -> None:
        # The rule is enforced whatever the prompt says; a model that has not
        # been TOLD it, though, fails every save on a connector with optional
        # fields, which reads as a broken lane rather than a refused answer.
        assert '"required"' in WriteMappingPrompt.SYSTEM
        assert "bind NO other column" in WriteMappingPrompt.SYSTEM

    async def test_the_chosen_ops_schema_is_what_bounds_the_write(self) -> None:
        """End to end through the mapper: the scope comes from the CANDIDATE.

        ``map_edits`` resolves the answer's op back to the descriptor the
        connector supplied, so a second candidate's looser schema cannot be the
        one a write is bounded by.
        """

        mapper, _fake = self.mapper_for(
            {
                "op": _OP,
                "args": [
                    *self.HONEST_ANSWER["args"],
                    {"arg": "team", "source": "row", "key": "team"},
                ],
            }
        )

        with pytest.raises(WriteMappingRejected) as caught:
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=(
                    self.op(),
                    self.op(name="create_issue", required=("id", "team")),
                ),
                edits=(self.edit(),),
            )

        assert caught.value.safe_message == _OUT_OF_SCOPE

    async def test_answer_naming_an_op_the_connector_lacks_is_rejected(self) -> None:
        mapper, _fake = self.mapper_for(
            {
                "op": "delete_everything",
                "args": [{"arg": "id", "source": "row", "key": "id"}],
            }
        )

        with pytest.raises(WriteMappingRejected) as caught:
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=self.candidates(),
                edits=(self.edit(),),
            )

        assert caught.value.safe_message == (
            "The proposed write operation is not one this connector offers."
        )

    async def test_connector_with_no_write_op_is_rejected(self) -> None:
        mapper, _fake = self.mapper_for({"op": _OP, "args": []})

        with pytest.raises(WriteMappingRejected) as caught:
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=(),
                edits=(self.edit(),),
            )

        assert caught.value.safe_message == (
            "This connector exposes no write operation to save into."
        )

    async def test_batch_is_bounded_before_the_model_is_asked(self) -> None:
        mapper, fake = self.mapper_for({"op": _OP, "args": []})

        with pytest.raises(WriteMappingRejected):
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=self.candidates(),
                edits=(),
            )

        assert fake.prompts == []


class TestMapperFailsLoud(WriteMappingFixtureMixin, FakeCompletionMixin):
    async def test_provider_failure_raises_a_safe_typed_error(self) -> None:
        mapper = SurfaceWriteMapper(
            completion=self._Exploding(RuntimeError("api key sk-live-abc rejected")),
            model_id="gpt-5.4-mini",
        )

        with pytest.raises(WriteMappingError) as caught:
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=self.candidates(),
                edits=(self.edit(),),
            )

        assert caught.value.safe_message == (
            "The save could not be prepared. Nothing was staged."
        )
        assert "sk-live" not in str(caught.value)

    async def test_malformed_answer_raises_rather_than_returning_none(self) -> None:
        mapper, _fake = self.mapper_for("not json at all")

        with pytest.raises(WriteMappingRejected):
            await mapper.map_edits(
                connector="linear",
                read_op="list_issues",
                candidates=self.candidates(),
                edits=(self.edit(),),
            )


class TestBuilderRefusesToDegrade:
    """Property 4 at the seam: no model ⇒ RAISE, never a quiet no-op mapper."""

    def test_no_resolvable_model_raises_unavailable(self) -> None:
        with pytest.raises(WriteMappingUnavailable) as caught:
            build_surface_write_mapper(
                environ={"SURFACES_V2": "true"}, run_provider=None
            )

        assert caught.value.safe_message == (
            "Saving to this connector needs a configured model provider, and "
            "none is available for this run. Nothing was staged and nothing "
            "was sent."
        )

    def test_flag_off_with_no_override_raises_unavailable(self) -> None:
        with pytest.raises(WriteMappingUnavailable):
            build_surface_write_mapper(
                environ={"SURFACES_V2": "false"}, run_provider="openai"
            )

    def test_unknown_provider_raises_unavailable(self) -> None:
        with pytest.raises(WriteMappingUnavailable):
            build_surface_write_mapper(
                environ={"SURFACES_V2": "true"}, run_provider="ollama"
            )

    def test_unconstructible_model_raises_unavailable(self, monkeypatch) -> None:
        # A resolved id whose model will not BUILD is the second failure the
        # read path degrades on. Forced rather than provoked with a bad id:
        # whether a provider client constructs depends on process env another
        # test may have set, and this branch must be asserted, not raced.
        from agent_runtime.capabilities.surfaces import generator

        monkeypatch.setattr(
            generator.ShapingModelBuild,
            "attempt",
            classmethod(
                lambda cls, *, model_id, credentials: generator.ShapingModelBuild(
                    reason=generator.ShapingModelBuild.NO_RUN_CREDENTIAL
                )
            ),
        )

        with pytest.raises(WriteMappingUnavailable):
            build_surface_write_mapper(
                environ={"SURFACES_V2": "true", "SURFACE_SPEC_MODEL": "gpt-5.4-mini"},
                run_provider="openai",
            )

    def test_injected_completion_bypasses_model_resolution(self) -> None:
        class _Stub:
            async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
                return SpecCompletionResult(candidate={}, raw_text="")

        mapper = build_surface_write_mapper(environ={}, completion=_Stub())

        assert isinstance(mapper, SurfaceWriteMapper)


_UNDECLARED = (
    "The proposed write would send a field this operation does not accept, so "
    "what you approved and what would be sent are not the same write. Nothing "
    "was staged."
)
_RELOCATED_ROW = (
    "The proposed write would fill one of this record's identifying fields "
    "from a different field of the record. Nothing was staged."
)
_EDIT_INTO_KEY = (
    "The proposed write would put one of your edits into a field this "
    "operation uses to find the record. Nothing was staged."
)
_ARG_NAME_MISMATCH = (
    "This connector names one of its fields differently in the operation that "
    "would save it, so what you edited and what would be sent cannot be "
    "matched up. Nothing was staged."
)
_MISSING_RECORD_KEY = (
    "The proposed write does not carry the fields this operation needs to find "
    "the record. Nothing was staged."
)
_DUPLICATE_FIELD = (
    "A row lists the same field twice, so only one of the two values would be "
    "sent. Nothing was staged."
)


class TestEveryArgIsOneTheOpDeclares(WriteMappingFixtureMixin):
    """The payload lane's arg NAME was unbounded, in both directions.

    Coverage proves a binding's ``key`` names a column the user edited. Nothing
    proved anything about its ``arg`` — scope only ever inspected ``ROW``
    bindings — so an edit displayed as ``body`` could be dispatched as ``bcc``,
    and an argument the connector does not declare at all staged cleanly. The
    diff then names a field that is not sent while the payload carries a field
    the diff never names: both halves of the lie, from one binding.
    """

    def test_an_edit_relocated_onto_another_declared_arg_is_refused(self) -> None:
        # ``team`` is declared by the op and is a field of the row as read, so
        # this is the model overwriting a DIFFERENT field of the record with the
        # value from this one. The refusal is now by NAME — the arg does not
        # match the column it reads — rather than by the accident of whether
        # this particular row happened to carry a field called ``team``.
        relocated = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="team", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

        assert self.compose_rejects(relocated, self.edit()) == _ARG_NAME_MISMATCH

    def test_an_arg_the_op_never_declared_is_refused(self) -> None:
        invented_arg = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(
                    arg="webhook_url", source=ArgSourceKind.EDITED, key="priority"
                ),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

        assert self.compose_rejects(invented_arg, self.edit()) == _UNDECLARED

    def test_an_op_declaring_no_properties_refuses_every_binding(self) -> None:
        bare = WriteOpCandidate(name=_OP, input_schema={"required": ["id"]})

        assert self.compose_rejects(self.answer(), self.edit(), candidate=bare) == (
            _UNBOUNDED
        )


class TestAScopingKeyIsTheRecordsOwnField(WriteMappingFixtureMixin):
    """A ``ROW`` binding's VALUE source was unbounded even once its NAME was.

    Bounding ``binding.arg`` to the connector's ``required`` set left the model
    free to choose WHICH row field fills it: ``id`` composed from
    ``row['parent_id']`` sends the write to a different record than the one the
    diff is titled after, and ``id`` composed from a field the surface never
    rendered leaks a value nobody saw. Identity-mapping closes both.
    """

    def edit_with_parent(self) -> SurfaceRowEdit:
        return SurfaceRowEdit(
            row_key="ISS-1",
            title="Ship the thing",
            row={
                "id": "ISS-1",
                "parent_id": "ISS-9",
                "_token": "sk-live-xyz",
                "team": "core",
                "priority": 1,
            },
            changes=(RowFieldChange(field="priority", old=1, new=3),),
        )

    def one_edit_answer(self, key: str) -> WriteMappingAnswer:
        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key=key),
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
            ),
        )

    def test_a_scope_arg_filled_from_another_row_field_is_refused(self) -> None:
        rejected = self.compose_rejects(
            self.one_edit_answer("parent_id"), self.edit_with_parent()
        )

        assert rejected == _RELOCATED_ROW

    def test_a_scope_arg_filled_from_a_field_the_surface_never_showed_is_refused(
        self,
    ) -> None:
        rejected = self.compose_rejects(
            self.one_edit_answer("_token"), self.edit_with_parent()
        )

        assert rejected == _RELOCATED_ROW

    def test_the_identity_mapping_still_composes(self) -> None:
        rows = self.compose(self.one_edit_answer("id"), self.edit_with_parent())

        assert rows[0].target_args == {"id": "ISS-1", "priority": 3}


class TestOneOptionalPropertyDoesNotBuyAScope(WriteMappingFixtureMixin):
    """The strict-subset test alone was defeated by adding ONE optional property.

    ``required < properties`` holds for a five-required schema with one spare
    optional key, and the cap was applied AFTER subtracting the payload — so a
    one-line diff left four "scoping" args and reproduced the module docstring's
    own five-field exploit verbatim. The cap belongs on ``required`` itself.
    """

    def inflated(self) -> WriteOpCandidate:
        return self.op(
            properties=(
                "id",
                "priority",
                "assignee",
                "state",
                "notes",
                "idempotency_key",
            ),
            required=("id", "priority", "assignee", "state", "notes"),
        )

    def edit(self, *, row_key: str = "PAR-9") -> SurfaceRowEdit:  # noqa: ARG002
        return SurfaceRowEdit(
            row_key="PAR-9",
            title="Parity run",
            row={
                "id": "PAR-9",
                "priority": "high",
                "assignee": "alice",
                "state": "open",
                "notes": "ship by friday",
            },
            changes=(RowFieldChange(field="priority", old="high", new="low"),),
        )

    def answer(self, *extra: ArgBinding) -> WriteMappingAnswer:  # noqa: ARG002
        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                ArgBinding(arg="assignee", source=ArgSourceKind.ROW, key="assignee"),
                ArgBinding(arg="state", source=ArgSourceKind.ROW, key="state"),
                ArgBinding(arg="notes", source=ArgSourceKind.ROW, key="notes"),
            ),
        )

    def test_five_required_args_cannot_become_a_record_shaped_overwrite(self) -> None:
        rejected = self.compose_rejects(
            self.answer(), self.edit(), candidate=self.inflated()
        )

        assert rejected == _UNBOUNDED


class TestEveryComposedArgIsDisclosed(WriteMappingFixtureMixin):
    """``target_args`` is server-only, so the row has to ACCOUNT for what is sent.

    ``StageRowView`` deliberately never carries ``target_args`` and the client
    ledger projection reads the row's display half alone, so an arg with no
    counterpart there is a value dispatched with nobody's approval. On a mail op
    whose ``required`` set is the message itself, that was the recipient and the
    whole body riding a one-line subject diff.

    The account is ``sends``, keyed by the CONNECTOR's arg name, ordered exactly
    as ``target_args``. It replaced an earlier fix that appended ``old == new``
    entries to ``changes``: that one accounted only for scope args, labelled
    every entry with the COLUMN name (so a rename stayed invisible), and left
    two independently-built lists for the next producer to desynchronise.
    """

    def test_every_arg_the_write_sends_is_accounted_for_in_order(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        assert [item.arg for item in rows[0].sends] == list(rows[0].target_args)
        assert [item.new for item in rows[0].sends] == list(
            rows[0].target_args.values()
        )

    def test_a_carried_arg_is_named_with_its_value_as_read(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        carried = [item for item in rows[0].sends if item.arg == "id"]
        assert carried == [
            StagedArg(
                arg="id",
                origin=ArgOrigin.CARRIED,
                column="id",
                old="ISS-1",
                new="ISS-1",
            )
        ]

    def test_an_edited_arg_carries_its_column_and_both_values(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        edited = [item for item in rows[0].sends if item.arg == "priority"]
        assert edited == [
            StagedArg(
                arg="priority",
                origin=ArgOrigin.EDITED,
                column="priority",
                old=1,
                new=self.NEW_PRIORITY,
            )
        ]

    def test_the_users_own_diff_is_carried_verbatim(self) -> None:
        rows = self.compose(self.answer(), self.edit())

        assert rows[0].changes == self.edit().changes

    def test_the_composed_row_survives_the_staging_engines_own_check(self) -> None:
        # The composer and ``RowsetValidator`` are the two halves of one rule.
        # A row this lane produces must be one the staging engine accepts, or
        # the refusal arrives two layers down with no diagnosis attached.
        rows = self.compose(self.answer(), self.edit())

        RowsetValidator.validate(rows=tuple(rows), agent_holds=())


class TestOneFieldOneChange(WriteMappingFixtureMixin):
    """Two changes on one field: the diff renders both, exactly one is sent."""

    def test_a_duplicated_field_in_one_row_is_refused(self) -> None:
        duplicated = SurfaceRowEdit(
            row_key="ISS-1",
            title="Ship the thing",
            row={"id": "ISS-1"},
            changes=(
                RowFieldChange(field="priority", old=1, new=2),
                RowFieldChange(field="priority", old=1, new=3),
            ),
        )

        with pytest.raises(WriteMappingRejected) as caught:
            EditBatchValidator.validate([duplicated])

        assert caught.value.safe_message == _DUPLICATE_FIELD


class TestTheArgNameIsForcedToIdentity(WriteMappingFixtureMixin):
    """Three of the four attacks were a RENAME, so a rename is now refused.

    ``body → bcc``, ``subject → to`` and ``priority → description`` are the same
    move: the diff names the SOURCE column, the wire carries a different
    destination, and nothing between them could see the difference. Coverage
    constrained ``binding.key`` and nobody constrained ``binding.arg``.

    There is deliberately no alias escape hatch. ``WriteOpCandidate`` is captured
    from an MCP ``input_schema`` and JSON Schema has no keyword a connector could
    author a column→arg map in, so any alias would have to come from the MODEL —
    which is the hole, not the fix.
    """

    def relocated(self, *, arg: str, key: str) -> WriteMappingAnswer:
        """The honest answer with exactly one binding's destination moved."""

        return WriteMappingAnswer(
            op=_OP,
            args=tuple(
                ArgBinding(arg=arg, source=binding.source, key=binding.key)
                if binding.key == key
                else binding
                for binding in self.answer().args
            ),
        )

    def test_an_edit_dispatched_under_a_different_declared_name_is_refused(
        self,
    ) -> None:
        assert (
            self.compose_rejects(
                self.relocated(arg="reviewers", key="priority"), self.edit()
            )
            == _ARG_NAME_MISMATCH
        )

    def test_an_edit_dispatched_under_an_undeclared_name_is_refused(self) -> None:
        # Refused as UNDECLARED, which is the more specific diagnosis: the
        # connector never offered an arg by that name at all.
        assert (
            self.compose_rejects(
                self.relocated(arg="totally_made_up_arg", key="priority"), self.edit()
            )
            == _UNDECLARED
        )

    def test_the_identity_rule_is_stated_in_the_prompt(self) -> None:
        # Or the model burns a call per save learning it by rejection.
        system = WriteMappingPrompt.SYSTEM

        assert "IDENTICAL" in system
        assert "arg == key" in system


class TestAnEditMayNotFillARecordKey(WriteMappingFixtureMixin):
    """The rule that makes ``scope_args == required`` sound.

    ``required`` used to have the payload args subtracted from it, which had two
    failures. A five-required op with one edited column left FOUR scope args and
    passed the cap. And an answer that bound every required key as an edit left
    the remainder EMPTY while still reporting ``bounded=True`` — a write with no
    record-addressing argument at all, dispatched as ``{'issue_id': 'low'}``
    where ``low`` was the cell value the user had just typed.
    """

    def send_class_op(self) -> WriteOpCandidate:
        """A send/create-class op: every required arg IS the content."""

        return self.op(
            properties=("to", "cc", "subject", "body"),
            required=("to", "subject", "body"),
        )

    def mail_edit(self) -> SurfaceRowEdit:
        return SurfaceRowEdit(
            row_key="m-1041",
            title="Re: renewal",
            row={
                "to": "jordan@acme.example",
                "cc": "",
                "subject": "Re: renewal",
                "body": "…model-authored prose…",
            },
            changes=(RowFieldChange(field="subject", old="Re: renewal", new="Re: x"),),
        )

    def test_the_worked_exploit_is_refused_before_anything_stages(self) -> None:
        # The user edits ``subject`` on a drafted reply. ``send_reply`` requires
        # [to, subject, body], so under the old rule ``subject`` became payload
        # and ``to`` + the whole model-authored ``body`` rode along as "scope"
        # with a one-line diff naming neither.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="subject", source=ArgSourceKind.EDITED, key="subject"),
                ArgBinding(arg="to", source=ArgSourceKind.ROW, key="to"),
                ArgBinding(arg="body", source=ArgSourceKind.ROW, key="body"),
            ),
        )

        with pytest.raises(WriteMappingRejected) as caught:
            RowWriteComposer.compose(
                answer=answer,
                candidate=self.send_class_op(),
                edits=(self.mail_edit(),),
            )

        assert caught.value.safe_message == _EDIT_INTO_KEY

    def test_an_edit_that_captures_the_only_scoping_slot_is_refused(self) -> None:
        # The extreme form: the ONLY required arg is filled from an edited cell,
        # so ``required - payload`` was empty and ``bounded`` was still True.
        narrow = self.op(properties=("id", "priority"), required=("id",))
        captured = WriteMappingAnswer(
            op=_OP,
            args=(ArgBinding(arg="id", source=ArgSourceKind.EDITED, key="id"),),
        )
        edit = SurfaceRowEdit(
            row_key="ISS-1",
            title="Ship the thing",
            row={"id": "ISS-1"},
            changes=(RowFieldChange(field="id", old="ISS-1", new="low"),),
        )

        with pytest.raises(WriteMappingRejected) as caught:
            RowWriteComposer.compose(answer=captured, candidate=narrow, edits=(edit,))

        assert caught.value.safe_message == _EDIT_INTO_KEY


class TestTheOpIsBoundedBeforeAnyRowComposes(WriteMappingFixtureMixin):
    """Rule 0 — a refusal about the OP must not depend on the ANSWER's shape.

    ``bounded`` was only ever read inside the ``ROW``-binding branch, so an op
    the module had just declared unusable staged cleanly whenever the model
    happened to emit no ROW binding at all.
    """

    def unbounded(self) -> WriteOpCandidate:
        """A schema with properties and NO ``required`` key whatsoever."""

        return WriteOpCandidate(
            name=_OP,
            input_schema={
                "properties": {
                    key: {"type": "string"}
                    for key in ("id", "priority", "title", "blocked")
                }
            },
        )

    def all_edited_answer(self) -> WriteMappingAnswer:
        return WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

    def test_an_answer_with_no_row_binding_no_longer_slips_past(self) -> None:
        assert (
            self.compose_rejects(
                self.all_edited_answer(), self.edit(), candidate=self.unbounded()
            )
            == _UNBOUNDED
        )

    def test_scope_is_unbounded_for_that_op_whatever_the_answer(self) -> None:
        assert WriteArgScope.for_op(candidate=self.unbounded()).bounded is False


class TestTheRecordIsActuallyAddressed(WriteMappingFixtureMixin):
    """Rule 6 — a partial answer must not stage a write addressed at nothing.

    ``bounded`` says the op DECLARED which args find a record. This says the
    composed row carries them. Without it an answer that binds the edits and
    simply omits the id stages a write a connector may well read as a create.
    """

    def test_an_answer_omitting_the_required_key_is_refused(self) -> None:
        no_id = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="title", source=ArgSourceKind.EDITED, key="title"),
                ArgBinding(arg="blocked", source=ArgSourceKind.EDITED, key="blocked"),
            ),
        )

        assert self.compose_rejects(no_id, self.edit()) == _MISSING_RECORD_KEY


class TestOneSourceOneBinding(WriteMappingFixtureMixin):
    """The mirror of ``_args_are_distinct``: two args may not read one field."""

    def test_two_bindings_reading_the_same_key_are_unrepresentable(self) -> None:
        with pytest.raises(ValidationError) as caught:
            WriteMappingAnswer(
                op=_OP,
                args=(
                    ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
                    ArgBinding(arg="team", source=ArgSourceKind.ROW, key="id"),
                ),
            )

        assert "reads one field twice" in str(caught.value)


class TestProvenanceAdmitsOnlyWhatABindingCanReach(WriteMappingFixtureMixin):
    """The row half of ``allowed`` is TOP-LEVEL, matching the ROW binding's reach.

    A ROW binding may only read ``edit.row[arg]`` for a declared, required
    ``arg`` — a top-level lookup — so walking the row to any depth admitted
    values no binding could ever compose, and weakened the audit for nothing.
    """

    def nested_edit(self) -> SurfaceRowEdit:
        return SurfaceRowEdit(
            row_key="ISS-1",
            title="Ship the thing",
            row={"id": "ISS-1", "meta": {"token": "sk-live-xyz"}},
            changes=(RowFieldChange(field="priority", old=1, new=2),),
        )

    def test_a_nested_leaf_is_no_longer_admissible(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.nested_edit())

        assert audit.offending_arg({"id": "sk-live-xyz"}) == "id"

    def test_the_top_level_value_that_holds_it_still_is(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.nested_edit())

        assert audit.offending_arg({"meta": {"token": "sk-live-xyz"}}) is None


class TestTheConnectorsProseCannotBeUnbounded(WriteMappingFixtureMixin):
    """``description`` is interpolated verbatim into the mapping prompt.

    It is the one member of a descriptor a hostile or compromised MCP server
    writes freely, so an uncapped field is an unbounded attacker-authored string
    on a prompt. The cap does NOT close the residual — such a server can still
    describe a destructive op attractively and steer which op is picked — and
    that is bounded by port narrowing and by the user reading every outbound arg
    at the gate, not by anything in this field.
    """

    def test_an_over_long_description_is_refused_at_the_contract(self) -> None:
        with pytest.raises(ValidationError):
            WriteOpCandidate(name=_OP, description="x" * 2001)

    def test_the_prompt_carries_the_description_verbatim(self) -> None:
        rendered = WriteMappingPrompt.user(
            connector="linear",
            read_op="list_issues",
            candidates=(WriteOpCandidate(name=_OP, description="Update one issue."),),
            edited_columns=("priority",),
            row_fields=("id",),
        )

        assert (
            "Update one issue."
            in json.loads(rendered)["candidate_write_operations"][0]["description"]
        )


_UNADDRESSED_RECORD = (
    "The proposed operation does not take the identifier of the record you "
    "edited, so it would act on a different record — or bring a new one into "
    "being — rather than update this one. Nothing was staged."
)
_UNIDENTIFIED_ROW = (
    "This row carries no identifier from the connector, so a save cannot show "
    "that it would change this record rather than create another one. Nothing "
    "was staged."
)


class TestTheOpVerbIsBounded:
    """The residual every arg-set rule left open: a CREATE from an edit gesture.

    The user's gesture is *"change one field of the row I am looking at"*. The
    model answers with ``create_issue``, and every rule this module had passes —
    ``arg == key`` holds for all three bindings, ``required`` is a strict
    two-member subset of ``properties`` so the op is "bounded", every value came
    off the row or the diff so the audit admits it, and ``sends`` accounts for
    all of it in order. None of those rules asks what the operation DOES, so a
    NEW record is stood up from an edit to an existing one and the object
    approved is not even the same RECORD as the object sent.

    The bound is not the op's NAME and not its description — both are the shape
    of heuristic the earlier attackers walked through, and the second is
    attacker-writable prose. It is the record's identity: an update carries the
    address of a record that exists, a create cannot, so the op must REQUIRE the
    field the row carries its identity in.
    """

    # Verbatim from the probe.
    CREATE_SCHEMA = {
        "type": "object",
        "properties": {
            "team_id": {},
            "title": {},
            "priority": {},
            "description": {},
        },
        "required": ["team_id", "title"],
    }
    ROW = {
        "team_id": "TEAM-1",
        "title": "Fix the login redirect",
        "priority": "high",
        "description": "Repro steps live in the ticket thread.",
    }

    @staticmethod
    def op(*, name: str, properties: tuple[str, ...], required: tuple[str, ...]):
        return WriteOpCandidate(
            name=name,
            description="Does something to an issue.",
            input_schema={
                "type": "object",
                "properties": {key: {"type": "string"} for key in properties},
                "required": list(required),
            },
        )

    def create_op(self) -> WriteOpCandidate:
        return WriteOpCandidate(
            name="create_issue",
            description="Create an issue.",
            input_schema=dict(self.CREATE_SCHEMA),
        )

    def edit(self, *, row: dict | None = None, row_key: str = "PAR-9"):
        return SurfaceRowEdit(
            row_key=row_key,
            title="Fix the login redirect",
            row=dict(self.ROW) if row is None else row,
            changes=(RowFieldChange(field="priority", old="high", new="low"),),
        )

    @staticmethod
    def create_answer() -> WriteMappingAnswer:
        """The answer the probe produced: identity-mapped, in scope, accounted."""

        return WriteMappingAnswer(
            op="create_issue",
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="team_id", source=ArgSourceKind.ROW, key="team_id"),
                ArgBinding(arg="title", source=ArgSourceKind.ROW, key="title"),
            ),
        )

    @staticmethod
    def rejects(answer, candidate, edit) -> str:
        with pytest.raises(WriteMappingRejected) as caught:
            RowWriteComposer.compose(answer=answer, candidate=candidate, edits=(edit,))
        return caught.value.safe_message

    # -- the probe, exactly as it landed --------------------------------------

    def test_the_probes_create_from_an_edit_gesture_is_refused(self) -> None:
        rejected = self.rejects(self.create_answer(), self.create_op(), self.edit())

        # The probe's row carries no field holding its own key, so the refusal
        # is the earlier of the two: nothing here identifies the record at all.
        assert rejected == _UNIDENTIFIED_ROW

    def test_a_create_is_refused_even_when_the_row_IS_identified(self) -> None:
        # The sharper form. Give the row a real id, so the only thing wrong is
        # that ``create_issue`` does not take it — which is precisely the
        # difference between updating this record and standing up another.
        identified = self.edit(row={**self.ROW, "id": "PAR-9"})

        rejected = self.rejects(self.create_answer(), self.create_op(), identified)

        assert rejected == _UNADDRESSED_RECORD

    def test_the_honest_update_of_the_same_row_still_stages(self) -> None:
        # Or the rule is a refusal of everything rather than a bound on the verb.
        update = self.op(
            name="update_issue",
            properties=("id", "team_id", "title", "priority", "description"),
            required=("id",),
        )
        answer = WriteMappingAnswer(
            op="update_issue",
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
            ),
        )

        rows = RowWriteComposer.compose(
            answer=answer,
            candidate=update,
            edits=(self.edit(row={**self.ROW, "id": "PAR-9"}),),
        )

        assert rows[0].target_args == {"priority": "low", "id": "PAR-9"}

    # -- the signal is the identity, NOT the op's name -------------------------

    def test_an_op_named_create_that_requires_the_id_is_not_refused_for_its_name(
        self,
    ) -> None:
        # An upsert. A ``create_*`` regex would refuse it; the identity rule
        # does not, because it takes the address of the record in front of the
        # user and therefore acts on THAT record.
        upsert = self.op(
            name="create_issue",
            properties=("id", "priority", "description"),
            required=("id",),
        )
        answer = WriteMappingAnswer(
            op="create_issue",
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
            ),
        )

        rows = RowWriteComposer.compose(
            answer=answer,
            candidate=upsert,
            edits=(self.edit(row={**self.ROW, "id": "PAR-9"}),),
        )

        assert rows[0].target_args["id"] == "PAR-9"

    def test_an_op_named_update_that_takes_no_identifier_is_still_refused(self) -> None:
        # The mirror. The name says update and the declaration says otherwise;
        # only the declaration is a connector fact this module can act on.
        misnamed = WriteOpCandidate(
            name="update_issue",
            description="Update an issue.",
            input_schema=dict(self.CREATE_SCHEMA),
        )
        answer = WriteMappingAnswer(
            op="update_issue",
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="team_id", source=ArgSourceKind.ROW, key="team_id"),
                ArgBinding(arg="title", source=ArgSourceKind.ROW, key="title"),
            ),
        )

        rejected = self.rejects(
            answer, misnamed, self.edit(row={**self.ROW, "id": "PAR-9"})
        )

        assert rejected == _UNADDRESSED_RECORD

    # -- the class that refuses, named ----------------------------------------

    def test_a_positionally_keyed_surface_cannot_be_saved_at_all(self) -> None:
        # ``rowKeyFor`` falls back to ``row-<index>`` when the read returned no
        # id-ish field. Nothing then distinguishes updating that record from
        # creating another, so the whole lane refuses rather than guessing.
        update = self.op(
            name="update_issue",
            properties=("id", "priority"),
            required=("id",),
        )
        answer = WriteMappingAnswer(
            op="update_issue",
            args=(
                ArgBinding(arg="priority", source=ArgSourceKind.EDITED, key="priority"),
                ArgBinding(arg="id", source=ArgSourceKind.ROW, key="id"),
            ),
        )
        positional = self.edit(row={**self.ROW, "id": "PAR-9"}, row_key="row-3")

        assert self.rejects(answer, update, positional) == _UNIDENTIFIED_ROW


class TestRowIdentityIsRederivedFromTheRead:
    """``row_key`` is checked against the row, never believed on its own.

    The client is the user and not the adversary here, but the identity is
    still cross-checked against ``edit.row`` so a posted key that names nothing
    the connector returned can never become an authorisation to write.
    """

    @staticmethod
    def audit(*, row_key: str, row: dict) -> RecordAddressAudit:
        return RecordAddressAudit.for_edit(
            SurfaceRowEdit(
                row_key=row_key,
                title="t",
                row=row,
                changes=(RowFieldChange(field="priority", old=1, new=2),),
            )
        )

    def test_the_field_holding_the_key_is_the_identity_field(self) -> None:
        found = self.audit(row_key="PAR-9", row={"id": "PAR-9", "team": "core"})

        assert found.identity_fields == frozenset({"id"})

    def test_an_integer_id_matches_its_stringified_key(self) -> None:
        # ``rowKeyFor`` stringifies a finite number, so the comparison mirrors
        # it — otherwise a GitHub-ish ``number: 128`` surface refuses wrongly.
        found = self.audit(row_key="128", row={"number": 128, "title": "x"})

        assert found.identity_fields == frozenset({"number"})

    def test_a_bool_is_never_an_identifier(self) -> None:
        # ``bool`` is a subclass of ``int`` and ``str(True) == "True"``.
        found = self.audit(row_key="True", row={"blocked": True})

        assert found.identity_fields == frozenset()

    def test_a_key_matching_no_read_value_identifies_nothing(self) -> None:
        found = self.audit(row_key="PAR-9", row={"team_id": "TEAM-1", "title": "x"})

        assert found.identity_fields == frozenset()

    def test_a_nested_value_is_not_reachable_as_an_identity(self) -> None:
        # A ROW binding reads ``edit.row[arg]`` — a top-level lookup — so a
        # nested match is not an address any binding could carry.
        found = self.audit(row_key="PAR-9", row={"meta": {"id": "PAR-9"}})

        assert found.identity_fields == frozenset()

    def test_the_verb_rule_is_stated_in_the_prompt(self) -> None:
        # Or the model burns a call per save learning it by rejection.
        assert "UPDATES the record" in WriteMappingPrompt.SYSTEM
