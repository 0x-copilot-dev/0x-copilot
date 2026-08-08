from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


WORKFLOWS = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKFLOWS.parents[2]
sys.path.insert(0, str(WORKFLOWS.parent))

from _lib import (  # noqa: E402
    EXIT_SKIPPED,
    host_runtime_key,
    staged_runtime_dir,
)

G1_PATH = WORKFLOWS / "g1_markdown_lifecycle.py"
G1_SPEC = importlib.util.spec_from_file_location("g1_markdown_lifecycle", G1_PATH)
assert G1_SPEC is not None and G1_SPEC.loader is not None
g1 = importlib.util.module_from_spec(G1_SPEC)
sys.modules[G1_SPEC.name] = g1
G1_SPEC.loader.exec_module(g1)


def _inventoried_patterns(operator: str) -> tuple[str, ...]:
    """The `^=` (or `$=`) testid patterns the journey's inventory declares."""

    marker = f"[data-testid{operator}="
    return tuple(
        claim.selector[len(marker) : -1]
        for claim in g1.PAINTED_SELECTORS
        if claim.selector.startswith(marker) and claim.selector.endswith("]")
    )


def workspace_stage_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "artifact.created",
            "payload": {
                "artifact_id": "art_brief",
                "revision": 4,
                "kind": "document",
                "content_ref": "artifact://art_brief/revisions/4",
                "content_digest": "a" * 64,
            },
        },
        {
            "event_type": "read.executed",
            "payload": {
                "capability": "artifact",
                "artifact_id": "art_brief",
                "revision": 4,
                "content_ref": "artifact://art_brief/revisions/4",
                "content_digest": "a" * 64,
            },
        },
        {
            "event_type": "effect.staged",
            "payload": {
                "executor": "workspace",
                "stage_id": "stage_brief",
                "proposal_digest": "a" * 64,
                "target_digest": "b" * 64,
                "proposal_content_ref": "artifact://art_brief/revisions/4",
                "display_target": "/workspace/fixture/brief.md",
                "capability": "workspace",
            },
        },
    ]


class UiSession:
    """A fake that can only report selectors the product is claimed to paint.

    The previous version answered ``selector != self.missing`` — `True` for
    every selector it had not been explicitly told to hide. So this suite stayed
    green over `[data-testid=artifact-editor]`, `#artifact-editor-text` and
    `Save new revision` for as long as the raw-markdown textarea has been
    deleted: the guards asked, the fake said yes, and nothing in the loop had
    ever seen the product.

    Presence is answered from `g1.PAINTED_SELECTORS`, whose entries are checked
    against the real renderer sources by
    `test_every_driven_selector_is_painted_by_product_source`. A selector that
    is not in that inventory reads as ABSENT and is recorded in `unknown`, so a
    guard reaching for one fails twice: on its own assertion, and on the
    `unknown` check every test that uses this fake makes.
    """

    def __init__(self, missing: str | None = None) -> None:
        self.missing = missing
        self.clicked: list[str] = []
        self.unknown: list[str] = []
        # A testid the product BUILDS (`doc-block-3`, `doc-cell-1-0-2-input`)
        # has no literal to inventory, so the inventory pins its pattern and a
        # concrete id is accepted only when an inventoried pattern covers it.
        self.prefixes = _inventoried_patterns("^")
        self.suffixes = _inventoried_patterns("$")
        # The inventory is the WHOLE claim: there is no second, unverified list
        # of "selectors we are fairly sure exist" beside it. Anything a guard
        # reaches for has to be declared there and found in product source.
        self.painted = {claim.selector for claim in g1.PAINTED_SELECTORS}

    def _visible(self, selector: str) -> bool:
        if selector == self.missing:
            return False
        if selector in self.painted or self._matches_pattern(selector):
            return True
        self.unknown.append(selector)
        return False

    def _matches_pattern(self, selector: str) -> bool:
        match = re.fullmatch(r'\[data-testid="(.+)"\]', selector)
        if match is None:
            return False
        value = match.group(1)
        return value.startswith(self.prefixes) or value.endswith(self.suffixes)

    def present(self, selector: str) -> bool:
        return self._visible(selector)

    def wait_for(self, selector: str, timeout_s: int = 60) -> bool:  # noqa: ARG002
        return self._visible(selector)

    def wait_visible(self, selector: str, timeout_s: int = 30) -> bool:  # noqa: ARG002
        return self._visible(selector)

    def click(self, selector: str) -> None:
        self._visible(selector)
        self.clicked.append(selector)

    def evaluate(self, javascript: str) -> str:
        if "stage-path" in javascript:
            return "/workspace/fixture/brief.md"
        if "stage-revision" in javascript:
            return "rev 2 · Agent"
        if "receipt-v2-status" in javascript:
            return "Completed"
        if "receipt-v2-metric" in javascript:
            return json.dumps(
                [
                    {
                        "label": "Effects",
                        "value": "1 proposed · 1 approved · 1 applied · 0 rejected",
                    }
                ]
            )
        if "sources-v2-row" in javascript:
            return json.dumps(["Artifact", "Workspace activity"])
        return ""


