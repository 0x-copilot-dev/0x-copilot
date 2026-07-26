from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKFLOWS.parents[2]
G0_PATH = WORKFLOWS / "g0_plain_chat.py"
API_TYPES = REPO_ROOT / "packages" / "api-types" / "src" / "index.ts"
LEDGER_CONTRACT = (
    REPO_ROOT
    / "packages"
    / "service-contracts"
    / "src"
    / "copilot_service_contracts"
    / "work_ledger.json"
)
G0_SPEC = importlib.util.spec_from_file_location("g0_plain_chat", G0_PATH)
assert G0_SPEC is not None and G0_SPEC.loader is not None
g0 = importlib.util.module_from_spec(G0_SPEC)
G0_SPEC.loader.exec_module(g0)


class FacadeSession:
    """Minimal authenticated-facade fixture for the persisted-event grammar."""

    def __init__(
        self,
        events: list[dict[str, object]],
        surfaces: list[dict[str, object]] | None = None,
    ) -> None:
        self.events = events
        self.surfaces = surfaces or []

    def transport(self, method: str, path: str) -> dict[str, object]:
        self.assert_request(method, path)
        if path.endswith("/events"):
            return {"run_status": "completed", "events": self.events}
        return {"run_id": "run-g0", "surfaces": self.surfaces}

    @staticmethod
    def assert_request(method: str, path: str) -> None:
        assert method == "GET"
        assert path in {
            "/v1/agent/runs/run-g0/events",
            "/v1/agent/runs/run-g0/surfaces",
        }


class UiSession:
    """Minimal DOM fixture for the rich-UI absence guard."""

    def __init__(self, visible_selector: str) -> None:
        self.visible_selector = visible_selector

    def present(self, selector: str) -> bool:
        return selector == self.visible_selector


