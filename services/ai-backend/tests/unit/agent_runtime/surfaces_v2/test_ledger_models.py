"""Behavioural + adversarial tests for the Work Ledger payload models.

Every contract gets valid + invalid parsing (tests/CLAUDE.md): the validation
chokepoint rejects unknown types with a typed error + safe message, and rejects
malformed payloads (extra keys, wrong enum, ``v != 1``, both/neither decision
scope) as ``pydantic.ValidationError`` — never a silent pass.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.surfaces_v2.ledger_models import (
    ActionClass,
    ActionClassifiedPayload,
    DecisionScope,
    LedgerContractError,
    WorkLedgerVocabulary,
)


class LedgerPayloadMixin:
    """Reusable valid payloads keyed by event type."""

    @staticmethod
    def valid_action_classified() -> dict[str, object]:
        return {
            "v": 1,
            "call_id": "call_01",
            "connector": "linear",
            "op": "get_issue",
            "class": "read",
            "basis": "catalog",
        }

    @staticmethod
    def valid_gate_opened() -> dict[str, object]:
        return {
            "v": 1,
            "gate_id": "gate_01",
            "connector": "linear",
            "purpose": "to read ENG-142",
            "scopes": ["read:issues"],
            "auth_state": "missing",
        }


class TestWorkLedgerVocabulary(LedgerPayloadMixin):
    def test_unknown_event_type_raises_typed_error(self) -> None:
        with pytest.raises(LedgerContractError) as exc_info:
            WorkLedgerVocabulary.validate_payload("gate.exploded", {"v": 1})

        message = str(exc_info.value)
        assert "unknown ledger event type" in message
        assert "gate.exploded" in message

    def test_model_for_unknown_event_type_raises_typed_error(self) -> None:
        with pytest.raises(LedgerContractError):
            WorkLedgerVocabulary.model_for("not.an.event")

    def test_extra_keys_rejected(self) -> None:
        payload = self.valid_gate_opened()
        payload["surprise"] = "boom"
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload("gate.opened", payload)

    def test_wrong_enum_value_rejected(self) -> None:
        payload = self.valid_gate_opened()
        payload["auth_state"] = "revoked"  # not in the auth_state enum
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload("gate.opened", payload)

    def test_v_field_other_than_one_rejected(self) -> None:
        payload = self.valid_gate_opened()
        payload["v"] = 2
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload("gate.opened", payload)

    def test_missing_required_field_rejected(self) -> None:
        payload = self.valid_gate_opened()
        del payload["connector"]
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload("gate.opened", payload)

    def test_action_classified_round_trips_class_alias(self) -> None:
        model = WorkLedgerVocabulary.validate_payload(
            "action.classified", self.valid_action_classified()
        )
        assert isinstance(model, ActionClassifiedPayload)
        assert model.action_class is ActionClass.READ

        dumped = model.model_dump(by_alias=True)
        assert dumped["class"] == "read"
        assert "action_class" not in dumped

    def test_action_classified_accepts_either_alias_or_field_name(self) -> None:
        # `populate_by_name=True` (D3): producers may construct by the python
        # field name `action_class`; the wire alias `class` is the canonical
        # input. Both parse to the same value and dump back as `class`.
        payload = self.valid_action_classified()
        del payload["class"]
        payload["action_class"] = "read"
        model = WorkLedgerVocabulary.validate_payload("action.classified", payload)
        assert model.action_class is ActionClass.READ
        assert model.model_dump(by_alias=True)["class"] == "read"

    def test_action_classified_still_forbids_unknown_keys(self) -> None:
        # populate_by_name widens accepted names to {alias, field name} only —
        # extra=forbid still rejects anything else.
        payload = self.valid_action_classified()
        payload["klass"] = "read"
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload("action.classified", payload)


class TestDecisionScope:
    def test_rev_only_is_valid(self) -> None:
        scope = DecisionScope(rev=2)
        assert scope.rev == 2
        assert scope.row_keys is None

    def test_row_keys_only_is_valid(self) -> None:
        scope = DecisionScope(row_keys=("row_1", "row_2"))
        assert scope.row_keys == ("row_1", "row_2")
        assert scope.rev is None

    def test_both_rev_and_row_keys_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DecisionScope(rev=1, row_keys=("row_1",))
        assert "exactly one" in str(exc_info.value)

    def test_neither_rev_nor_row_keys_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DecisionScope()
        assert "exactly one" in str(exc_info.value)

    def test_decision_recorded_requires_exactly_one_scope(self) -> None:
        base = {
            "v": 1,
            "stage_id": "stage_01",
            "decision": "approve",
            "actor": "user",
        }
        WorkLedgerVocabulary.validate_payload(
            "decision.recorded", {**base, "scope": {"rev": 1}}
        )
        WorkLedgerVocabulary.validate_payload(
            "decision.recorded", {**base, "scope": {"row_keys": ["row_1"]}}
        )
        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload(
                "decision.recorded",
                {**base, "scope": {"rev": 1, "row_keys": ["row_1"]}},
            )
