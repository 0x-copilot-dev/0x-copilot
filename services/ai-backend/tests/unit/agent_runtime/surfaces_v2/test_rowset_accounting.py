"""The accounting rule: what a row DISCLOSES must be what a row SENDS.

``target_args`` is server-only — ``StageRowView`` is ``extra="forbid"`` and
omits it, and the client's ledger projection never sees it — so anything that
reaches ``target_args`` without a counterpart in the row's visible half is
invisible at the approval gate. That was not a hypothetical: an entire
model-authored email body dispatched under a one-line ``cc`` diff, and a
four-field overwrite against a *different* record dispatched under a one-line
``priority`` diff.

:attr:`StagedRow.sends` is the account, and :class:`RowsetValidator` is the ONE
place it is enforced — every staging lane already passes through it. Each rule
below is asserted by ATTACK: a row that violates it must raise the typed error
with its safe public message, and nothing may be staged.

The refusals are stated positively too. :class:`StagedRowAccounting` derives the
account server-side for the agent lane, so the model has no slot to author one
in, and the derivation itself refuses a proposal whose diff and whose args
describe different writes.
"""

from __future__ import annotations

import pytest

from agent_runtime.surfaces_v2.rowset import (
    AgentHold,
    ArgOrigin,
    ProposedRow,
    RowFieldChange,
    RowsetValidationError,
    RowsetValidator,
    StagedArg,
    StagedRow,
    StagedRowAccounting,
    UnaccountedRow,
    ValueFingerprint,
)

_UNACCOUNTED = (
    "A staged row does not disclose every field it would send, so what you "
    "would approve and what would be sent are not the same write. Nothing was "
    "staged."
)
_ARG_VALUE_MISMATCH = (
    "A staged row would send a value different from the one it discloses. "
    "Nothing was staged."
)
_UNSHOWN_CHANGE = (
    "A staged row lists a change for a field it would not send. Nothing was staged."
)
_CHANGE_VALUE_MISMATCH = (
    "A staged row's diff does not match the field it would send. Nothing was staged."
)
_CHANGE_OLD_MISMATCH = (
    "A staged row's diff and its account disagree about the value a field "
    "currently holds, so the change on screen is not the size of the change "
    "that would be made. Nothing was staged."
)
_CARRIED_ARG_CHANGED = (
    "A staged row shows a field as carried through unchanged but would send a "
    "value different from the one it read. Nothing was staged."
)
_DUPLICATE_FIELD = (
    "A row lists the same field twice, so only one of the two values would be "
    "sent. Nothing was staged."
)
_NO_TARGET_ARGS = "A staged row would send no field at all. Nothing was staged."
_TOO_MANY_SENDS = "A row exceeds the maximum number of outbound fields."


class RowsetAccountingFixtureMixin:
    """One honest row, plus the builders each attack deforms it with."""

    def row(self, **overrides: object) -> StagedRow:
        """A fully-accounted row: two args, one of them the user's own edit."""

        base: dict[str, object] = {
            "row_key": "PAR-9",
            "title": "Fix the login redirect",
            "target_args": {"issue_id": "PAR-9", "priority": "low"},
            "changes": (RowFieldChange(field="priority", old="high", new="low"),),
            "sends": (
                StagedArg(
                    arg="issue_id",
                    origin=ArgOrigin.CARRIED,
                    column="issue_id",
                    old="PAR-9",
                    new="PAR-9",
                ),
                StagedArg(
                    arg="priority",
                    origin=ArgOrigin.EDITED,
                    column="priority",
                    old="high",
                    new="low",
                ),
            ),
        }
        base.update(overrides)
        return StagedRow(**base)  # type: ignore[arg-type]

    def refuses(self, row: StagedRow) -> str:
        """Validate one row, require the typed error, return its safe message."""

        with pytest.raises(RowsetValidationError) as caught:
            RowsetValidator.validate(rows=(row,), agent_holds=())
        return caught.value.safe_message

    def derives(self, **overrides: object) -> StagedRow:
        return StagedRowAccounting.for_proposed(ProposedRow(**overrides))  # type: ignore[arg-type]

    def derivation_refuses(self, **overrides: object) -> str:
        with pytest.raises(RowsetValidationError) as caught:
            self.derives(**overrides)
        return caught.value.safe_message


