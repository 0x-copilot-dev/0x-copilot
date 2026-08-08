"""The shaping-answer contract: the three legal answers, and everything else.

Drives the validator directly rather than a caller, because the validator IS the
security boundary for model output on the read path. The cases that matter are
the ones a well-behaved model never produces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.surfaces.shaping_answer import (
    GenerateBinding,
    SelectBinding,
    ShapingAnswerError,
    ShapingAnswerValidator,
    ShapingBindingMode,
    ShapingDeclined,
    ShapingOutcome,
    ShapingSurface,
    ShapingVerdict,
    parse_shaping_answer,
    validate_shaping_answer,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceDotPath,
    SurfaceFieldFormat,
)
from agent_runtime.surfaces_v2.ledger_models import ViewBasis


class AnswerBuilderMixin:
    """The three legal answers, verbatim from the contract."""

    @staticmethod
    def declined() -> dict[str, object]:
        return {"render": False}

    @staticmethod
    def select_answer() -> dict[str, object]:
        return {
            "render": True,
            "archetype": "table",
            "title": "Open issues",
            "binding": {
                "mode": "select",
                "items_path": "issues",
                "columns": [
                    {"label": "ID", "path": "identifier", "format": "badge"},
                    {"label": "Title", "path": "title"},
                ],
            },
        }

    @staticmethod
    def generate_answer() -> dict[str, object]:
        return {
            "render": True,
            "archetype": "table",
            "title": "Attendees",
            "binding": {
                "mode": "generate",
                "rows": [
                    {"user": "Ada", "role": "chair"},
                    {"user": "Grace", "role": "note-taker"},
                ],
                "columns": [
                    {"label": "User", "path": "user"},
                    {"label": "Role", "path": "role"},
                ],
            },
        }


class TestDeclinedAnswer(AnswerBuilderMixin):
    """``{"render": false}`` is an answer, not a failure."""

    def test_declining_validates(self) -> None:
        answer = validate_shaping_answer(self.declined())

        assert isinstance(answer, ShapingDeclined)
        assert answer.render is False

    def test_declining_is_not_an_invalid_verdict(self) -> None:
        """The distinction the whole three-verdict vocabulary exists for.

        A model that looked and said no is a different fact from a model that
        produced garbage. Folding them together would make a broken prompt
        indistinguishable from a payload with nothing in it.
        """

        outcome = parse_shaping_answer(self.declined())

        assert outcome.verdict is ShapingVerdict.DECLINED
        assert outcome.surface is None
        assert outcome.reason == ""
        assert outcome.renders is False
        assert outcome.view_basis is None

    def test_a_decline_may_say_why(self) -> None:
        """The one sibling a decline may carry, and why the line is drawn here.

        A model asked to justify itself writes the justification somewhere, and
        with no legal place to put one ``{"render": false, "reason": "the
        payload is an acknowledgement"}`` was classified INVALID: a retry spent
        and a ``schema_invalid`` recorded for an answer that was correct.
        Naming the field is what keeps that fix from becoming "a decline may
        carry any extra key" — see
        :meth:`test_declining_may_not_smuggle_a_surface`.
        """

        answer = validate_shaping_answer(
            {"render": False, "reason": "the payload is an acknowledgement"}
        )

        assert isinstance(answer, ShapingDeclined)
        assert answer.reason == "the payload is an acknowledgement"

    def test_the_note_on_a_decline_never_becomes_the_outcomes_reason(self) -> None:
        """Two different facts, and one field may only carry one of them.

        ``ShapingOutcome.reason`` means "why this answer was rejected". A
        decline was not rejected, so copying the note across would make an
        honest "no" read as a model failure in a receipt.
        """

        outcome = parse_shaping_answer({"render": False, "reason": "an ack"})

        assert outcome.verdict is ShapingVerdict.DECLINED
        assert outcome.reason == ""

    def test_the_note_on_a_decline_is_bounded(self) -> None:
        """It is model-authored text; it does not get to be unbounded."""

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer({"render": False, "reason": "x" * 201})

    def test_null_siblings_on_a_decline_are_omissions(self) -> None:
        """A provider emitting every declared key is not a malformed model.

        Structured output spells an inapplicable field ``null``, so a decline
        arrives as ``{"render": false, "archetype": null, "binding": null}``.
        Under ``extra="forbid"`` that read as garbage: another retry, another
        breakage metric, for the answer the contract most wants to hear.
        """

        outcome = parse_shaping_answer(
            {"render": False, "archetype": None, "title": None, "binding": None}
        )

        assert outcome.verdict is ShapingVerdict.DECLINED
        assert outcome.surface is None
        assert outcome.reason == ""

    @pytest.mark.parametrize(
        "smuggled",
        [
            {"binding": {"mode": "select"}},
            {"archetype": "table"},
            {"title": "Open issues"},
            {"on_click": "https://example.com/steal"},
        ],
    )
    def test_declining_may_not_smuggle_a_surface(
        self, smuggled: dict[str, object]
    ) -> None:
        """What the strictness on a decline is actually *for*.

        A decline that also describes a surface is an answer no caller can act
        on — draw it and the decline was a lie, drop it and the description was
        noise. That is worth a rejection. A decline that explains itself, or
        that spells an inapplicable field ``null``, is neither of those things
        and is accepted above; the old contract could not tell the three apart
        and refused all of them.
        """

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer({"render": False, **smuggled})


class TestSelectAnswer(AnswerBuilderMixin):
    """Paths into the payload — the values stay the connector's."""

    def test_select_validates_into_typed_members(self) -> None:
        answer = validate_shaping_answer(self.select_answer())

        assert isinstance(answer, ShapingSurface)
        assert answer.archetype is SurfaceArchetype.TABLE
        assert answer.title == "Open issues"
        binding = answer.binding
        assert isinstance(binding, SelectBinding)
        assert binding.mode is ShapingBindingMode.SELECT
        assert binding.items_path == "issues"
        assert [column.path for column in binding.columns] == ["identifier", "title"]
        assert binding.columns[0].format is SurfaceFieldFormat.BADGE

    def test_select_records_connector_provenance(self) -> None:
        """``selected`` and not ``generated``: a model chose the shape, not the values."""

        outcome = parse_shaping_answer(self.select_answer())

        assert outcome.verdict is ShapingVerdict.RENDER
        assert outcome.view_basis is ViewBasis.SELECTED

    def test_items_path_is_optional(self) -> None:
        """A record binds against the payload root; only a list needs a path."""

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        del binding["items_path"]
        answer["archetype"] = "record"

        validated = validate_shaping_answer(answer)

        assert isinstance(validated, ShapingSurface)
        assert isinstance(validated.binding, SelectBinding)
        assert validated.binding.items_path is None

    @pytest.mark.parametrize(
        "bad_path",
        [
            "issues[0].title",
            "issues.0.title()",
            "issues['title']",
            "a b",
            "",
        ],
    )
    def test_paths_are_the_constrained_dot_path_grammar(self, bad_path: str) -> None:
        """Anything that is not an identifier/index walk is rejected outright.

        The dot-path is the reason a spec is inert: it can name a value and can
        express nothing else. A select binding that could carry an expression
        would hand the model a way to run something.
        """

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["items_path"] = bad_path

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_a_dunder_path_is_accepted_and_reaches_no_attribute(self) -> None:
        """``__class__.__init__`` is a legal *name*, and that is not a hole.

        The grammar admits it — ``__class__`` is an identifier, and rejecting
        dunders here would fork this module's rules away from the SurfaceSpec
        grammar it deliberately shares. What makes it inert is the resolver:
        every step is a mapping lookup or a list index, never ``getattr``, so on
        a payload without a literal ``__class__`` key it simply finds nothing.
        Pinned as behaviour so a "faster" resolver cannot quietly introduce
        attribute access.
        """

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["items_path"] = "__class__.__init__"

        assert isinstance(validate_shaping_answer(answer), ShapingSurface)
        assert SurfaceDotPath.resolve({"issues": []}, "__class__.__init__") == (
            False,
            None,
        )

    def test_a_null_items_path_binds_the_payload_root(self) -> None:
        """``null`` and "absent" mean one thing, so a provider may spell either.

        The schema lists ``items_path`` in ``required`` with a ``T | null``
        type, which is the structured-output way to say "emit the key, use null
        to omit" — so the validator must read the null back as the omission the
        previous test exercises, not as a second, subtly different answer.
        """

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["items_path"] = None

        validated = validate_shaping_answer(answer)

        assert isinstance(validated, ShapingSurface)
        assert isinstance(validated.binding, SelectBinding)
        assert validated.binding.items_path is None

    def test_a_null_rows_beside_a_select_binding_is_an_omission(self) -> None:
        """The other half of the same rule, one level down.

        ``SelectBinding`` forbids ``rows`` outright, so a provider that writes
        ``"rows": null`` to mean "not this mode" was told its answer was
        malformed. The omission rule is applied to the binding as well as to the
        answer for exactly this case.
        """

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["rows"] = None

        assert isinstance(validate_shaping_answer(answer), ShapingSurface)

    def test_real_rows_beside_a_select_binding_are_still_rejected(self) -> None:
        """Tolerating a null is not tolerating the other mode's data."""

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["rows"] = [{"identifier": "ENG-1"}]

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_select_paths_are_not_resolved_here(self) -> None:
        """A select binding names a shape for a payload this layer never sees.

        Checking it against real data is the existing spec linter's job; failing
        it here would reject every answer, since there is no payload to check.
        """

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["items_path"] = "nothing.like.this.exists"

        assert isinstance(validate_shaping_answer(answer), ShapingSurface)


class TestGenerateAnswer(AnswerBuilderMixin):
    """Values the model wrote — for prose, where there are no paths to point at."""

    def test_generate_validates_into_typed_members(self) -> None:
        answer = validate_shaping_answer(self.generate_answer())

        assert isinstance(answer, ShapingSurface)
        binding = answer.binding
        assert isinstance(binding, GenerateBinding)
        assert binding.mode is ShapingBindingMode.GENERATE
        assert binding.rows[0] == {"user": "Ada", "role": "chair"}

    def test_generate_records_model_authored_provenance(self) -> None:
        outcome = parse_shaping_answer(self.generate_answer())

        assert outcome.verdict is ShapingVerdict.RENDER
        assert outcome.view_basis is ViewBasis.GENERATED

    def test_a_column_path_that_misses_a_row_is_rejected(self) -> None:
        """The guarantee this mode exists to make.

        The model authored the rows AND the paths in one answer, so a path that
        finds nothing is not sparse data — it is the answer contradicting
        itself, and it renders a confident heading over an empty column.
        """

        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        rows = binding["rows"]
        assert isinstance(rows, list)
        del rows[1]["role"]

        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer(answer)

        message = str(excinfo.value)
        assert "role" in message
        assert "rows[1]" in message

    def test_a_column_naming_a_key_no_row_has_is_rejected(self) -> None:
        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        columns = binding["columns"]
        assert isinstance(columns, list)
        columns.append({"label": "Email", "path": "email"})

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_an_explicit_null_resolves_and_renders_empty(self) -> None:
        """Sparse-but-honest stays expressible, so the rule above is not a trap.

        ``found`` is what the check tests, not truthiness: a key present with a
        ``null`` value is a cell the model deliberately left blank.

        This is also the boundary of the "a null is an omission" rule the
        validator applies to the answer and its binding. A row is model-authored
        DATA, not a declared field, so the rule must stop before it: strip the
        null here and a blank cell becomes a column that resolves in one row and
        not the next — the self-contradiction this mode exists to refuse.
        """

        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        rows = binding["rows"]
        assert isinstance(rows, list)
        rows[1]["role"] = None

        validated = validate_shaping_answer(answer)

        assert isinstance(validated, ShapingSurface)
        assert isinstance(validated.binding, GenerateBinding)
        assert validated.binding.rows[1]["role"] is None

    def test_nested_row_values_resolve_through_the_same_grammar(self) -> None:
        answer = self.generate_answer()
        answer["binding"] = {
            "mode": "generate",
            "rows": [{"who": {"name": "Ada"}}, {"who": {"name": "Grace"}}],
            "columns": [{"label": "Name", "path": "who.name"}],
        }

        validated = validate_shaping_answer(answer)

        assert isinstance(validated, ShapingSurface)

    def test_rows_must_be_objects(self) -> None:
        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["rows"] = ["Ada", "Grace"]

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_rows_may_not_be_empty(self) -> None:
        """An empty table is a decline wearing a heading."""

        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["rows"] = []

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_row_count_is_bounded(self) -> None:
        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["rows"] = [{"user": str(index), "role": "x"} for index in range(201)]

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_row_width_is_bounded(self) -> None:
        answer = self.generate_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        wide = {f"k{index}": index for index in range(61)}
        wide.update({"user": 0, "role": 0})
        binding["rows"] = [wide]

        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer(answer)

        assert "keys" in str(excinfo.value)


class TestMalformedAnswers(AnswerBuilderMixin):
    """Everything that is not one of the three legal answers."""

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "render it",
            42,
            ["render"],
            b"{}",
        ],
    )
    def test_a_non_object_is_not_an_answer(self, raw: object) -> None:
        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer(raw)

        assert "JSON object" in str(excinfo.value)

    @pytest.mark.parametrize("render", ["true", 1, 0, None])
    def test_render_must_be_a_real_boolean(self, render: object) -> None:
        """``1`` is not ``true``. A truthy answer is not an answer."""

        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer({"render": render})

        assert "boolean" in str(excinfo.value)

    def test_a_rendering_answer_needs_a_binding(self) -> None:
        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer(
                {"render": True, "archetype": "table", "title": "Nothing"}
            )

        assert "binding" in str(excinfo.value)

    def test_an_unknown_binding_mode_is_named_as_such(self) -> None:
        """A third mode is rejected with one message, not two union errors."""

        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["mode"] = "invent"

        with pytest.raises(ShapingAnswerError) as excinfo:
            validate_shaping_answer(answer)

        assert str(excinfo.value) == "binding.mode must be one of select, generate"

    def test_archetype_comes_from_the_curated_vocabulary(self) -> None:
        """One closed list, shared with curated specs — never a second one."""

        answer = self.select_answer()
        answer["archetype"] = "spreadsheet"

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    @pytest.mark.parametrize("archetype", [member.value for member in SurfaceArchetype])
    def test_every_spec_archetype_is_acceptable(self, archetype: str) -> None:
        """Accepting is not licensing.

        Validation stays on the whole enum so an archetype the client cannot
        draw degrades to the generic renderer instead of erroring; what a
        GENERATOR may be told to emit is the narrower ``implemented()`` set, and
        that is a prompt-side decision, not a validation-side one.
        """

        answer = self.select_answer()
        answer["archetype"] = archetype

        assert isinstance(validate_shaping_answer(answer), ShapingSurface)

    def test_title_is_required_and_bounded(self) -> None:
        answer = self.select_answer()
        answer["title"] = "x" * 121

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_unknown_top_level_keys_are_rejected(self) -> None:
        """No side-effectful member can be smuggled in beside the known ones."""

        answer = self.select_answer()
        answer["on_click"] = "https://example.com/steal"

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_an_unknown_key_holding_null_carries_nothing_and_is_dropped(self) -> None:
        """The deliberate edge of the omission rule, pinned so it is visible.

        ``"on_click": null`` names a member and supplies no value for it, so
        there is nothing to smuggle: dropping it costs the answer nothing, while
        rejecting it would put us back to failing correct answers over a
        provider's spelling of "not applicable". The test above is what keeps
        this from being a hole — a key with an actual value is still refused.
        """

        answer = self.select_answer()
        answer["on_click"] = None

        assert isinstance(validate_shaping_answer(answer), ShapingSurface)

    def test_column_count_is_bounded(self) -> None:
        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["columns"] = [
            {"label": f"c{index}", "path": f"c{index}"} for index in range(13)
        ]

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)

    def test_columns_may_not_be_empty(self) -> None:
        answer = self.select_answer()
        binding = answer["binding"]
        assert isinstance(binding, dict)
        binding["columns"] = []

        with pytest.raises(ShapingAnswerError):
            validate_shaping_answer(answer)


