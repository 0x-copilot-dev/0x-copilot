"""Unit tests for the v1→v2 notification preferences backfill mapper.

The Postgres-bound script is exercised via the in-process translator
helper so tests don't need a real database. Coverage matches the
mapping table in the script docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The script lives outside the standard pytest collection path; add it
# to sys.path so the translator helper imports cleanly.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from backfill_notification_preferences import translate_v1_blob  # noqa: E402


def test_translate_full_v1_matrix() -> None:
    blob = {
        "notifications": {
            "matrix": {
                "mention": {"email": True, "slack": True, "desktop": False},
                "approval_needed": {"email": True, "desktop": True},
                "run_finished": {"email": False, "desktop": True},
                "weekly_digest": {"email": True, "desktop": False},
            }
        }
    }
    rows = sorted(translate_v1_blob(blob))
    assert rows == sorted(
        [
            ("mention", "email", True),
            ("mention", "in_app", False),
            ("approval_requested", "email", True),
            ("approval_requested", "in_app", True),
            ("long_task_finished", "email", False),
            ("long_task_finished", "in_app", True),
            ("weekly_digest", "email", True),
            ("weekly_digest", "in_app", False),
        ]
    )


def test_translate_drops_slack_channel() -> None:
    """slack has no v2 equivalent; backfill MUST silently drop the
    cell rather than write a row the dispatcher will never read."""

    blob = {"notifications": {"matrix": {"mention": {"slack": True}}}}
    assert list(translate_v1_blob(blob)) == []


def test_translate_drops_unknown_event() -> None:
    blob = {
        "notifications": {
            "matrix": {
                "made_up_event": {"email": True},
            }
        }
    }
    assert list(translate_v1_blob(blob)) == []


def test_translate_handles_empty_or_malformed_blob() -> None:
    assert list(translate_v1_blob(None)) == []
    assert list(translate_v1_blob({})) == []
    assert list(translate_v1_blob({"notifications": "not-a-dict"})) == []
    assert list(translate_v1_blob({"notifications": {"matrix": "not-a-dict"}})) == []
    # A non-bool ``enabled`` value drops cleanly without raising.
    assert (
        list(
            translate_v1_blob(
                {"notifications": {"matrix": {"mention": {"email": "yes"}}}}
            )
        )
        == []
    )