class G0PlainChatGrammarTests(unittest.TestCase):
    PLAIN_CHAT_ACTIVITY_KINDS = {
        "run_queued": "run",
        "run_started": "run",
        "model_call_started": "run",
        "model_call_completed": "event",
        "model_delta": "message",
        "reasoning_summary": "reasoning",
        "reasoning_summary_delta": "reasoning",
        "final_response": "message",
        "surface.created": "event",
        "receipt.emitted": "event",
        "run_completed": "run",
    }

    @staticmethod
    def typescript_array_values(constant: str) -> set[str]:
        source = API_TYPES.read_text()
        match = re.search(
            rf"export const {constant} = \[(.*?)\] as const",
            source,
            flags=re.DOTALL,
        )
        assert match is not None, f"could not find {constant} in {API_TYPES}"
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    @classmethod
    def runtime_and_ledger_event_types(cls) -> set[str]:
        ledger = json.loads(LEDGER_CONTRACT.read_text())
        events = ledger.get("events")
        assert isinstance(events, dict), "work ledger contract has no event map"
        return cls.typescript_array_values("RUNTIME_API_EVENT_TYPES") | set(events)

    @classmethod
    def event(
        cls,
        event_type: str,
        *,
        payload: dict[str, object] | None = None,
        activity_kind: str | None = None,
    ) -> dict[str, object]:
        return {
            "sequence_no": 0,
            "event_type": event_type,
            "activity_kind": activity_kind
            or cls.PLAIN_CHAT_ACTIVITY_KINDS.get(event_type, "event"),
            **({"payload": payload} if payload is not None else {}),
        }

    @staticmethod
    def sequenced(events: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {**event, "sequence_no": index} for index, event in enumerate(events, 1)
        ]

    @classmethod
    def receipt_pair(cls, surface_id: str) -> list[dict[str, object]]:
        return [
            cls.event(
                "surface.created",
                payload={"kind": "receipt", "surface_id": surface_id},
            ),
            cls.event("receipt.emitted", payload={"surface_id": surface_id}),
        ]

    @classmethod
    def plain_events(
        cls,
        *,
        receipt_surface_id: str | None = None,
        include_reasoning: bool = False,
        close_model_call: bool = False,
    ) -> list[dict[str, object]]:
        events = [
            cls.event("run_queued"),
            cls.event("run_started"),
            cls.event("model_call_started"),
        ]
        if include_reasoning:
            events.extend(
                [
                    cls.event("reasoning_summary"),
                    cls.event("reasoning_summary_delta"),
                ]
            )
        events.append(cls.event("model_delta"))
        if close_model_call:
            events.append(cls.event("model_call_completed"))
        events.append(cls.event("final_response"))
        if receipt_surface_id is not None:
            events.extend(cls.receipt_pair(receipt_surface_id))
        events.append(cls.event("run_completed"))
        return cls.sequenced(events)

    def assert_replay_passes(
        self,
        events: list[dict[str, object]],
        surfaces: list[dict[str, object]] | None = None,
    ) -> None:
        g0._assert_facade_plain_chat(FacadeSession(events, surfaces), "run-g0")

    def test_grammar_is_pinned_to_runtime_and_ledger_contracts(self) -> None:
        runtime_and_ledger_types = self.runtime_and_ledger_event_types()
        self.assertEqual(
            g0.PLAIN_CHAT_EVENT_ACTIVITY_KINDS, self.PLAIN_CHAT_ACTIVITY_KINDS
        )
        self.assertTrue(
            set(g0.PLAIN_CHAT_EVENT_ACTIVITY_KINDS).issubset(runtime_and_ledger_types)
        )
        self.assertTrue(
            {"gate.opened.v2", "gate.resolved.v2"}.issubset(runtime_and_ledger_types)
        )

    def test_accepts_only_plain_model_lifecycle_with_optional_receipt(self) -> None:
        self.assert_replay_passes(
            self.plain_events(include_reasoning=True, close_model_call=True)
        )
        surface_id = "receipt://run-g0"
        self.assert_replay_passes(
            self.plain_events(receipt_surface_id=surface_id),
            [{"kind": "receipt", "surface_id": surface_id}],
        )

    def test_rejects_each_other_known_runtime_or_ledger_event_type(self) -> None:
        disallowed = self.runtime_and_ledger_event_types() - set(
            g0.PLAIN_CHAT_EVENT_ACTIVITY_KINDS
        )
        self.assertTrue(disallowed)
        for event_type in sorted(disallowed):
            with self.subTest(event_type=event_type):
                events = self.plain_events()
                events.insert(4, self.event(event_type))
                with self.assertRaisesRegex(AssertionError, "outside the grammar"):
                    self.assert_replay_passes(self.sequenced(events))

    def test_rejects_unknown_event_type_and_each_wrong_activity_kind(self) -> None:
        unknown = self.plain_events()
        unknown.insert(4, self.event("arbitrary.activity"))
        with self.assertRaisesRegex(AssertionError, "outside the grammar"):
            self.assert_replay_passes(self.sequenced(unknown))

        activity_kinds = self.typescript_array_values("RUNTIME_ACTIVITY_KINDS")
        for activity_kind in sorted((activity_kinds | {"arbitrary"}) - {"message"}):
            with self.subTest(activity_kind=activity_kind):
                events = self.plain_events()
                events[3]["activity_kind"] = activity_kind
                with self.assertRaisesRegex(AssertionError, "activity_kind"):
                    self.assert_replay_passes(events)

    def test_rejects_sequence_ordering_permutations(self) -> None:
        receipt_id = "receipt://run-g0"
        cases = {
            "completed before final": [
                self.event("run_queued"),
                self.event("run_started"),
                self.event("model_call_started"),
                self.event("run_completed"),
                self.event("final_response"),
            ],
            "final before model delta": [
                self.event("run_queued"),
                self.event("run_started"),
                self.event("model_call_started"),
                self.event("final_response"),
                self.event("model_delta"),
                self.event("run_completed"),
            ],
            "receipt before final": [
                self.event("run_queued"),
                self.event("run_started"),
                self.event("model_call_started"),
                *self.receipt_pair(receipt_id),
                self.event("final_response"),
                self.event("run_completed"),
            ],
            "receipt pair separated": [
                self.event("run_queued"),
                self.event("run_started"),
                self.event("model_call_started"),
                self.event("final_response"),
                self.receipt_pair(receipt_id)[0],
                self.event("model_delta"),
                self.receipt_pair(receipt_id)[1],
                self.event("run_completed"),
            ],
            "receipt after completed": [
                self.event("run_queued"),
                self.event("run_started"),
                self.event("model_call_started"),
                self.event("final_response"),
                self.event("run_completed"),
                *self.receipt_pair(receipt_id),
            ],
        }
        for name, events in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(AssertionError):
                    self.assert_replay_passes(self.sequenced(events))

    def test_rejects_non_monotonic_or_missing_sequence_numbers(self) -> None:
        cases = {
            "duplicate": [1, 2, 3, 4, 4, 6],
            "reversed": [1, 2, 3, 5, 4, 6],
        }
        for name, sequence_numbers in cases.items():
            with self.subTest(case=name):
                events = self.plain_events()
                for event, sequence_no in zip(events, sequence_numbers, strict=True):
                    event["sequence_no"] = sequence_no
                with self.assertRaisesRegex(AssertionError, "strictly ordered"):
                    self.assert_replay_passes(events)

        missing = self.plain_events()
        missing[0].pop("sequence_no")
        with self.assertRaisesRegex(AssertionError, "integer sequence_no"):
            self.assert_replay_passes(missing)

    def test_rejects_all_receipt_mismatches(self) -> None:
        receipt_a = "receipt://run-g0-a"
        receipt_b = "receipt://run-g0-b"
        surface_only = self.plain_events()
        surface_only.insert(
            -1,
            self.event(
                "surface.created",
                payload={"kind": "receipt", "surface_id": receipt_a},
            ),
        )
        emitted_only = self.plain_events()
        emitted_only.insert(
            -1, self.event("receipt.emitted", payload={"surface_id": receipt_a})
        )
        mismatched_event_pair = self.plain_events()
        mismatched_event_pair[-1:-1] = self.receipt_pair(receipt_a)
        mismatched_event_pair[-2]["payload"] = {"surface_id": receipt_b}
        duplicate_pairs = self.plain_events()
        duplicate_pairs[-1:-1] = self.receipt_pair(receipt_a) + self.receipt_pair(
            receipt_b
        )
        nonreceipt_surface = self.plain_events()
        nonreceipt_surface[-1:-1] = [
            self.event(
                "surface.created",
                payload={"kind": "table", "surface_id": "surface://table"},
            ),
            self.event("receipt.emitted", payload={"surface_id": "surface://table"}),
        ]
        persisted_pair = self.plain_events(receipt_surface_id=receipt_a)
        cases = {
            "surface event only": (surface_only, []),
            "emission event only": (emitted_only, []),
            "mismatched event ids": (mismatched_event_pair, []),
            "duplicate persisted pairs": (duplicate_pairs, []),
            "nonreceipt persisted surface": (nonreceipt_surface, []),
            "event-only receipt": (persisted_pair, []),
            "projection-only receipt": (
                self.plain_events(),
                [{"kind": "receipt", "surface_id": receipt_a}],
            ),
            "mismatched projection id": (
                persisted_pair,
                [{"kind": "receipt", "surface_id": receipt_b}],
            ),
            "duplicate projected receipts": (
                persisted_pair,
                [
                    {"kind": "receipt", "surface_id": receipt_a},
                    {"kind": "receipt", "surface_id": receipt_b},
                ],
            ),
            "nonreceipt projection": (
                persisted_pair,
                [{"kind": "table", "surface_id": receipt_a}],
            ),
        }
        for name, (events, surfaces) in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(AssertionError):
                    self.assert_replay_passes(self.sequenced(events), surfaces)

    def test_rejects_each_receipt_ui_selector(self) -> None:
        receipt_selectors = {
            "receipt launcher": "[data-testid=receipt-v2-launch]",
            "receipt surface": "[data-testid=receipt-v2-surface]",
        }
        self.assertEqual(
            {name: g0.RICH_UI_SELECTORS[name] for name in receipt_selectors},
            receipt_selectors,
        )
        for name, selector in receipt_selectors.items():
            with self.subTest(selector=selector):
                with self.assertRaisesRegex(AssertionError, name):
                    g0._assert_no_rich_ui(UiSession(selector))


if __name__ == "__main__":
    unittest.main()
