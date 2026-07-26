from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

CONNECTOR = Path(__file__).resolve().parents[1]
WORKFLOWS = CONNECTOR.parent
sys.path.insert(0, str(CONNECTOR))

from fixture_connector import FixtureError, FixtureStore  # noqa: E402

SCENARIO = WORKFLOWS / "scenarios" / "local-communications.json"
SERVER = CONNECTOR / "server.py"


class FixtureStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FixtureStore.from_path(SCENARIO)
        self.namespace = "fixture://generative-workflows/launch-week"
        self.workspace = "fixture://workspace/launch-week"

    def call(self, operation: str, **arguments: object) -> dict[str, object]:
        return self.store.call(operation, arguments)

    def assert_error(self, code: str, operation: str, **arguments: object) -> None:
        with self.assertRaises(FixtureError) as raised:
            self.call(operation, **arguments)
        self.assertEqual(raised.exception.code, code)

    def test_manifest_is_fixture_only_and_credential_free(self) -> None:
        manifest = self.call("fixture_manifest")
        self.assertEqual(manifest["namespace"], self.namespace)
        self.assertEqual(manifest["network"], "disabled")
        self.assertIs(manifest["accepts_credentials"], False)

    def test_workspace_grant_fault_is_once_then_read_retries(self) -> None:
        target = f"{self.workspace}/project-brief.md"
        self.assert_error(
            "grant_expired", "workspace_read", path="project-brief.md", target=target
        )
        read = self.call("workspace_read", path="project-brief.md", target=target)
        self.assertIn("# Launch brief", read["content"])
        self.assertEqual(read["target"], target)

    def test_rejects_remote_target_secret_and_path_escape(self) -> None:
        self.assert_error(
            "invalid_target",
            "mail_draft_reply",
            thread_id="thr_q3_renewal",
            recipient="sam@fixture.invalid",
            body="Fixture only",
            target="https://mail.example/send",
        )
        self.assert_error(
            "secret_input_rejected", "fixture_manifest", api_key="not-accepted"
        )
        self.assert_error(
            "invalid_path",
            "workspace_read",
            path="../outside.txt",
            target=f"{self.workspace}/../outside.txt",
        )

    def test_mail_is_staged_then_approved_and_idempotent(self) -> None:
        target = f"{self.namespace}/mail/threads/thr_q3_renewal"
        stage = self.call(
            "mail_draft_reply",
            thread_id="thr_q3_renewal",
            recipient="sam@fixture.invalid",
            body="The export workflow is staged in the release plan.",
            target=target,
        )
        self.assert_error(
            "approval_required",
            "mail_send_draft",
            stage_id=stage["stage_id"],
            target=target,
        )
        applied = self.call(
            "mail_send_draft", stage_id=stage["stage_id"], target=target, approved=True
        )
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(str(applied["receipt"]).startswith("fixture://"))
        replay = self.call(
            "mail_send_draft", stage_id=stage["stage_id"], target=target, approved=True
        )
        self.assertTrue(replay["idempotent"])
        thread = self.call("mail_get_thread", thread_id="thr_q3_renewal", target=target)
        self.assertEqual(len(thread["messages"]), 3)

    def test_discord_retry_is_deterministic_then_idempotent(self) -> None:
        target = f"{self.namespace}/discord/channels/chn_launch_room"
        stage = self.call(
            "discord_draft_announcement",
            channel_id="chn_launch_room",
            body="@maya and @leo: Studio approval is ready.",
            mentions=["@maya", "@leo"],
            target=target,
        )
        first = self.call(
            "discord_publish_announcement",
            stage_id=stage["stage_id"],
            target=target,
            approved=True,
        )
        self.assertEqual(first["status"], "retryable_failure")
        self.assertTrue(first["retryable"])
        second = self.call(
            "discord_publish_announcement",
            stage_id=stage["stage_id"],
            target=target,
            approved=True,
        )
        self.assertEqual(second["status"], "applied")
        third = self.call(
            "discord_publish_announcement",
            stage_id=stage["stage_id"],
            target=target,
            approved=True,
        )
        self.assertTrue(third["idempotent"])
        messages = self.call(
            "discord_get_messages", channel_id="chn_launch_room", target=target
        )
        self.assertEqual(len(messages["messages"]), 3)

    def test_timeline_draft_is_exact_target_and_only_approved_commit_publishes(
        self,
    ) -> None:
        target = f"{self.namespace}/timeline/posts/post_northstar_launch"
        stage = self.call(
            "timeline_draft_reply_post",
            post_id="post_northstar_launch",
            body="A reviewable approval step makes the walkthrough clear.",
            target=target,
        )
        before = self.call("timeline_list_posts")
        self.assertEqual(len(before["posts"]), 1)
        self.assert_error(
            "approval_required",
            "timeline_publish_draft",
            stage_id=stage["stage_id"],
            target=target,
        )
        effect = self.call(
            "timeline_publish_draft",
            stage_id=stage["stage_id"],
            target=target,
            approved=True,
        )
        self.assertEqual(effect["in_reply_to"], "post_northstar_launch")
        after = self.call("timeline_list_posts")
        self.assertEqual(len(after["posts"]), 2)

    def test_workspace_revision_is_staged_then_applied_to_memory_only(self) -> None:
        target = f"{self.workspace}/project-brief.md"
        stage = self.call(
            "workspace_write_revision",
            path="project-brief.md",
            content="# Launch brief\n\nUpdated only in the fixture.\n",
            target=target,
        )
        self.assertEqual(stage["status"], "staged")
        self.assert_error(
            "approval_required",
            "workspace_write_revision",
            stage_id=stage["stage_id"],
            target=target,
        )
        applied = self.call(
            "workspace_write_revision",
            stage_id=stage["stage_id"],
            target=target,
            approved=True,
        )
        self.assertEqual(applied["status"], "applied")
        # Consume the intentionally one-shot grant fault, then assert the value.
        self.assert_error(
            "grant_expired", "workspace_read", path="project-brief.md", target=target
        )
        current = self.call("workspace_read", path="project-brief.md", target=target)
        self.assertEqual(
            current["content"], "# Launch brief\n\nUpdated only in the fixture.\n"
        )

    def test_csv_rowset_holds_block_apply_and_partial_apply_is_exact(self) -> None:
        target = f"{self.workspace}/pipeline.csv"
        stage = self.call(
            "workspace_apply_rowset",
            path="pipeline.csv",
            row_key="account",
            changes={
                "Northstar": {"stage": "approved"},
                "Orbit": {"stage": "approved"},
            },
            holds=["Orbit"],
            target=target,
        )
        self.assert_error(
            "held_row",
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Northstar", "Orbit"],
            approved=True,
        )
        partial = self.call(
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Northstar"],
            approved=True,
        )
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["applied_rows"], ["Northstar"])
        self.assertEqual(partial["held_rows"], ["Orbit"])

    def test_csv_rowset_tracks_exact_subsets_and_never_falsely_replays(self) -> None:
        target = f"{self.workspace}/pipeline.csv"
        stage = self.call(
            "workspace_apply_rowset",
            path="pipeline.csv",
            row_key="account",
            changes={
                "Northstar": {"stage": "approved"},
                "Acme": {"stage": "review"},
                "Orbit": {"stage": "approved"},
            },
            target=target,
        )
        first = self.call(
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Northstar"],
            approved=True,
        )
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["applied_rows"], ["Northstar"])
        replay = self.call(
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Northstar"],
            approved=True,
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["receipt"], first["receipt"])
        second = self.call(
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Acme"],
            approved=True,
        )
        self.assertEqual(second["status"], "partial")
        self.assertEqual(second["applied_rows"], ["Acme"])
        self.assertNotEqual(second["receipt"], first["receipt"])
        self.assert_error(
            "rowset_subset_conflict",
            "workspace_apply_rowset",
            stage_id=stage["stage_id"],
            target=target,
            row_keys=["Northstar", "Orbit"],
            approved=True,
        )
        # A repeated exact subset has no fresh effect/audit, while the two
        # distinct successful subsets have exact, separate audit rows.
        effects = [
            entry
            for entry in self.store.audit_log()
            if entry["operation"] == "workspace.apply_rowset"
        ]
        self.assertEqual(len(effects), 2)
        self.assertEqual(effects[0]["payload"]["applied_rows"], ["Northstar"])
        self.assertEqual(effects[1]["payload"]["applied_rows"], ["Acme"])
        # Consume the one-shot grant fault and verify both rows really changed.
        self.assert_error(
            "grant_expired", "workspace_read", path="pipeline.csv", target=target
        )
        content = self.call("workspace_read", path="pipeline.csv", target=target)[
            "content"
        ]
        self.assertIn("Northstar,approved,medium", content)
        self.assertIn("Acme,review,low", content)
        self.assertIn("Orbit,review,high", content)

    def test_audit_is_hash_chained_detached_and_survives_reset(self) -> None:
        self.call("mail_list_threads")
        before = self.store.audit_log()
        self.assertTrue(self.store.verify_audit())
        before_list = list(before)
        before_list[0]["operation"] = "tampered-copy"
        self.assertTrue(self.store.verify_audit())
        reset = self.call("fixture_reset")
        self.assertEqual(reset["generation"], 1)
        after = self.store.audit_log()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[-1]["operation"], "fixture.reset")
        self.assertTrue(self.store.verify_audit())

    def test_unknown_scenario_operation_is_honest(self) -> None:
        tool_name_with_secret = "calendar_archive__sk_fixture_do_not_retain"
        self.assert_error("unknown_operation", tool_name_with_secret)
        audit = self.call("fixture_audit")
        self.assertTrue(audit["valid"])
        self.assertEqual(audit["entries"][-1]["operation"], "fixture.unknown_operation")
        self.assertEqual(
            audit["entries"][-1]["payload"], {"category": "unrecognized_tool"}
        )
        self.assertNotIn(tool_name_with_secret, json.dumps(audit, sort_keys=True))


