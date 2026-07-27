from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKFLOWS = Path(__file__).resolve().parents[1]
RUNNER = WORKFLOWS / "g2_csv_lifecycle.py"

G2_SPEC = importlib.util.spec_from_file_location("g2_csv_lifecycle", RUNNER)
assert G2_SPEC is not None and G2_SPEC.loader is not None
g2 = importlib.util.module_from_spec(G2_SPEC)
sys.modules[G2_SPEC.name] = g2
G2_SPEC.loader.exec_module(g2)


def stage_event(revision: int = 1, digest: str = "a" * 64) -> dict[str, object]:
    return {
        "event_type": "effect.staged" if revision == 1 else "effect.revised",
        "payload": {
            "stage_id": "stg_g2_001",
            "executor": "workspace",
            "revision": revision,
            "proposal_digest": digest,
            "target_digest": "b" * 64,
            "proposal_content_ref": (
                f"artifact://artifact_forecast/revisions/{revision + 1}"
            ),
            "display_target": "/workspace/forecast.csv",
            "target_ref": "workspace-target://grant_opaque/path_opaque",
        },
    }


def derived_csv() -> bytes:
    return (
        b"month,region,bookings,forecast,variance\n"
        b"2026-08,North,126,132,6\n"
        b"2026-09,South,98,114,16\n"
        b"2026-10,West,145,151,6\n"
    )


class UiSession:
    def __init__(self, selectors: set[str], *, path: str = "forecast.csv") -> None:
        self.selectors = selectors
        self.path = path
        self.clicked: list[str] = []

    def present(self, selector: str) -> bool:
        return selector in self.selectors

    def wait_for(self, selector: str, timeout_s: int = 60) -> bool:
        del timeout_s
        return self.present(selector)

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def evaluate(self, javascript: str) -> str:
        if "tc-workspace-stage-path" in javascript:
            return self.path
        if "tc-workspace-stage-revision" in javascript:
            return "rev 2"
        if "sources-v2-tab" in javascript:
            return "Artifact\nWorkspace activity"
        return ""


