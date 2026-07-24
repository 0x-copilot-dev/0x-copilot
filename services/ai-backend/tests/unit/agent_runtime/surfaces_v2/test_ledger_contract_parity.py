"""Cross-language parity: the pydantic Work Ledger models vs the JSON SSOT.

The keystone test (replicates ``test_schema_parity.py`` for surface_spec): the
pydantic models' event-type set/order, value-enum values/order, per-event
required lists, and payload version must match ``work_ledger.json`` in
``copilot_service_contracts``. The ts side (``ledger.test.ts``) pins the same
JSON + the same golden fixture, so the three mirrors cannot drift silently.
"""

from __future__ import annotations

from copilot_service_contracts.work_ledger import (
    LEDGER_EVENT_TYPES,
    LEDGER_PAYLOAD_VERSION,
    load_ledger_golden_events,
    load_ledger_golden_journeys,
    load_work_ledger_contract,
)

from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    LedgerPayload,
    WorkLedgerVocabulary,
)

_FORBIDDEN_TENANCY_FIELDS = frozenset(
    {"org_id", "user_id", "tenant_id", "organization_id"}
)


class LedgerContractMixin:
    """Resolves the SSOT contract + golden fixture for the parity assertions."""

    @staticmethod
    def contract() -> dict[str, object]:
        return load_work_ledger_contract()

    @staticmethod
    def contract_event_keys() -> list[str]:
        events = load_work_ledger_contract()["events"]
        assert isinstance(events, dict)
        return list(events.keys())

    @staticmethod
    def contract_enums() -> dict[str, list[str]]:
        enums = load_work_ledger_contract()["enums"]
        assert isinstance(enums, dict)
        return {str(key): list(value) for key, value in enums.items()}

    @staticmethod
    def contract_required(event_type: str) -> set[str]:
        events = load_work_ledger_contract()["events"]
        assert isinstance(events, dict)
        return set(events[event_type]["required"])


class TestLedgerContractParity(LedgerContractMixin):
    def test_event_type_values_match_contract(self) -> None:
        contract_keys = set(self.contract_event_keys())
        enum_values = {member.value for member in LedgerEventType}
        constant_values = set(LEDGER_EVENT_TYPES)

        assert contract_keys == enum_values == constant_values
        assert len(contract_keys) == 32

    def test_event_type_order_is_stable(self) -> None:
        # Ordering is part of the contract: the JSON events insertion order, the
        # LEDGER_EVENT_TYPES tuple, and the StrEnum declaration all agree.
        contract_order = self.contract_event_keys()

        assert list(LEDGER_EVENT_TYPES) == contract_order
        assert [member.value for member in LedgerEventType] == contract_order

    def test_every_event_type_has_a_payload_model(self) -> None:
        model_keys = set(WorkLedgerVocabulary.PAYLOAD_MODELS.keys())

        assert model_keys == set(LedgerEventType)
        assert len(WorkLedgerVocabulary.PAYLOAD_MODELS) == 32
        for model in WorkLedgerVocabulary.PAYLOAD_MODELS.values():
            assert issubclass(model, LedgerPayload)

    def test_enum_key_sets_match_contract(self) -> None:
        assert set(WorkLedgerVocabulary.ENUM_TYPES.keys()) == set(
            self.contract_enums().keys()
        )

    def test_enum_values_match_contract(self) -> None:
        for key, values in self.contract_enums().items():
            enum_type = WorkLedgerVocabulary.ENUM_TYPES[key]
            model_values = [member.value for member in enum_type]
            # Order is part of the contract (mirrors surface_spec archetype order).
            assert model_values == values, key

    def test_required_lists_match_models(self) -> None:
        for event_type in LedgerEventType:
            model = WorkLedgerVocabulary.PAYLOAD_MODELS[event_type]
            schema = model.model_json_schema(by_alias=True)
            model_required = set(schema.get("required") or [])
            contract_required = self.contract_required(event_type.value)

            assert model_required == contract_required, event_type.value
            # `v` is always required (base class, no default).
            assert "v" in model_required

    def test_complete_field_lists_match_models(self) -> None:
        events = self.contract()["events"]
        assert isinstance(events, dict)
        for event_type in LedgerEventType:
            metadata = events[event_type.value]
            assert isinstance(metadata, dict)
            declared = set(metadata["required"]) | set(metadata.get("optional") or [])
            model = WorkLedgerVocabulary.PAYLOAD_MODELS[event_type]
            schema = model.model_json_schema(by_alias=True)
            assert set(schema["properties"]) == declared, event_type.value

    def test_payload_version_const_is_one(self) -> None:
        assert LEDGER_PAYLOAD_VERSION == 1
        assert self.contract()["payload_version"] == LEDGER_PAYLOAD_VERSION
        for model in WorkLedgerVocabulary.PAYLOAD_MODELS.values():
            v_schema = model.model_json_schema()["properties"]["v"]
            assert v_schema.get("const") == 1

    def test_golden_events_all_validate(self) -> None:
        golden = load_ledger_golden_events()
        events = golden["events"]
        assert isinstance(events, list)

        seen: set[str] = set()
        for event in events:
            assert isinstance(event, dict)
            event_type = event["event_type"]
            payload = event["payload"]
            assert isinstance(event_type, str)
            assert isinstance(payload, dict)
            validated = WorkLedgerVocabulary.validate_payload(event_type, payload)
            assert isinstance(validated, LedgerPayload)
            seen.add(event_type)

        # The immutable v2 fixture remains replayable without being rewritten.
        assert seen == set(list(LEDGER_EVENT_TYPES)[:15])

    def test_v2_1_golden_journeys_all_validate(self) -> None:
        fixture = load_ledger_golden_journeys()
        journeys = fixture["journeys"]
        assert isinstance(journeys, list)
        assert len(journeys) >= 12

        seen: set[str] = set()
        for journey in journeys:
            assert isinstance(journey, dict)
            events = journey["events"]
            assert isinstance(events, list)
            for event in events:
                assert isinstance(event, dict)
                event_type = event["event_type"]
                payload = event["payload"]
                assert isinstance(event_type, str)
                assert isinstance(payload, dict)
                WorkLedgerVocabulary.validate_payload(event_type, payload)
                seen.add(event_type)

        assert set(list(LEDGER_EVENT_TYPES)[15:]).issubset(seen)

    def test_no_org_or_user_fields_on_any_payload(self) -> None:
        # Wire-shape tenancy rule: attribution rides the run envelope, never the
        # payload. Adversarially assert no payload leaks org/user identity.
        for model in WorkLedgerVocabulary.PAYLOAD_MODELS.values():
            field_names = set(model.model_fields.keys())
            leaked = field_names & _FORBIDDEN_TENANCY_FIELDS
            assert leaked == set(), (model.__name__, leaked)