class TestAnHonestRowIsAccepted(RowsetAccountingFixtureMixin):
    """The baseline every attack below is one deformation away from."""

    def test_a_fully_accounted_row_validates(self) -> None:
        RowsetValidator.validate(rows=(self.row(),), agent_holds=())

    def test_a_hold_on_an_accounted_row_still_validates(self) -> None:
        RowsetValidator.validate(
            rows=(self.row(),),
            agent_holds=(AgentHold(row_key="PAR-9", reason="recent reply"),),
        )


class TestA1TheAccountIsAnOrderedBijection(RowsetAccountingFixtureMixin):
    """Same names, same order, no duplicates — or the row does not stage.

    Ordered rather than set-equal because the ledger's canonical bytes and the
    ``proposal_digest`` an approval is pinned to are computed over this list. A
    set comparison would let two byte-different rows carry the same account.
    """

    def test_an_arg_with_no_disclosure_is_refused(self) -> None:
        # THE finding: a value reaches ``target_args`` that the visible half
        # never names. Here it is a Bcc.
        row = self.row(
            target_args={
                "issue_id": "PAR-9",
                "priority": "low",
                "bcc": "legal@acme.example",
            }
        )

        assert self.refuses(row) == _UNACCOUNTED

    def test_a_disclosure_with_no_arg_is_refused(self) -> None:
        # The mirror lie: the review shows a field the wire does not carry.
        row = self.row(
            sends=(
                *self.row().sends,
                StagedArg(arg="assignee", origin=ArgOrigin.CARRIED, new="alice"),
            )
        )

        assert self.refuses(row) == _UNACCOUNTED

    def test_a_reordered_account_is_refused(self) -> None:
        row = self.row(sends=tuple(reversed(self.row().sends)))

        assert self.refuses(row) == _UNACCOUNTED

    def test_a_duplicated_arg_in_the_account_is_refused(self) -> None:
        first = self.row().sends[0]
        row = self.row(sends=(first, first))

        assert self.refuses(row) == _UNACCOUNTED

    def test_an_empty_account_over_real_args_is_refused(self) -> None:
        # The migration case: a proposal persisted before this rule existed.
        # Fail-closed is the correct answer — a write proposed under the old
        # accounting must not be approvable under the new one.
        row = self.row(sends=())

        assert self.refuses(row) == _UNACCOUNTED


class TestA2TheDisclosedValueIsTheSentValue(RowsetAccountingFixtureMixin):
    """Naming the arg is not enough; the value has to be the same value."""

    def test_a_disclosed_value_that_differs_from_the_arg_is_refused(self) -> None:
        row = self.row(
            sends=(
                StagedArg(
                    arg="issue_id",
                    origin=ArgOrigin.CARRIED,
                    column="issue_id",
                    old="PAR-9",
                    new="PAR-9",
                ),
                StagedArg(
                    arg="priority",
                    origin=ArgOrigin.EDITED,
                    column="priority",
                    old="high",
                    new="medium",  # shown as medium, sent as low
                ),
            )
        )

        assert self.refuses(row) == _ARG_VALUE_MISMATCH

    def test_a_boolean_shown_for_a_number_is_refused(self) -> None:
        # ``True == 1`` in Python, so ``==`` is the wrong test. The fingerprint
        # is type-tagged, which is the whole reason it exists.
        row = self.row(
            target_args={"issue_id": "PAR-9", "estimate": 1},
            changes=(),
            sends=(
                StagedArg(
                    arg="issue_id", origin=ArgOrigin.CARRIED, old="PAR-9", new="PAR-9"
                ),
                StagedArg(arg="estimate", origin=ArgOrigin.CARRIED, old=True, new=True),
            ),
        )

        assert self.refuses(row) == _ARG_VALUE_MISMATCH

    def test_the_fingerprint_separates_types_that_compare_equal(self) -> None:
        assert ValueFingerprint.of(True) != ValueFingerprint.of(1)
        assert ValueFingerprint.of(1) != ValueFingerprint.of(1.0)
        assert ValueFingerprint.of("1") != ValueFingerprint.of(1)
        assert ValueFingerprint.of({"a": 1, "b": 2}) == ValueFingerprint.of(
            {"b": 2, "a": 1}
        )