class McpStdioTests(unittest.TestCase):
    def test_server_supports_initialize_list_call_and_safe_errors(self) -> None:
        transcript = (
            "\n".join(
                [
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                            "params": {},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "fixture_manifest", "arguments": {}},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "tools/call",
                            "params": {
                                "name": "fixture_manifest",
                                "arguments": {"token": "never"},
                            },
                        }
                    ),
                ]
            )
            + "\n"
        )
        process = subprocess.run(
            [sys.executable, str(SERVER)],
            input=transcript,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            cwd=str(CONNECTOR),
        )
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(
            responses[0]["result"]["serverInfo"]["name"],
            "generative-workflows-local-fixture",
        )
        self.assertIn(
            "workspace_apply_rowset",
            [tool["name"] for tool in responses[1]["result"]["tools"]],
        )
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(responses[3]["result"]["isError"])
        self.assertIn(
            "secret_input_rejected", responses[3]["result"]["content"][0]["text"]
        )

    def test_unknown_mcp_tool_name_never_reaches_error_or_audit(self) -> None:
        unsafe_name = "unknown__sk_fixture_not_for_retention"
        transcript = (
            "\n".join(
                [
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": unsafe_name, "arguments": {}},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "fixture_audit", "arguments": {}},
                        }
                    ),
                ]
            )
            + "\n"
        )
        process = subprocess.run(
            [sys.executable, str(SERVER)],
            input=transcript,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            cwd=str(CONNECTOR),
        )
        self.assertNotIn(unsafe_name, process.stdout)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertIn("unknown_operation", responses[0]["result"]["content"][0]["text"])
        audit_text = responses[1]["result"]["content"][0]["text"]
        self.assertNotIn(unsafe_name, audit_text)
        audit = json.loads(audit_text)
        self.assertEqual(
            audit["entries"][0]["payload"], {"category": "unrecognized_tool"}
        )

    def test_connector_modules_have_no_network_client_imports(self) -> None:
        banned = {
            "socket",
            "requests",
            "urllib.request",
            "http.client",
            "aiohttp",
            "httpx",
        }
        for path in (CONNECTOR / "fixture_connector.py", CONNECTOR / "server.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            self.assertTrue(
                imports.isdisjoint(banned),
                f"network import in {path.name}: {imports & banned}",
            )


if __name__ == "__main__":
    unittest.main()
