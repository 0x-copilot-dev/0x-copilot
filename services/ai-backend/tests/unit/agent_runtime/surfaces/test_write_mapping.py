"""Write-mapping tests — the model picks the tool and the arg NAMES, never a value.

This is the one module in the tree that can compose a request to a user's real
Linear, so its safety property is asserted by attack rather than by reading the
docstring: a fake model returns a paraphrase, a coerced type, a trimmed string, a
re-ordered list, a re-cased key, an empty container and a duplicate arg binding,
and every one of them must be REFUSED with a typed error and nothing staged.

The mirror half is just as load-bearing and is asserted first: three ordinary
cell edits must arrive in ``StagedRow.target_args`` as the exact objects the user
typed, with their exact Python types — the property the whole staging engine
rests on (``rowset.StagedRow``: *"the EXACT connector-op args the shared
dispatcher sends for THIS row, verbatim"*).

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
    RowWriteComposer,
    SurfaceRowEdit,
    SurfaceWriteMapper,
    WriteMappingAnswer,
    WriteMappingAnswerParser,
    WriteMappingError,
    WriteMappingPrompt,
    WriteMappingRejected,
    WriteMappingUnavailable,
    WriteOpCandidate,
    build_surface_write_mapper,
)
from agent_runtime.surfaces_v2.rowset import RowFieldChange

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_OP = "update_issue"


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

    def candidates(self) -> tuple[WriteOpCandidate, ...]:
        return (
            WriteOpCandidate(
                name=_OP,
                description="Update one issue.",
                input_schema={"type": "object", "properties": {"id": {}}},
            ),
            WriteOpCandidate(name="create_issue", description="Create an issue."),
        )

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

    @staticmethod
    def compose_rejects(answer: WriteMappingAnswer, edit: SurfaceRowEdit) -> str:
        """Compose, require a typed rejection, and return its safe message."""

        with pytest.raises(WriteMappingRejected) as caught:
            RowWriteComposer.compose(answer=answer, edits=(edit,))
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

        rows = RowWriteComposer.compose(answer=self.answer(), edits=(edit,))

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
        rows = RowWriteComposer.compose(answer=self.answer(), edits=(self.edit(),))
        args = rows[0].target_args

        assert type(args["priority"]) is int
        assert type(args["title"]) is str
        assert type(args["blocked"]) is bool

    def test_trailing_whitespace_is_not_trimmed(self) -> None:
        rows = RowWriteComposer.compose(answer=self.answer(), edits=(self.edit(),))

        assert rows[0].target_args["title"] == "Ship the thing "

    def test_row_read_values_pass_through_from_the_read_not_the_model(self) -> None:
        rows = RowWriteComposer.compose(answer=self.answer(), edits=(self.edit(),))

        assert rows[0].target_args["id"] == "ISS-1"

    def test_one_answer_maps_a_batch_whose_rows_edited_different_cells(self) -> None:
        # A binding for a column THIS row did not edit is skipped, not rejected.
        partial = SurfaceRowEdit(
            row_key="ISS-2",
            title="Second",
            row={"id": "ISS-2", "team": "core", "priority": 4},
            changes=(RowFieldChange(field="priority", old=4, new=self.NEW_PRIORITY),),
        )

        rows = RowWriteComposer.compose(
            answer=self.answer(), edits=(self.edit(), partial)
        )

        assert rows[1].target_args == {"id": "ISS-2", "priority": self.NEW_PRIORITY}
        assert "title" not in rows[1].target_args


class TestModelAuthoredValuesAreRefused(WriteMappingFixtureMixin):
    """Every attack the design names, one test each. None may be absorbed."""

    def test_paraphrase_is_rejected(self) -> None:
        answer = self.literal_answer(arg="summary", value="Ship the thing soon")

        message = self.compose_rejects(answer, self.edit())

        assert message == (
            "The proposed write contains a value that you did not enter and "
            "that was not read from the connector. Nothing was staged."
        )

    def test_stringified_number_is_rejected(self) -> None:
        # The user typed ``3``; a provider that JSON-encodes everything as text
        # returns "3", which is a different value to a connector schema.
        answer = self.literal_answer(arg="rank", value="3")

        assert self.compose_rejects(answer, self.edit())

    def test_float_for_int_is_rejected(self) -> None:
        answer = self.literal_answer(arg="rank", value=3.0)

        assert self.compose_rejects(answer, self.edit())

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

        assert self.compose_rejects(answer, one_edit)

    def test_trimmed_string_is_rejected(self) -> None:
        answer = self.literal_answer(arg="name", value=self.NEW_TITLE.strip())

        assert self.compose_rejects(answer, self.edit())

    def test_reordered_list_is_rejected(self) -> None:
        # The list lives in the row AS READ, which ``_collect`` walks to any
        # depth — so "alice" and "bob" are each admissible on their own, and a
        # leaf-wise rule admits any permutation of them.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(
                    arg="order",
                    source=ArgSourceKind.LITERAL,
                    value=["bob", "alice"],
                ),
            ),
        )

        assert self.compose_rejects(answer, self.assignee_edit())

    def test_truncated_list_is_rejected(self) -> None:
        # Same shape, worse outcome: dropping an element silently un-assigns a
        # person while the displayed diff still shows both.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(
                    arg="assignees", source=ArgSourceKind.LITERAL, value=["alice"]
                ),
            ),
        )

        assert self.compose_rejects(answer, self.assignee_edit())

    def test_list_extended_with_an_admissible_value_is_rejected(self) -> None:
        # "carol" is the user's own new value and "alice"/"bob" are the
        # connector's — every leaf is accounted for, and the LIST still is not.
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(
                    arg="assignees",
                    source=ArgSourceKind.LITERAL,
                    value=["alice", "bob", "carol"],
                ),
            ),
        )

        assert self.compose_rejects(answer, self.assignee_edit())

    def test_list_read_from_the_connector_passes_verbatim(self) -> None:
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(arg="assignees", source=ArgSourceKind.ROW, key="assignees"),
            ),
        )

        rows = RowWriteComposer.compose(answer=answer, edits=(self.assignee_edit(),))

        assert rows[0].target_args["assignees"] == ["alice", "bob"]

    def test_empty_list_is_rejected(self) -> None:
        # ``labels: []`` is not an absent edit — it is how a connector is told
        # to clear a field, and the user never asked for it.
        answer = self.literal_answer(arg="labels", value=[])

        assert self.compose_rejects(answer, self.edit())

    def test_empty_object_is_rejected(self) -> None:
        answer = self.literal_answer(arg="patch", value={})

        assert self.compose_rejects(answer, self.edit())

    def test_nested_empty_list_is_rejected(self) -> None:
        answer = self.literal_answer(arg="patch", value={"labels": []})

        assert self.compose_rejects(answer, self.edit())

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

        message = self.compose_rejects(answer, self.edit())

        assert message == (
            "The proposed write mapping does not cover every edited field. "
            "Nothing was staged."
        )

    def test_recased_row_field_is_rejected(self) -> None:
        answer = self.answer(
            ArgBinding(arg="team", source=ArgSourceKind.ROW, key="Team")
        )

        message = self.compose_rejects(answer, self.edit())

        assert message == (
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
        rejected = WriteMappingAnswerParser
        with pytest.raises(WriteMappingRejected) as caught:
            rejected.parse(
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

        rows = RowWriteComposer.compose(answer=answer, edits=(identity_only, second))

        assert rows[0].target_args == {"id": "ISS-5", "owner": "b"}
        assert rows[1].target_args == {"id": "ISS-6", "other": 2}


class TestValuesTheUserOrConnectorSupplied(WriteMappingFixtureMixin):
    """The other half: an honest citation must not be refused."""

    def test_literal_identical_to_the_users_value_passes(self) -> None:
        # The property under guard is the VALUE, not the citation.
        answer = self.literal_answer(arg="echo", value=self.NEW_TITLE)

        rows = RowWriteComposer.compose(answer=answer, edits=(self.edit(),))

        assert rows[0].target_args["echo"] == self.NEW_TITLE

    def test_literal_identical_to_a_connector_read_value_passes(self) -> None:
        answer = self.literal_answer(arg="team", value="core")

        rows = RowWriteComposer.compose(answer=answer, edits=(self.edit(),))

        assert rows[0].target_args["team"] == "core"

    def test_literal_echoing_the_prior_value_passes(self) -> None:
        # A write op that echoes ``old`` back is sending the connector its own
        # datum, which is why ``change.old`` is admissible too.
        answer = self.literal_answer(arg="previous_title", value="Ship it")

        rows = RowWriteComposer.compose(answer=answer, edits=(self.edit(),))

        assert rows[0].target_args["previous_title"] == "Ship it"

    def test_object_envelope_composed_from_admissible_leaves_passes(self) -> None:
        # The documented intent: the model composes the SHAPE a connector op
        # wants, out of values it did not author.
        answer = self.literal_answer(
            arg="input", value={"team": "core", "headline": self.NEW_TITLE}
        )

        rows = RowWriteComposer.compose(answer=answer, edits=(self.edit(),))

        assert rows[0].target_args["input"] == {
            "team": "core",
            "headline": self.NEW_TITLE,
        }

    def test_object_envelope_with_one_invented_leaf_is_rejected(self) -> None:
        answer = self.literal_answer(
            arg="input", value={"team": "core", "headline": "invented"}
        )

        assert self.compose_rejects(answer, self.edit())

    def test_list_the_user_typed_verbatim_passes(self) -> None:
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
                ArgBinding(
                    arg="reviewers", source=ArgSourceKind.EDITED, key="reviewers"
                ),
                ArgBinding(
                    arg="mirror",
                    source=ArgSourceKind.LITERAL,
                    value=["alice", "bob"],
                ),
            ),
        )

        rows = RowWriteComposer.compose(answer=answer, edits=(listy,))

        assert rows[0].target_args["mirror"] == ["alice", "bob"]

    def test_empty_list_the_user_actually_typed_passes(self) -> None:
        # Clearing a multi-select IS a legitimate edit; only an INVENTED empty
        # container is refused.
        cleared = SurfaceRowEdit(
            row_key="ISS-8",
            title="Cleared",
            row={"id": "ISS-8"},
            changes=(RowFieldChange(field="labels", old=["x"], new=[]),),
        )
        answer = WriteMappingAnswer(
            op=_OP,
            args=(
                ArgBinding(arg="labels", source=ArgSourceKind.EDITED, key="labels"),
                ArgBinding(arg="tags", source=ArgSourceKind.LITERAL, value=[]),
            ),
        )

        rows = RowWriteComposer.compose(answer=answer, edits=(cleared,))

        assert rows[0].target_args == {"labels": [], "tags": []}


class TestProvenanceAuditUnit(WriteMappingFixtureMixin):
    """The audit in isolation — it names the arg and never the value."""

    def test_offending_arg_names_the_argument_only(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.edit())

        assert audit.offending_arg({"id": "ISS-1", "note": "invented"}) == "note"

    def test_admissible_args_return_none(self) -> None:
        audit = ArgProvenanceAudit.for_edit(self.edit())

        assert audit.offending_arg({"id": "ISS-1", "team": "core"}) is None


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
    async def test_no_cell_value_reaches_the_prompt(self) -> None:
        mapper, fake = self.mapper_for(
            {
                "op": _OP,
                "args": [
                    {"arg": "id", "source": "row", "key": "id"},
                    {"arg": "priority", "source": "edited", "key": "priority"},
                    {"arg": "title", "source": "edited", "key": "title"},
                    {"arg": "blocked", "source": "edited", "key": "blocked"},
                ],
            }
        )

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