class TestA3NoChangeGoesUnsent(RowsetAccountingFixtureMixin):
    """``changes`` is a checked SUBSET view, never an independent artifact."""

    def test_a_change_naming_a_field_no_arg_carries_is_refused(self) -> None:
        # Finding #9's exploit shape: the diff says ``priority``, the wire has
        # no ``priority`` key at all. Refused, never silently dropped.
        row = self.row(
            target_args={"issue_id": "PAR-1", "assignee": "mallory"},
            changes=(RowFieldChange(field="priority", old="high", new="low"),),
            sends=(
                StagedArg(arg="issue_id", origin=ArgOrigin.PROPOSED, new="PAR-1"),
                StagedArg(arg="assignee", origin=ArgOrigin.PROPOSED, new="mallory"),
            ),
        )

        assert self.refuses(row) == _UNSHOWN_CHANGE

    def test_a_change_whose_new_value_is_not_what_is_sent_is_refused(self) -> None:
        row = self.row(
            changes=(RowFieldChange(field="priority", old="high", new="urgent"),)
        )

        assert self.refuses(row) == _CHANGE_VALUE_MISMATCH


class TestA6CarriedMeansUnchanged(RowsetAccountingFixtureMixin):
    """The one rule about ``old`` that is falsifiable at this layer.

    ``origin: carried`` is a CLAIM — "read from this record and sent back
    unchanged" — and a UI that renders carried args as inert is entitled to
    believe it. So it is checked against the arg's own ``new``: an arg that
    claims to be carrying a value through while sending a different one is an
    overwrite wearing the costume of a no-op.
    """

    def test_a_carried_arg_that_changes_the_value_is_refused(self) -> None:
        row = self.row(
            target_args={"issue_id": "PAR-9", "priority": "low"},
            changes=(),
            sends=(
                StagedArg(
                    arg="issue_id",
                    origin=ArgOrigin.CARRIED,
                    column="issue_id",
                    old="PAR-9",
                    new="PAR-9",
                ),
                # The disguise: a real overwrite of ``priority`` labelled as
                # untouched, so the reviewer's eye slides off it.
                StagedArg(
                    arg="priority",
                    origin=ArgOrigin.CARRIED,
                    column="priority",
                    old="high",
                    new="low",
                ),
            ),
        )

        assert self.refuses(row) == _CARRIED_ARG_CHANGED

    def test_a_carried_arg_with_no_prior_value_is_refused(self) -> None:
        # ``old`` left absent is not "unknown, allow it" — an arg that cannot
        # say what it read cannot claim it is sending that value back.
        row = self.row(
            target_args={"issue_id": "PAR-9"},
            changes=(),
            sends=(StagedArg(arg="issue_id", origin=ArgOrigin.CARRIED, new="PAR-9"),),
        )

        assert self.refuses(row) == _CARRIED_ARG_CHANGED

    def test_only_carried_claims_unchanged(self) -> None:
        # An edited or proposed arg makes no such claim, so no rule here reads
        # its ``old`` — which is exactly why ``origin`` is the provenance the
        # wire carries.
        assert ArgOrigin.CARRIED.claims_unchanged is True
        assert ArgOrigin.EDITED.claims_unchanged is False
        assert ArgOrigin.PROPOSED.claims_unchanged is False

    def test_an_edited_arg_may_change_the_value(self) -> None:
        RowsetValidator.validate(rows=(self.row(),), agent_holds=())


class TestA7TheTwoHalvesAgreeAboutOld(RowsetAccountingFixtureMixin):
    """``changes`` and ``sends`` must report the same prior value — INTEGRITY only.

    Read what this rule is honestly. It compares two halves of ONE payload; it
    does NOT verify ``old`` against the record, because nothing at this layer
    can: the validator is pure, holds no store and no connector handle, and
    neither producer re-reads the record here. On the agent lane
    ``StagedRowAccounting.for_proposed`` copies one half from the other, so the
    rule cannot fail on a freshly-derived row — the test below pins that fact
    rather than hiding it. Where it DOES fire is the shape it exists for: a row
    whose two halves were composed independently, which is precisely what the
    fold rebuilds from a ledger payload on replay.
    """

    def test_two_halves_that_disagree_about_the_prior_value_are_refused(self) -> None:
        # A diff that misreports the prior value misrepresents the SIZE of the
        # change being approved: "none → low" reads as filling in a blank,
        # "high → low" reads as a downgrade.
        row = self.row(
            changes=(RowFieldChange(field="priority", old="none", new="low"),)
        )

        assert self.refuses(row) == _CHANGE_OLD_MISMATCH

    def test_the_rule_cannot_fire_on_a_derived_agent_row(self) -> None:
        # Stated, not assumed. ``for_proposed`` sets ``sends[].old`` FROM
        # ``changes[].old``, so the two halves are the same model claim and
        # agreement is structural. The reviewer's "Currently" column on this
        # lane is the agent's assertion about the record, and ``origin`` is
        # what says so.
        derived = self.derives(
            row_key="PAR-9",
            title="Fix the login redirect",
            target_args={"priority": "low"},
            changes=(RowFieldChange(field="priority", old="fabricated", new="low"),),
        )

        RowsetValidator.validate(rows=(derived,), agent_holds=())
        assert derived.sends[0].old == "fabricated"
        assert derived.sends[0].origin is ArgOrigin.PROPOSED


