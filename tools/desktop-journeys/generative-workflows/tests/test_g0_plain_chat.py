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

    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    def transport(self, method: str, path: str) -> dict[str, object]:
        self.assert_request(method, path)
        if path.endswith("/events"):
            return {"run_status": "completed", "events": self.events}
        return {"run_id": "run-g0", "surfaces": []}

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

    def test_rejects_each_v2_tool_execution_event(self) -> None:
        self.assertEqual(
            g0.TOOL_EXECUTION_V2_EVENT_TYPES, self.V2_TOOL_EXECUTION_EVENTS
        )
        for event_type in self.V2_TOOL_EXECUTION_EVENTS:
            with self.subTest(event_type=event_type):
                session = FacadeSession(self.completed_events(event_type))
                with self.assertRaisesRegex(AssertionError, "v2 tool execution events"):
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
