from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1]
G0_PATH = WORKFLOWS / "g0_plain_chat.py"
G0_SPEC = importlib.util.spec_from_file_location("g0_plain_chat", G0_PATH)
assert G0_SPEC is not None and G0_SPEC.loader is not None
g0 = importlib.util.module_from_spec(G0_SPEC)
G0_SPEC.loader.exec_module(g0)


class FacadeSession:
    """Minimal authenticated-facade fixture for the persisted-event guard."""

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


class G0PlainChatGuardTests(unittest.TestCase):
    SUBAGENT_TASK_EXECUTION_EVENTS = frozenset(
        {
            "subagent_update",
            "subagent_started",
            "subagent_progress",
            "subagent_completed",
            "subagent_fleet_started",
            "subagent_fleet_finished",
            "subagent_paused",
            "subagent_resumed",
        }
    )
    V2_TOOL_EXECUTION_EVENTS = frozenset(
        {
            "action.classified",
            "read.executed",
            "gate.opened",
            "gate.resolved",
        }
    )

    @staticmethod
    def completed_events(event_type: str | None = None) -> list[dict[str, object]]:
        events: list[dict[str, object]] = [{"event_type": "final_response"}]
        if event_type is not None:
            events.append({"event_type": event_type})
        return events

    @staticmethod
    def receipt_event_pair(surface_id: str) -> list[dict[str, object]]:
        return [
            {
                "event_type": "surface.created",
                "payload": {"kind": "receipt", "surface_id": surface_id},
            },
            {"event_type": "receipt.emitted", "payload": {"surface_id": surface_id}},
        ]

    def test_rejects_each_v2_tool_execution_event(self) -> None:
        self.assertEqual(
            g0.TOOL_EXECUTION_V2_EVENT_TYPES, self.V2_TOOL_EXECUTION_EVENTS
        )
        for event_type in self.V2_TOOL_EXECUTION_EVENTS:
            with self.subTest(event_type=event_type):
                session = FacadeSession(self.completed_events(event_type))
                with self.assertRaisesRegex(AssertionError, "v2 tool execution events"):
                    g0._assert_facade_plain_chat(session, "run-g0")

    def test_rejects_each_subagent_task_execution_event(self) -> None:
        self.assertEqual(
            g0.SUBAGENT_TASK_EXECUTION_EVENT_TYPES,
            self.SUBAGENT_TASK_EXECUTION_EVENTS,
        )
        for event_type in self.SUBAGENT_TASK_EXECUTION_EVENTS:
            with self.subTest(event_type=event_type):
                session = FacadeSession(self.completed_events(event_type))
                with self.assertRaisesRegex(
                    AssertionError, "subagent task execution events"
                ):
                    g0._assert_facade_plain_chat(session, "run-g0")

    def test_rejects_subagent_activity_projection(self) -> None:
        session = FacadeSession(
            [
                {"event_type": "final_response"},
                {"event_type": "progress", "activity_kind": "subagent"},
            ]
        )
        with self.assertRaisesRegex(AssertionError, "subagent task activity"):
            g0._assert_facade_plain_chat(session, "run-g0")

    def test_allows_non_tool_model_and_run_events(self) -> None:
        session = FacadeSession(
            [
                {"event_type": "run_started"},
                {"event_type": "model_call_started"},
                {"event_type": "model_delta"},
                {"event_type": "model_call_completed"},
                {"event_type": "final_response"},
                {"event_type": "run_completed"},
            ]
        )
        g0._assert_facade_plain_chat(session, "run-g0")

    def test_allows_one_matching_terminal_receipt(self) -> None:
        surface_id = "receipt://run-g0"
        events = self.completed_events()
        events.extend(self.receipt_event_pair(surface_id))
        g0._assert_facade_plain_chat(
            FacadeSession(events, [{"kind": "receipt", "surface_id": surface_id}]),
            "run-g0",
        )

    def test_rejects_duplicate_terminal_receipt_event_pairs(self) -> None:
        events = self.completed_events()
        events.extend(self.receipt_event_pair("receipt://run-g0-a"))
        events.extend(self.receipt_event_pair("receipt://run-g0-b"))
        with self.assertRaisesRegex(AssertionError, "more than one terminal receipt"):
            g0._assert_facade_plain_chat(FacadeSession(events), "run-g0")

    def test_rejects_duplicate_projected_terminal_receipts(self) -> None:
        session = FacadeSession(
            self.completed_events(),
            [
                {"kind": "receipt", "surface_id": "receipt://run-g0-a"},
                {"kind": "receipt", "surface_id": "receipt://run-g0-b"},
            ],
        )
        with self.assertRaisesRegex(AssertionError, "more than one terminal receipt"):
            g0._assert_facade_plain_chat(session, "run-g0")

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