class G2CsvLifecycleTests(unittest.TestCase):
    def test_preflight_skips_only_missing_staged_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.object(g2, "_copilot_home", return_value=Path(root)),
                patch.dict(
                    os.environ,
                    {"COPILOT_DESKTOP_TEST_TARGET": "installed-payload"},
                    clear=True,
                ),
                self.assertRaisesRegex(g2.PreflightSkip, "staged runtime"),
            ):
                g2._preflight_packaged_supervisor()

    def test_preflight_rejects_external_facade_and_non_packaged_target(self) -> None:
        with patch.dict(
            os.environ,
            {"COPILOT_DESKTOP_TEST_TARGET": "source"},
            clear=True,
        ):
            with self.assertRaisesRegex(AssertionError, "installed-payload"):
                g2._preflight_packaged_supervisor()
        with patch.dict(
            os.environ,
            {
                "COPILOT_DESKTOP_TEST_TARGET": "installed-payload",
                "COPILOT_FACADE_URL": "http://127.0.0.1:8200",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(AssertionError, "COPILOT_FACADE_URL"):
                g2._preflight_packaged_supervisor()

    def test_missing_byok_is_structured_prerequisite_skip_not_pass(self) -> None:
        with patch.object(g2, "load_env_key", side_effect=SystemExit):
            with self.assertRaisesRegex(g2.PreflightSkip, "BYOK"):
                g2._byok_provider()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(g2._skip("staged runtime is absent"), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["journey"], "G2")
        self.assertEqual(result["outcome"], "skipped")
        self.assertIn("staged runtime", result["reason"])
        self.assertNotEqual(result["outcome"], "passed")

    def test_fixture_is_isolated_empty_and_cleaned_without_runner_host_write(
        self,
    ) -> None:
        with g2.FixtureWorkspace() as fixture:
            root = fixture.root
            self.assertIsNotNone(root)
            self.assertFalse(fixture.forecast_path.exists())
            self.assertEqual(list(root.iterdir()) if root else [], [])
        assert root is not None
        self.assertFalse(root.exists())
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with g2.FixtureWorkspace() as failed:
                failed_root = failed.root
                raise RuntimeError("fixture failure")
        self.assertFalse(failed_root.exists())

        tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
        direct_writes = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"write_bytes", "write_text", "touch", "unlink"}
        ]
        self.assertEqual(direct_writes, [])
        self.assertNotIn("MockTransport", RUNNER.read_text(encoding="utf-8"))

    def test_native_dialog_commands_are_argument_safe(self) -> None:
        fixture_root = Path("/private/tmp/g2 fixture; no shell")
        picker = g2._folder_picker_command(fixture_root, 4242)
        approval = g2._approval_command(4242)
        self.assertEqual(picker[:2], ["/usr/bin/osascript", "-e"])
        self.assertEqual(picker[3], "--")
        self.assertEqual(picker[4], str(fixture_root))
        self.assertNotIn(str(fixture_root), picker[2])
        self.assertNotIn("sh -c", " ".join(picker))
        self.assertEqual(approval[3:], ["--", "4242"])
        self.assertIn('button "Approve"', approval[2])

    def test_journey_environment_removes_plaintext_provider_key(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "not-printed", "SENTINEL": "kept"},
            clear=True,
        ):
            with g2._journey_environment():
                self.assertEqual(os.environ["WORKSPACE_EFFECT_MODE"], "enforce")
                self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "not-printed")
            self.assertEqual(os.environ["SENTINEL"], "kept")

    def test_csv_semantics_require_structured_user_edit_and_derived_column(
        self,
    ) -> None:
        initial = (
            b"month,region,bookings,forecast\n"
            b"2026-08,North,126,132\n"
            b"2026-09,South,98,114\n"
            b"2026-10,West,145,151\n"
        )
        g2._assert_initial_csv_semantics(initial)
        g2._assert_derived_csv_semantics(derived_csv())
        with self.assertRaisesRegex(AssertionError, "duplicate headers"):
            g2._parse_csv_bytes(b"month,month\n2026-08,2026-08\n")
        with self.assertRaisesRegex(AssertionError, "derived variance"):
            g2._assert_derived_csv_semantics(
                derived_csv().replace(b",132,6", b",132,9")
            )

    def test_stage_revisions_are_digest_pinned_and_use_only_workspace_authority(
        self,
    ) -> None:
        first = stage_event()
        revised = stage_event(
            revision=2, digest=hashlib.sha256(derived_csv()).hexdigest()
        )
        stage = g2._single_workspace_stage([first, revised])
        self.assertEqual(stage.stage_id, "stg_g2_001")
        self.assertEqual(stage.revision, 2)
        self.assertEqual(
            g2._artifact_from_content_ref(stage.proposal_content_ref).revision, 3
        )
        with g2.FixtureWorkspace() as fixture:
            g2._assert_stage_uses_workspace_authority(stage, fixture)
            escaped = g2.WorkspaceStage(
                **{**stage.__dict__, "target_ref": "https://unsafe.example/write"}
            )
            with self.assertRaisesRegex(AssertionError, "opaque workspace"):
                g2._assert_stage_uses_workspace_authority(escaped, fixture)

    def test_review_selectors_cover_dataset_stage_diff_receipt_and_sources(
        self,
    ) -> None:
        selectors = {
            "[data-testid=artifact-frame]",
            "[data-testid=artifact-dataset-renderer]",
            '[role=grid][aria-label="Dataset cell editor"]',
            '[aria-label="bookings, row 2"]',
            '[aria-label="Dataset revision actions"]',
            "[data-testid=artifact-revision-history]",
            "[data-testid=tc-workspace-stage]",
            "[data-testid=tc-workspace-stage-path]",
            "[data-testid=tc-workspace-stage-revision]",
            "[data-testid=tc-workspace-stage-preview-csv]",
            "[data-testid=tc-workspace-stage-diff-csv]",
            "[data-testid=tc-workspace-stage-edit]",
            "[data-testid=receipt-v2-launch]",
            "[data-testid=receipt-v2-surface]",
            "[data-testid=sources-v2-tab]",
        }
        ui = UiSession(selectors)
        stage = g2._single_workspace_stage([stage_event(), stage_event(revision=2)])
        g2._assert_dataset_surface(ui)
        g2._assert_workspace_stage_surface(ui, stage)
        g2._assert_receipt_and_sources(ui)
        self.assertIn("[data-testid=receipt-v2-open]", ui.clicked)
        self.assertIn('[role=tab]:has-text("Sources")', ui.clicked)

    def test_no_bypass_guards_require_reload_and_reject_preapproval_effect(
        self,
    ) -> None:
        edited = g2.ArtifactReference(
            artifact_id="artifact_forecast",
            revision=2,
            kind="dataset",
            content_ref="artifact://artifact_forecast/revisions/2",
        )
        reads = [
            {
                "event_type": "read.executed",
                "payload": {
                    "artifact_id": edited.artifact_id,
                    "revision": edited.revision,
                    "capability": "artifact",
                },
            }
        ]
        g2._assert_agent_reloaded_dataset(reads, edited)
        g2._assert_only_workspace_or_artifact_tools(reads)
        with self.assertRaisesRegex(AssertionError, "before explicit approval"):
            g2._assert_no_workspace_apply(
                [
                    {
                        "event_type": "write.applied",
                        "payload": {"executor": "workspace"},
                    }
                ]
            )
        with self.assertRaisesRegex(AssertionError, "before explicit approval"):
            g2._assert_no_workspace_apply(
                [{"event_type": "effect.applied", "payload": {}}]
            )
        with self.assertRaisesRegex(AssertionError, "unrelated"):
            g2._assert_only_workspace_or_artifact_tools(
                [{"event_type": "tool_result", "payload": {"tool": "web_search"}}]
            )
        with g2.FixtureWorkspace() as fixture:
            assert fixture.root is not None
            leaked = [
                {
                    "event_type": "effect.staged",
                    "payload": {"display_target": str(fixture.root / "forecast.csv")},
                }
            ]
            with self.assertRaisesRegex(AssertionError, "physical host path"):
                g2._assert_no_fixture_root_leak(leaked, fixture)

    def test_approval_requires_exact_stage_revision_and_exact_bytes(self) -> None:
        content = derived_csv()
        digest = hashlib.sha256(content).hexdigest()
        stage = g2._single_workspace_stage(
            [stage_event(), stage_event(revision=2, digest=digest)]
        )
        exact = [
            {
                "event_type": "effect.decision_recorded",
                "payload": {
                    "stage_id": stage.stage_id,
                    "decision": "approve",
                    "revision": stage.revision,
                    "proposal_digest": stage.proposal_digest,
                    "target_digest": stage.target_digest,
                },
            },
            {
                "event_type": "effect.applied",
                "payload": {
                    "stage_id": stage.stage_id,
                    "revision": stage.revision,
                    "outcome": "applied",
                },
            },
        ]
        g2._assert_approved_and_applied(exact, stage)
        wrong = [
            {
                **exact[0],
                "payload": {**exact[0]["payload"], "revision": 1},
            },
            exact[1],
        ]
        with self.assertRaises(AssertionError):
            g2._assert_approved_and_applied(wrong, stage)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "forecast.csv"
            path.write_bytes(content)
            g2._assert_exact_file_bytes(path, content)
            with self.assertRaisesRegex(AssertionError, "bytes differ"):
                g2._assert_exact_file_bytes(path, content + b"\n")

    def test_plaintext_secret_guard_never_echoes_value(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "driver.log"
            path.write_text("safe", encoding="utf-8")
            g2._assert_no_plaintext_secret("secret-not-printed", (Path(root),))
            path.write_text("secret-not-printed", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "plaintext BYOK") as raised:
                g2._assert_no_plaintext_secret("secret-not-printed", (Path(root),))
            self.assertNotIn("secret-not-printed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