class TestParseIsTotal(AnswerBuilderMixin):
    """The read path must never learn that shaping was attempted."""

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "not json",
            {"render": "yes"},
            {"render": True},
            {"render": True, "archetype": "table", "title": "T"},
            {
                "render": True,
                "archetype": "table",
                "title": "T",
                "binding": {
                    "mode": "generate",
                    "rows": [{"a": 1}],
                    "columns": [{"label": "B", "path": "b"}],
                },
            },
        ],
    )
    def test_nothing_raises(self, raw: object) -> None:
        outcome = parse_shaping_answer(raw)

        assert outcome.verdict is ShapingVerdict.INVALID
        assert outcome.surface is None
        assert outcome.renders is False
        assert outcome.view_basis is None

    def test_the_reason_is_a_safe_bounded_summary(self) -> None:
        """It is fed back as a correction and written to the ledger's ``reason``.

        So it must stay short and must not become a channel for raw model output
        to reach either the next prompt or an audit row.
        """

        outcome = parse_shaping_answer(
            {
                "render": True,
                "archetype": "table",
                "title": "T",
                "binding": {
                    "mode": "generate",
                    "rows": [{"secret": "x" * 5000}],
                    "columns": [{"label": "B", "path": "b"}],
                },
            }
        )

        assert outcome.verdict is ShapingVerdict.INVALID
        assert 0 < len(outcome.reason) <= 200
        assert "x" * 100 not in outcome.reason

    def test_a_valid_answer_still_comes_back_whole(self) -> None:
        outcome = parse_shaping_answer(self.select_answer())

        assert outcome.renders is True
        assert outcome.surface is not None
        assert outcome.surface.title == "Open issues"