class EditorUiSession(UiSession):
    """A document whose blocks answer the way the in-place editor's do.

    It holds `{testid: value}` and nothing else — no rendering, no splicing, no
    opinion about what an edit means. That is deliberate: what this fake is for
    is proving WHICH selectors the gesture touches and that the planner's
    skip rules run against a real document string. The splice itself is
    asserted by `_expected_spliced_document` over real text, and the selectors
    are checked against real product source; neither is delegated to a mock.
    """

    def __init__(self, spans: dict[str, str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.spans = dict(spans)
        self.open_field: str | None = None
        self.modified: set[str] = set()
        self.filled: list[tuple[str, str]] = []
        self.pressed: list[tuple[str, str]] = []

    def click(self, selector: str) -> None:
        super().click(selector)
        match = re.fullmatch(r'\[data-testid="(.+)"\]', selector)
        if match is not None and match.group(1) in self.spans:
            self.open_field = match.group(1)

    def fill(self, selector: str, value: str) -> None:
        self._visible(selector)
        self.filled.append((selector, value))
        field = re.fullmatch(r'\[data-testid="(.+)-input"\]', selector)
        assert field is not None, f"fill drove a non-field selector: {selector}"
        self.spans[field.group(1)] = value
        self.modified.add(field.group(1))

    def press(self, selector: str, key: str) -> None:
        self._visible(selector)
        self.pressed.append((selector, key))
        if key == "Enter":
            self.open_field = None

    def present(self, selector: str) -> bool:
        # Inventory first, so an uninventoried field id is still RECORDED and
        # not merely reported closed. A field only exists while its block is
        # open, which is what makes the gesture reopen one before typing.
        inventoried = super().present(selector)
        field = re.fullmatch(r'\[data-testid="(.+)-input"\]', selector)
        if field is not None and field.group(1) != self.open_field:
            return False
        return inventoried

    def wait_visible(self, selector: str, timeout_s: int = 30) -> bool:  # noqa: ARG002
        return self.present(selector)

    def evaluate(self, javascript: str) -> object:
        if "data-testid^=" in javascript:
            return json.dumps(list(self.spans))
        value = re.search(r'\[data-testid=\\?"(.+?)-input\\?"\]', javascript)
        if value is not None and ".value" in javascript:
            return self.spans[value.group(1)]
        modified = re.search(r'\[data-testid=\\?"(.+?)\\?"\]', javascript)
        if modified is not None and "data-modified" in javascript:
            return "true" if modified.group(1) in self.modified else "false"
        if g1.EDITOR_STATUS in javascript:
            count = len(self.modified)
            return f"{count} unsaved {'edit' if count == 1 else 'edits'}"
        return super().evaluate(javascript)


class G1MarkdownLifecycleTests(unittest.TestCase):
    def staged_runtime(self, root: Path) -> Path:
        home = root / "resources"
        runtime = home / "runtime" / host_runtime_key()
        (runtime / "python" / "bin").mkdir(parents=True)
        (runtime / "postgres" / "bin").mkdir(parents=True)
        (runtime / "services" / "backend").mkdir(parents=True)
        (runtime / "services" / "ai-backend").mkdir(parents=True)
        (runtime / "services" / "backend-facade").mkdir(parents=True)
        (runtime / "python" / "bin" / "python3.13").write_text("")
        (runtime / "postgres" / "bin" / "postgres").write_text("")
        (runtime / "staging-manifest.json").write_text(json.dumps({"host_exec": True}))
        return home

    def test_preflight_skips_only_missing_staged_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = self.staged_runtime(root)
            # Drive the real COPILOT_HOME resolution rather than mocking it out:
            # the point of the check is WHICH staged runtime preflight inspects.
            with patch.dict(
                os.environ,
                {
                    "COPILOT_DESKTOP_TEST_TARGET": g1.INSTALLED_PAYLOAD_TARGET,
                    "COPILOT_HOME": str(home),
                },
                clear=True,
            ):
                g1._preflight_packaged_supervisor()
                manifest = (
                    home / "runtime" / host_runtime_key() / "staging-manifest.json"
                )
                manifest.unlink()
                with self.assertRaisesRegex(g1.PreflightSkip, "staged runtime"):
                    g1._preflight_packaged_supervisor()

    def test_preflight_defaults_to_the_installed_payload_home(self) -> None:
        """G1 launches the installed payload, so it must gate on ~/.0xcopilot.

        Falling back to the checkout's stage would let an "installed" journey
        pass against source-tree services that are not the shipped artifact.
        """
        with patch.dict(
            os.environ,
            {"COPILOT_DESKTOP_TEST_TARGET": g1.INSTALLED_PAYLOAD_TARGET},
            clear=True,
        ):
            resolved = staged_runtime_dir(target=g1.INSTALLED_PAYLOAD_TARGET)
        self.assertEqual(resolved.parents[1], Path.home() / ".0xcopilot")

    def test_preflight_rejects_non_packaged_target_or_external_facade(self) -> None:
        with patch.dict(
            os.environ,
            {
                "COPILOT_DESKTOP_TEST_TARGET": "source",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(AssertionError, "installed-payload"):
                g1._preflight_packaged_supervisor()
        with patch.dict(
            os.environ,
            {
                "COPILOT_DESKTOP_TEST_TARGET": g1.INSTALLED_PAYLOAD_TARGET,
                "COPILOT_FACADE_URL": "http://127.0.0.1:8200",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(AssertionError, "COPILOT_FACADE_URL"):
                g1._preflight_packaged_supervisor()

    def test_missing_byok_is_a_structured_prerequisite_skip_not_a_pass(self) -> None:
        with (
            patch.dict(os.environ, {"G1_PROVIDER": "openai"}, clear=True),
            patch.object(g1, "load_env_key", side_effect=SystemExit("missing")),
        ):
            with self.assertRaisesRegex(g1.PreflightSkip, "BYOK"):
                g1._byok_provider()

    def test_manual_prerequisite_skip_is_structured_and_never_reports_a_pass(
        self,
    ) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("builtins.print") as printed:
            code = g1._prerequisite_result("staged runtime is absent")
        # A skip exits non-zero: `python3 g1_....py && echo ok` must not print
        # ok for a journey that never ran.
        self.assertEqual(code, EXIT_SKIPPED)
        self.assertNotEqual(code, 0)
        result = json.loads(printed.call_args.args[0])
        self.assertEqual(result["journey"], "G1")
        self.assertEqual(result["outcome"], "skipped")
        self.assertIn("staged runtime", result["reason"])
        self.assertNotEqual(result["outcome"], "passed")

    def test_release_prerequisite_failure_is_nonzero_and_structured(self) -> None:
        with (
            patch.dict(os.environ, {"CI": "true"}, clear=True),
            patch("builtins.print") as printed,
        ):
            self.assertEqual(g1._prerequisite_result("staged runtime is absent"), 2)
        result = json.loads(printed.call_args.args[0])
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("unmet prerequisite", result["reason"])
        self.assertIn("staged runtime", result["reason"])

    def test_main_fails_release_runner_when_preflight_is_unmet(self) -> None:
        with (
            patch.dict(os.environ, {"CI": "1"}, clear=True),
            patch.object(
                g1,
                "_preflight_packaged_supervisor",
                side_effect=g1.PreflightSkip("runtime is absent"),
            ),
            patch("builtins.print") as printed,
        ):
            self.assertEqual(g1.main(), 2)
        self.assertEqual(json.loads(printed.call_args.args[0])["outcome"], "failed")

    def test_fixture_workspace_is_empty_and_removed_after_failure_or_success(
        self,
    ) -> None:
        with g1.FixtureWorkspace() as fixture:
            root = fixture.root
            self.assertIsNotNone(root)
            self.assertFalse(fixture.brief_path.exists())
            fixture.brief_path.write_bytes(b"temporary only")
            self.assertTrue(fixture.brief_path.exists())
        assert root is not None
        self.assertFalse(root.exists())

        failed_root: Path | None = None
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with g1.FixtureWorkspace() as fixture:
                failed_root = fixture.root
                raise RuntimeError("fixture failure")
        assert failed_root is not None
        self.assertFalse(failed_root.exists())

    def test_native_dialog_commands_keep_fixture_path_out_of_script_and_shell(
        self,
    ) -> None:
        fixture_root = Path("/private/tmp/G1 fixture; never a shell fragment")
        picker = g1._folder_picker_command(fixture_root)
        approval = g1._approval_command()

        self.assertEqual(picker[:2], ["/usr/bin/osascript", "-e"])
        self.assertEqual(picker[3], "--")
        self.assertEqual(picker[4], str(fixture_root))
        self.assertNotIn(str(fixture_root), picker[2])
        self.assertNotIn("sh -c", " ".join(picker))
        self.assertEqual(approval[:2], ["/usr/bin/osascript", "-e"])
        self.assertEqual(approval[3:], ["--", g1.APP_PROCESS_NAME])
        self.assertIn('button "Approve"', approval[2])

    def test_journey_environment_enables_workspace_without_exporting_provider_keys(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "not-printed",
                "COPILOT_DEV": "1",
                "COPILOT_AUTH_MODE": "dev-mint",
                "COPILOT_DEV_PERSONA": "sarah_acme",
                "SENTINEL": "kept",
            },
            clear=True,
        ):
            with g1._journey_environment():
                self.assertEqual(os.environ["RUNTIME_ENABLE_DESKTOP_FILESYSTEM"], "1")
                self.assertEqual(os.environ["WORKSPACE_EFFECT_MODE"], "enforce")
                self.assertEqual(os.environ["COPILOT_PRODUCTION"], "1")
                self.assertNotIn("OPENAI_API_KEY", os.environ)
                self.assertNotIn("COPILOT_DEV", os.environ)
                self.assertNotIn("COPILOT_AUTH_MODE", os.environ)
                self.assertNotIn("COPILOT_DEV_PERSONA", os.environ)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "not-printed")
            self.assertEqual(os.environ["COPILOT_DEV"], "1")
            self.assertEqual(os.environ["COPILOT_AUTH_MODE"], "dev-mint")
            self.assertEqual(os.environ["COPILOT_DEV_PERSONA"], "sarah_acme")
            self.assertEqual(os.environ["SENTINEL"], "kept")

    def test_event_guards_require_read_stage_and_no_preapproval_apply(self) -> None:
        events = workspace_stage_events()
        stages = g1._workspace_stages(events)
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertEqual(stage.stage_id, "stage_brief")
        self.assertEqual(stage.revision, 1)
        artifact = g1.ArtifactReference(
            artifact_id="art_brief",
            revision=4,
            kind="document",
            content_ref="artifact://art_brief/revisions/4",
            content_digest="a" * 64,
        )
        g1._assert_agent_loaded_edited_artifact(events, artifact)
        g1._assert_only_workspace_or_artifact_tools(events)
        g1._assert_no_workspace_apply(events)

        unrelated = [
            *events,
            {"event_type": "tool_call_started", "payload": {"tool_name": "web_search"}},
        ]
        with self.assertRaisesRegex(AssertionError, "unrelated"):
            g1._assert_only_workspace_or_artifact_tools(unrelated)
        applied = [
            *events,
            {
                "event_type": "effect.applied",
                "payload": {"executor": "workspace", "stage_id": "stage_brief"},
            },
        ]
        with self.assertRaisesRegex(AssertionError, "before explicit approval"):
            g1._assert_no_workspace_apply(applied)

    def test_main_posture_comes_from_real_electron_ipc_not_driver_label(self) -> None:
        class PostureSession:
            def __init__(self, posture: bool) -> None:
                self.posture = posture
                self.javascript: str | None = None

            def evaluate(self, javascript: str) -> str:
                self.javascript = javascript
                return json.dumps({"productionPosture": self.posture})

        production = PostureSession(True)
        g1._assert_main_production_posture(production)  # type: ignore[arg-type]
        assert production.javascript is not None
        self.assertIn("auth.get-posture", production.javascript)
        self.assertNotIn('rpc("status")', production.javascript)
        with self.assertRaisesRegex(AssertionError, "production supervisor posture"):
            g1._assert_main_production_posture(PostureSession(False))  # type: ignore[arg-type]

    def test_artifact_guard_requires_the_requested_brief_name(self) -> None:
        g1._assert_artifact_named_brief(
            {"artifact": {"title": g1.ARTIFACT_NAME}, "suggested_filename": None}
        )
        g1._assert_artifact_named_brief(
            {
                "artifact": {"title": "Review notes"},
                "suggested_filename": g1.ARTIFACT_NAME,
            }
        )
        with self.assertRaisesRegex(AssertionError, "brief.md"):
            g1._assert_artifact_named_brief(
                {"artifact": {"title": "Review notes"}, "suggested_filename": None}
            )

    def test_agent_load_rejects_same_name_or_revision_foreign_artifact(self) -> None:
        edited = g1.ArtifactReference(
            artifact_id="art_brief",
            revision=4,
            kind="document",
            content_ref="artifact://art_brief/revisions/4",
            content_digest="a" * 64,
        )
        foreign = [
            {
                "event_type": "read.executed",
                "payload": {
                    "capability": "artifact",
                    "artifact_id": "art_foreign",
                    "path": g1.ARTIFACT_NAME,
                    "revision": edited.revision,
                    "content_ref": "artifact://art_foreign/revisions/4",
                    "content_digest": edited.content_digest,
                },
            }
        ]
        with self.assertRaisesRegex(AssertionError, "foreign artifact"):
            g1._assert_agent_loaded_edited_artifact(foreign, edited)

        wrong_digest = [
            {
                "event_type": "read.executed",
                "payload": {
                    "capability": "artifact",
                    "artifact_id": edited.artifact_id,
                    "revision": edited.revision,
                    "content_ref": edited.content_ref,
                    "content_digest": "b" * 64,
                },
            }
        ]
        with self.assertRaisesRegex(AssertionError, "immutable content digest"):
            g1._assert_agent_loaded_edited_artifact(wrong_digest, edited)

    def test_staged_proposal_must_be_the_exact_immutable_artifact_bytes(self) -> None:
        proposal_bytes = b"# Reviewed Desktop Brief\n\nExact staged bytes.\n"
        digest = hashlib.sha256(proposal_bytes).hexdigest()
        artifact = g1.ArtifactReference(
            artifact_id="art_brief",
            revision=5,
            kind="document",
            content_ref="artifact://art_brief/revisions/5",
            content_digest=digest,
        )
        material = json.dumps(
            {
                "target_digest": "b" * 64,
                "entries": [
                    {
                        "operation": "create",
                        "relative_path": g1.ARTIFACT_NAME,
                        "content_ref": g1.ARTIFACT_BLOB_PREFIX + digest,
                        "content_digest": digest,
                        "content_size": len(proposal_bytes),
                        "content_slot": "content_0",
                        "precondition": {"exists": False},
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        material_digest = hashlib.sha256(material).hexdigest()
        exact_stage = g1.WorkspaceStage(
            stage_id="stage_brief",
            revision=1,
            proposal_digest=material_digest,
            target_digest="b" * 64,
            proposal_content_ref=g1.WORKSPACE_MATERIAL_PREFIX + material_digest,
            display_target="/workspace/fixture/brief.md",
        )
        g1._assert_stage_binds_immutable_artifact(
            exact_stage, artifact, proposal_bytes, material
        )

        foreign_stage = g1.WorkspaceStage(
            **{
                **exact_stage.__dict__,
                "proposal_content_ref": g1.WORKSPACE_MATERIAL_PREFIX + "f" * 64,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                AssertionError, "digest-pinned immutable proposal ref"
            ):
                g1._read_staged_proposal_bytes(Path(temporary), foreign_stage)

        foreign_material = json.dumps(
            {
                **json.loads(material),
                "entries": [
                    {
                        **json.loads(material)["entries"][0],
                        "content_ref": g1.ARTIFACT_BLOB_PREFIX + "f" * 64,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        foreign_material_digest = hashlib.sha256(foreign_material).hexdigest()
        foreign_content_stage = g1.WorkspaceStage(
            **{
                **exact_stage.__dict__,
                "proposal_digest": foreign_material_digest,
                "proposal_content_ref": g1.WORKSPACE_MATERIAL_PREFIX
                + foreign_material_digest,
            }
        )
        with self.assertRaisesRegex(AssertionError, "content ref"):
            g1._assert_stage_binds_immutable_artifact(
                foreign_content_stage, artifact, proposal_bytes, foreign_material
            )

        with tempfile.TemporaryDirectory() as temporary:
            user_data = Path(temporary)
            material_path = (
                user_data
                / "agent-data"
                / "v1"
                / "objects"
                / "sha256"
                / material_digest[:2]
                / material_digest
            )
            material_path.parent.mkdir(parents=True)
            material_path.write_bytes(material)
            self.assertEqual(
                g1._read_staged_proposal_bytes(user_data, exact_stage), material
            )
            material_path.write_bytes(material + b"drift")
            with self.assertRaisesRegex(AssertionError, "do not match"):
                g1._read_staged_proposal_bytes(user_data, exact_stage)

    def test_main_journal_witness_requires_an_encrypted_native_claim_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            user_data = Path(temporary)
            before = g1._main_workspace_journal_snapshot(user_data)
            journal = user_data / "capabilities" / "workspace-journal.bin"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(g1.WORKSPACE_JOURNAL_CIPHER_MARKER + b"opaque")
            native = user_data / "capabilities" / "workspace-v2" / "native-journal"
            native.mkdir(parents=True)
            claim = native / ("c2c-" + "a" * 64)
            claim.write_bytes(b"main-owned-claim-receipt")
            after = g1._main_workspace_journal_snapshot(user_data)
            g1._assert_main_authority_commit(before, after)

            no_claim = g1.MainWorkspaceJournalSnapshot(
                journal_digest=after.journal_digest,
                native_claim_records=frozenset(),
            )
            with self.assertRaisesRegex(
                AssertionError, "workspace journal did not advance"
            ):
                g1._assert_main_authority_commit(after, no_claim)

            journal.write_bytes(g1.WORKSPACE_JOURNAL_PLAINTEXT_MARKER + b"unsafe")
            with self.assertRaisesRegex(AssertionError, "not encrypted"):
                g1._main_workspace_journal_snapshot(user_data)

    def test_every_driven_selector_is_painted_by_product_source(self) -> None:
        """The guard that would have caught the deleted textarea.

        Nothing else in this suite can: a selector is a contract with TypeScript
        that Python cannot execute. So each entry names the source that paints
        it and the smallest expression that must survive there, and this reads
        the real file. When product code retires a selector, this fails first
        and names the selector — instead of the journey passing its guard suite
        and then failing on a real device, or worse, not being run at all.
        """

        self.assertTrue(g1.PAINTED_SELECTORS, "the selector inventory is empty")
        for claim in g1.PAINTED_SELECTORS:
            with self.subTest(selector=claim.selector):
                path = REPO_ROOT / claim.source
                self.assertTrue(
                    path.is_file(),
                    f"{claim.selector} names a source file that no longer exists: "
                    f"{claim.source}",
                )
                self.assertRegex(
                    path.read_text(encoding="utf-8"),
                    claim.emitter,
                    f"{claim.selector} is no longer painted by {claim.source}; the "
                    "journey drives UI the product does not render",
                )

    def test_the_retired_editor_selectors_are_really_gone(self) -> None:
        """Pin the deletion, so nothing quietly reintroduces the old contract.

        These three are what G1 drove while the surface underneath had become a
        block editor: a whole-document textarea, its DOM id, and a button whose
        label is now just "Save". They are asserted ABSENT from every renderer
        source, because a journey that still names them is driving a UI no
        reader has.
        """

        sources = [
            path
            for root in ("packages/surface-renderers/src", "packages/chat-surface/src")
            for path in (REPO_ROOT / root).rglob("*.tsx")
            if ".test." not in path.name
        ]
        self.assertTrue(sources, "no renderer sources found to check")
        for retired in ('data-testid="artifact-editor"', "artifact-editor-text"):
            painted = [
                str(path.relative_to(REPO_ROOT))
                for path in sources
                if retired in path.read_text(encoding="utf-8")
            ]
            self.assertEqual(
                painted,
                [],
                f"{retired} is painted again; G1's editor gesture assumes it is not",
            )

    def test_surface_guards_require_editor_stage_diff_receipt_and_provenance(
        self,
    ) -> None:
        stage = g1.WorkspaceStage(
            stage_id="stage_brief",
            revision=2,
            proposal_digest="a" * 64,
            target_digest="b" * 64,
            proposal_content_ref="artifact://art_brief/revisions/4",
            display_target="/workspace/fixture/brief.md",
        )
        ui = UiSession()
        g1._assert_workspace_stage_surface(ui, stage)
        g1._assert_editor_surface(ui)
        g1._assert_receipt_and_sources(ui)
        g1._open_first_artifact_from_sources(ui)
        self.assertIn("[data-testid=receipt-v2-open]", ui.clicked)
        self.assertIn('[role=tab]:has-text("Sources")', ui.clicked)
        # Nothing above asked the fake about a selector outside the inventory.
        # Without this the fake is free to invent an answer again.
        self.assertEqual(ui.unknown, [])

        with self.assertRaisesRegex(AssertionError, "missing visible review UI"):
            g1._assert_workspace_stage_surface(
                UiSession("[data-testid=tc-workspace-stage-diff]"), stage
            )
        with self.assertRaisesRegex(AssertionError, "artifact editor is missing"):
            g1._assert_editor_surface(UiSession(g1.DOCUMENT_EDITOR))
        with self.assertRaisesRegex(AssertionError, "artifact editor is missing"):
            g1._assert_editor_surface(
                UiSession(f"[data-testid^={g1.PROSE_BLOCK_PREFIX}]")
            )

    def test_a_selector_outside_the_inventory_reads_as_absent(self) -> None:
        """The fake's own contract: it cannot vouch for what it does not know.

        This is the fix for the shape that hid G3 — the old fake returned
        `selector != self.missing`, so `[data-testid=artifact-editor]` was
        "present" years after it stopped being rendered. Presence now comes from
        the source-verified inventory, and anything else is both absent and
        recorded.
        """

        ui = UiSession()
        self.assertFalse(ui.present("[data-testid=artifact-editor]"))
        self.assertFalse(ui.wait_for("#artifact-editor-text"))
        self.assertFalse(ui.present('button:has-text("Save new revision")'))
        self.assertEqual(
            ui.unknown,
            [
                "[data-testid=artifact-editor]",
                "#artifact-editor-text",
                'button:has-text("Save new revision")',
            ],
        )
        # A testid the product BUILDS is accepted only through an inventoried
        # pattern, so a renamed prefix is caught the same way a literal is.
        self.assertTrue(ui.present('[data-testid="doc-block-4"]'))
        self.assertTrue(ui.present('[data-testid="doc-cell-1-0-2-input"]'))
        self.assertFalse(ui.present('[data-testid="markdown-block-4"]'))

    def test_editing_drives_the_blocks_the_product_paints(self) -> None:
        document = (
            "# Desktop workspace review\n\n"
            "The agent drafted this paragraph and a reader may retype it.\n\n"
            "A second paragraph nobody edits.\n"
        )
        ui = EditorUiSession(
            {
                "doc-block-0": "Desktop workspace review",
                "doc-block-1": (
                    "The agent drafted this paragraph and a reader may retype it."
                ),
                "doc-block-2": "A second paragraph nobody edits.",
            }
        )
        planned = g1._plan_user_edits(ui, document)
        self.assertEqual(
            [edit.test_id for edit in planned], ["doc-block-0", "doc-block-1"]
        )
        self.assertEqual(
            [edit.replacement for edit in planned], list(g1.USER_EDIT_VALUES)
        )
        for edit in planned:
            g1._apply_user_edit(ui, edit)
        g1._assert_unsaved_count(ui, 2)
        self.assertEqual(ui.unknown, [])
        # The gesture is per-field, and it commits with a real key rather than
        # by hoping a blur lands: `[data-testid$=-input]` is what it types into.
        self.assertEqual(
            [selector for selector, _ in ui.filled],
            ['[data-testid="doc-block-0-input"]', '[data-testid="doc-block-1-input"]'],
        )
        self.assertEqual({key for _, key in ui.pressed}, {"Enter"})

        expected = g1._expected_spliced_document(document, planned)
        self.assertEqual(
            expected,
            f"# {g1.USER_EDIT_VALUES[0]}\n\n"
            f"{g1.USER_EDIT_VALUES[1]}\n\n"
            "A second paragraph nobody edits.\n",
        )

    def test_the_edit_planner_skips_spans_it_cannot_pin_to_one_place(self) -> None:
        # An empty heading and a value that appears twice both make "which
        # bytes moved" unanswerable, and the journey's whole assertion is that
        # only those bytes moved. Skipping is the honest answer; guessing an
        # offset would be this file re-implementing the block model badly.
        document = "#\n\n| A | B |\n| --- | --- |\n| dup | dup |\n\nTail prose.\n"
        ui = EditorUiSession(
            {
                "doc-block-0": "",
                "doc-cell-1-0-0": "dup",
                "doc-cell-1-0-1": "dup",
                "doc-block-2": "Tail prose.",
            }
        )
        planned = g1._plan_user_edits(ui, document)
        self.assertEqual([edit.test_id for edit in planned], ["doc-block-2"])
        self.assertEqual(
            g1._expected_spliced_document(document, planned),
            document.replace("Tail prose.", g1.USER_EDIT_VALUES[0]),
        )

        with self.assertRaisesRegex(AssertionError, "no block"):
            g1._plan_user_edits(EditorUiSession({"doc-block-0": ""}), document)

    def test_the_splice_expectation_keeps_every_other_byte(self) -> None:
        """The assertion that separates a splice from a re-render.

        A save that reformatted the table, reflowed the prose or flattened a
        link would still contain the typed text. It would not survive this.
        """

        document = (
            "# My Assigned Linear Issues\n\n"
            "| Issue | Status |\n| --- | --- |\n"
            "| [PAR-9](https://linear.app/parth/issue/PAR-9) | Triage |\n\n"
            "All five issues are currently in **Cool** status.\n"
        )
        edits = [
            g1.InPlaceEdit("doc-block-0", "My Assigned Linear Issues", "My queue"),
            g1.InPlaceEdit("doc-cell-1-0-1", "Triage", "Warm"),
        ]
        spliced = g1._expected_spliced_document(document, edits)
        self.assertEqual(
            spliced,
            document.replace("My Assigned Linear Issues", "My queue", 1).replace(
                "| Triage |", "| Warm |", 1
            ),
        )
        self.assertIn("[PAR-9](https://linear.app/parth/issue/PAR-9)", spliced)
        self.assertIn("in **Cool** status.", spliced)

        with self.assertRaisesRegex(AssertionError, "exactly one span"):
            g1._expected_spliced_document(
                document, [g1.InPlaceEdit("doc-block-9", "absent", "x")]
            )

    def test_receipt_sources_and_stage_target_reject_substring_near_misses(
        self,
    ) -> None:
        stage = g1.WorkspaceStage(
            stage_id="stage_brief",
            revision=2,
            proposal_digest="a" * 64,
            target_digest="b" * 64,
            proposal_content_ref="artifact://art_brief/revisions/4",
            display_target="/workspace/fixture/brief.md",
        )

        class WrongTargetUi(UiSession):
            def evaluate(self, javascript: str) -> str:
                if "stage-path" in javascript:
                    return "/workspace/fixture/brief.md.backup"
                return super().evaluate(javascript)

        with self.assertRaisesRegex(AssertionError, "exactly match"):
            g1._assert_workspace_stage_surface(WrongTargetUi(), stage)

        class NearMissReceiptUi(UiSession):
            def evaluate(self, javascript: str) -> str:
                if "receipt-v2-metric" in javascript:
                    return json.dumps(
                        [
                            {
                                "label": "Effects",
                                "value": "1 proposed · 1 approved · 0 applied · 0 rejected",
                            }
                        ]
                    )
                return super().evaluate(javascript)

        with self.assertRaisesRegex(AssertionError, "exact approved workspace effect"):
            g1._assert_receipt_and_sources(NearMissReceiptUi())

        class NearMissSourceUi(UiSession):
            def evaluate(self, javascript: str) -> str:
                if "sources-v2-row" in javascript:
                    return json.dumps(["Artifact history", "Workspace activity log"])
                return super().evaluate(javascript)

        with self.assertRaisesRegex(AssertionError, "exactly one artifact"):
            g1._assert_receipt_and_sources(NearMissSourceUi())

    def test_approval_guard_requires_the_exact_stage_revision_and_digests(self) -> None:
        stage = g1.WorkspaceStage(
            stage_id="stage_brief",
            revision=2,
            proposal_digest="a" * 64,
            target_digest="b" * 64,
            proposal_content_ref="artifact://art_brief/revisions/4",
            display_target="/workspace/fixture/brief.md",
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
                "event_type": "effect.claimed",
                "payload": {
                    "stage_id": stage.stage_id,
                    "revision": stage.revision,
                    "executor": "workspace",
                    "claim_id": "clm_g1_exact",
                },
            },
            {
                "event_type": "effect.applied",
                "payload": {
                    "stage_id": stage.stage_id,
                    "revision": stage.revision,
                    "outcome": "applied",
                    "result_digest": "c" * 64,
                },
            },
        ]
        g1._assert_approved_and_applied(exact, stage)
        wrong_revision = [
            {**exact[0], "payload": {**exact[0]["payload"], "revision": 1}},
            exact[1],
        ]
        with self.assertRaises(AssertionError):
            g1._assert_approved_and_applied(wrong_revision, stage)

        generic_apply = [exact[0], exact[-1]]
        with self.assertRaisesRegex(AssertionError, "without an exact approved claim"):
            g1._assert_approved_and_applied(generic_apply, stage)

    def test_exact_file_verification_is_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / g1.ARTIFACT_NAME
            approved = b"# Approved\n\nExact bytes.\n"
            path.write_bytes(approved)
            g1._assert_exact_file_bytes(path, approved)
            path.write_bytes(approved + b"drift")
            with self.assertRaisesRegex(AssertionError, "bytes differ"):
                g1._assert_exact_file_bytes(path, approved)

    def test_plaintext_secret_guard_never_echoes_the_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "safe.log").write_text("provider configured")
            g1._assert_no_plaintext_secret("secret-not-printed", (root,))
            (root / "leak.log").write_text("secret-not-printed")
            with self.assertRaisesRegex(AssertionError, "plaintext BYOK") as raised:
                g1._assert_no_plaintext_secret("secret-not-printed", (root,))
            self.assertNotIn("secret-not-printed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