class TestA4AndA5ShapeRules(RowsetAccountingFixtureMixin):
    """Uniqueness and non-vacuity — the two shapes that render one thing and send another."""

    def test_two_changes_on_one_field_are_refused(self) -> None:
        # The diff renders both lines; exactly one value is ever sent. The
        # user-save lane already refused this shape; the agent lane did not.
        row = self.row(
            changes=(
                RowFieldChange(field="priority", old="high", new="low"),
                RowFieldChange(field="priority", old="high", new="low"),
            )
        )

        assert self.refuses(row) == _DUPLICATE_FIELD

    def test_a_row_that_sends_nothing_is_refused(self) -> None:
        row = self.row(target_args={}, changes=(), sends=())

        assert self.refuses(row) == _NO_TARGET_ARGS

    def test_a_row_over_the_outbound_cap_is_refused(self) -> None:
        wide = {f"a{index}": index for index in range(41)}
        row = self.row(
            target_args=wide,
            changes=(),
            sends=tuple(
                StagedArg(arg=name, origin=ArgOrigin.PROPOSED, new=value)
                for name, value in wide.items()
            ),
        )

        assert self.refuses(row) == _TOO_MANY_SENDS


class TestTheAgentLanesAccountIsDerivedNotAuthored(RowsetAccountingFixtureMixin):
    """The model proposes args; the SERVER says what that means.

    ``ProposedRow`` has no ``sends`` field and ``RuntimeContract`` is
    ``extra="forbid"``, so a model that tries to write its own account is a
    ``ValidationError``, not a row with a flattering diff.
    """

    def test_a_model_authored_account_is_not_even_representable(self) -> None:
        with pytest.raises(Exception) as caught:
            ProposedRow(
                row_key="PAR-9",
                title="Fix the login redirect",
                target_args={"issue_id": "PAR-9"},
                sends=[{"arg": "issue_id", "origin": "carried", "new": "PAR-9"}],
            )

        assert "sends" in str(caught.value)

    def test_every_arg_is_derived_in_target_args_order(self) -> None:
        derived = self.derives(
            row_key="PAR-1",
            title="Fix the login redirect",
            target_args={
                "issue_id": "PAR-1",
                "description": "",
                "assignee": "mallory",
                "state": "cancelled",
            },
            changes=(),
        )

        assert [(item.arg, item.origin) for item in derived.sends] == [
            ("issue_id", ArgOrigin.PROPOSED),
            ("description", ArgOrigin.PROPOSED),
            ("assignee", ArgOrigin.PROPOSED),
            ("state", ArgOrigin.PROPOSED),
        ]
        RowsetValidator.validate(rows=(derived,), agent_holds=())

    def test_a_change_carries_its_prior_value_and_its_column(self) -> None:
        derived = self.derives(
            row_key="PAR-9",
            title="Fix the login redirect",
            target_args={"issue_id": "PAR-9", "priority": "low"},
            changes=(RowFieldChange(field="priority", old="high", new="low"),),
        )

        assert derived.sends[1] == StagedArg(
            arg="priority",
            origin=ArgOrigin.PROPOSED,
            column="priority",
            old="high",
            new="low",
        )

    def test_a_change_for_a_field_no_arg_carries_refuses_the_derivation(self) -> None:
        # Finding #9, at the point the proposal is formed rather than two layers
        # down: the model shows a ``priority`` diff and sends four other fields.
        assert (
            self.derivation_refuses(
                row_key="PAR-9",
                title="Fix the login redirect",
                target_args={"issue_id": "PAR-1", "assignee": "mallory"},
                changes=(RowFieldChange(field="priority", old="high", new="low"),),
            )
            == _UNSHOWN_CHANGE
        )

    def test_a_change_that_lies_about_its_new_value_refuses_the_derivation(
        self,
    ) -> None:
        assert (
            self.derivation_refuses(
                row_key="PAR-9",
                title="Fix the login redirect",
                target_args={"issue_id": "PAR-9", "priority": "urgent"},
                changes=(RowFieldChange(field="priority", old="high", new="low"),),
            )
            == _CHANGE_VALUE_MISMATCH
        )