class TestProvenanceVocabulary:
    """The mode → basis mapping, stated once so no producer re-derives it."""

    def test_each_mode_maps_to_its_own_basis(self) -> None:
        assert ShapingBindingMode.SELECT.view_basis is ViewBasis.SELECTED
        assert ShapingBindingMode.GENERATE.view_basis is ViewBasis.GENERATED

    def test_every_mode_has_a_basis(self) -> None:
        """A mode added without a provenance claim would render un-attributable."""

        assert {mode.view_basis for mode in ShapingBindingMode} <= set(ViewBasis)
        assert len({mode.view_basis for mode in ShapingBindingMode}) == len(
            ShapingBindingMode
        )

    def test_selected_is_distinct_from_the_no_model_bases(self) -> None:
        """The split the receipt lane depends on.

        ``registry``/``schema`` mean no model was called at all; ``selected``
        means one was called and chose the shape. Collapsing ``selected`` into
        either would put a false claim in an audit export.
        """

        assert ViewBasis.SELECTED not in {
            ViewBasis.REGISTRY,
            ViewBasis.SCHEMA,
            ViewBasis.GENERATED,
        }


class TestValidatorSurface(AnswerBuilderMixin):
    """The two entry points are the same gate with different failure manners."""

    def test_validate_and_parse_agree_on_a_good_answer(self) -> None:
        answer = validate_shaping_answer(self.generate_answer())
        outcome = ShapingAnswerValidator.parse(self.generate_answer())

        assert outcome.surface == answer

    def test_the_typed_error_is_a_value_error(self) -> None:
        """Callers that only know ``ValueError`` still catch it."""

        assert issubclass(ShapingAnswerError, ValueError)


class TestOutcomeInvariants(AnswerBuilderMixin):
    """A verdict may not disagree with the evidence beside it."""

    def test_rendering_without_a_surface_is_not_constructible(self) -> None:
        """The failure mode this guards: a caller reads ``renders`` and draws
        nothing, or worse, draws a heading with no body.
        """

        with pytest.raises(ValidationError):
            ShapingOutcome(verdict=ShapingVerdict.RENDER)

    def test_a_surface_under_a_non_render_verdict_is_not_constructible(self) -> None:
        surface = validate_shaping_answer(self.select_answer())
        assert isinstance(surface, ShapingSurface)

        with pytest.raises(ValidationError):
            ShapingOutcome(verdict=ShapingVerdict.DECLINED, surface=surface)

    def test_only_a_rejection_carries_a_reason(self) -> None:
        """A reason on a decline would read as "the model failed" in a receipt."""

        with pytest.raises(ValidationError):
            ShapingOutcome(verdict=ShapingVerdict.DECLINED, reason="looked wrong")

    def test_an_invalid_outcome_always_has_something_to_say(self) -> None:
        assert ShapingOutcome.invalid("").reason != ""