class TestTheSameRuleIsReusableAfterStaging(RowsetAccountingFixtureMixin):
    """``first_unaccounted`` is how the later gates ask the SAME question.

    Staging validates a whole proposed set. A decision, an apply authorization
    and the dispatch handler each hold rows folded back out of the ledger, and
    they must not re-implement the rule to check them — one definition, one
    vocabulary of refusals, and an answer shaped so the caller can name the row.
    """

    def test_an_accounted_set_has_no_first_unaccounted(self) -> None:
        assert RowsetValidator.first_unaccounted((self.row(), self.row())) is None

    def test_it_names_the_first_offender_in_row_order(self) -> None:
        good = self.row()
        bad = self.row(row_key="PAR-1", sends=())
        worse = self.row(row_key="PAR-2", sends=())

        found = RowsetValidator.first_unaccounted((good, bad, worse))

        assert found is not None
        assert found.row_key == "PAR-1"
        assert found.reason == _UNACCOUNTED

    def test_the_message_re_tenses_the_rule_for_the_dispatch_boundary(self) -> None:
        # The row WAS staged by the time these gates run, so "Nothing was
        # staged." would be false. The clause is one definition either way —
        # the suffix is stripped and replaced, never restated.
        found = RowsetValidator.first_unaccounted(
            (self.row(row_key="PAR-7", sends=()),)
        )

        assert found is not None
        message = found.message()
        assert message.startswith('Row "PAR-7" cannot be applied.')
        assert "Nothing was staged." not in message
        assert message.endswith("Nothing was sent.")
        assert "does not disclose every field it would send" in message

    def test_a_row_with_no_staged_content_gets_its_own_reason(self) -> None:
        missing = UnaccountedRow.for_missing_content("PAR-4")

        assert 'Row "PAR-4"' in missing.message()
        assert "no staged content left" in missing.message()

    def test_assert_accounted_is_the_single_row_entry_point(self) -> None:
        RowsetValidator.assert_accounted(self.row())
        with pytest.raises(RowsetValidationError) as caught:
            RowsetValidator.assert_accounted(self.row(sends=()))

        assert caught.value.safe_message == _UNACCOUNTED


class TestAnUntrustedLedgerAccountIsRebuiltWholeOrNotAtAll:
    """A replayed run must be reviewable on the terms it was staged on.

    A partial rebuild is the failure mode that matters: it would under-disclose
    an arg that still dispatches, and the row would look reviewable. Whole or
    nothing, and ``nothing`` refuses at the validator.
    """

    def test_a_well_formed_account_round_trips(self) -> None:
        parsed = RowsetValidator.sends_of(
            [
                {
                    "arg": "issue_id",
                    "origin": "carried",
                    "column": "issue_id",
                    "old": "PAR-9",
                    "new": "PAR-9",
                }
            ]
        )

        assert parsed == (
            StagedArg(
                arg="issue_id",
                origin=ArgOrigin.CARRIED,
                column="issue_id",
                old="PAR-9",
                new="PAR-9",
            ),
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "not-a-list",
            [{"arg": "issue_id"}],  # no origin
            [{"arg": "issue_id", "origin": "invented", "new": "PAR-9"}],
            [{"origin": "carried", "new": "PAR-9"}],  # no arg
        ],
    )
    def test_an_unparseable_account_yields_nothing(self, raw: object) -> None:
        assert RowsetValidator.sends_of(raw) == ()

    @pytest.mark.parametrize(
        "tail",
        [
            7,  # not an object at all
            {"arg": "priority", "origin": "invented", "new": "low"},  # bad origin
            {"origin": "carried", "new": "low"},  # no arg name
        ],
    )
    def test_a_bad_member_takes_its_GOOD_SIBLINGS_with_it(self, tail: object) -> None:
        # The case that separates "whole or nothing" from "skip what fails". A
        # surviving prefix is the dangerous outcome: the row renders as
        # reviewable while the args in the dropped tail still dispatch.
        good = {
            "arg": "issue_id",
            "origin": "carried",
            "column": "issue_id",
            "old": "PAR-9",
            "new": "PAR-9",
        }

        assert RowsetValidator.sends_of([good, tail]) == ()
