#!/usr/bin/env python3
"""installed-payload — the shipped npm artifact, and the release matrix on it.

The `installed-payload` target drives the globally installed `@0x-copilot/cli`
payload and its own Electron dependency, staged to `~/.0xcopilot`. It
deliberately REJECTS `APP_DIR`: pointing this at the checkout would make an
"installed" journey prove the source tree rather than the shipped artifact, so
that fallback must never exist. Run `make desktop-install` first.

Most of these phases are **blocked**, and that is the honest state of the
product, not a harness defect. G3–G10 fail closed at preflight on three missing
capabilities, recorded in [RUN-RESULTS.md](./RUN-RESULTS.md) and
[generative-workflows' matrix](./MIGRATION.md):

* the installed supervisor does not propagate the env-gated deterministic model
  to `ai-backend` (`GENUI_DETERMINISTIC_SUPERVISED_READY`) — G3, G10;
* binary DOCX publication/preview is not a model-visible artifact contract
  (`GENUI_BINARY_ARTIFACTS_READY`) — G4;
* the public facade cannot register/execute the checked-in local stdio fixture
  in a fresh installed profile (`GENUI_LOCAL_FIXTURE_BRIDGE`) — G5–G9.

Those flags are ATTESTATIONS, not bypasses: setting one only lets the phase
attempt the authenticated path. Every UI, ledger, target, digest and audit
assertion still fails closed.

    make desktop-install
    python3 tools/desktop-journeys/installed_payload.py

Folds in: installed-payload/installed_payload_smoke,
generative-workflows/{g3..g10}, filesystem-access/jJ_principal_unlocks_writes.
Shares `_release_matrix_lib.py` (formerly `generative-workflows/_g3_g10_support.py`).
"""

# The G3-G10 stories are written against the release-matrix vocabulary
# (`ArtifactReference`, `PassConfig`, `assert_receipt`, `grant_workspace`, ...),
# which `_release_matrix_lib` exports as one namespace. Importing ~40 names
# explicitly would be noise that goes stale on the next story; the star import
# is the deliberate choice, so the warnings it raises are silenced here rather
# than per line.
# ruff: noqa: F403, F405

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from _lib import (
    INSTALLED_PAYLOAD_TARGET,
    DriverSession,
    JourneyPlan,
    PhaseBlocked,
    byok_provider,
    preflight_staged_runtime,
)
from _release_matrix_lib import *  # noqa: F403 — the release-matrix vocabulary
from _release_matrix_lib import run_matrix
from _workspace_lib import (
    ENFORCE_LANE,
    dump,
    lane,
)

STATE: dict[str, Any] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


def adopt_matrix(exit_code: int, journey: str) -> None:
    """Honour `run_matrix`'s own verdict instead of forming a second opinion.

    It already distinguishes passed / blocked / skipped / failed and encodes it
    in an exit code; re-deriving that here would be a different answer to the
    same question.
    """

    if exit_code == 0:
        return
    if exit_code == 2:
        raise PhaseBlocked(
            f"{journey} is fail-closed: a declared capability is absent "
            "(see the module docstring for which)"
        )
    if exit_code == 3:
        from _lib import PhaseSkipped

        raise PhaseSkipped(f"{journey}: a local prerequisite is absent")
    raise AssertionError(f"{journey} failed (run_matrix exit {exit_code})")


JOURNEY_ID: Final = "G3"


SLUG: Final = "g3-code-artifact"


SOURCE_PATH: Final = "src/normalize.ts"


TEST_PATH: Final = "src/normalize.test.ts"


INITIAL_SOURCE: Final = (
    "export function normalize(value: string): string {\n  return value.trim();\n}\n"
)


PATCHED_SOURCE: Final = (
    "export function normalize(value: string): string {\n"
    '  return value.trim().replace(/\\s+/g, " ").toLowerCase();\n'
    "}\n"
)


TEST_SOURCE: Final = (
    'import { strict as assert } from "node:assert";\n'
    'import { normalize } from "./normalize";\n\n'
    'assert.equal(normalize("  Hello   WORLD  "), "hello world");\n'
)


CREATE_PROMPT: Final = f"""Read `/workspace/{SOURCE_PATH}` and
`/workspace/{TEST_PATH}` before authoring anything. Do not execute either file
and do not run a shell, sandbox, package manager, interpreter, or test command.
Publish a reviewable code artifact named `{SOURCE_PATH}` with exactly these
UTF-8 bytes:

```typescript
{PATCHED_SOURCE}```

Read the published artifact back to verify its immutable revision. Do not write
or stage a workspace mutation in this step. Do not browse or use connectors."""


STAGE_PROMPT: Final = f"""Load the latest `{SOURCE_PATH}` code artifact from this
conversation, read its immutable revision, and stage its exact bytes as a held
workspace replacement for `/workspace/{SOURCE_PATH}`. The existing test has
already specified the behavior; do not execute code or tests. Do not apply the
workspace change, do not use a shell/sandbox/interpreter, and do not browse or
use connectors."""


@dataclass(frozen=True)
class CreatedState:
    conversation_id: str
    run_id: str
    artifact: ArtifactReference
    run_dir: Path


def g3_deterministic_create_tools() -> list[dict[str, object]]:
    return [
        {"name": "read_file", "args": {"file_path": f"/workspace/{SOURCE_PATH}"}},
        {"name": "read_file", "args": {"file_path": f"/workspace/{TEST_PATH}"}},
        {
            "name": "publish_artifact",
            "args": {
                "kind": "code",
                "title": SOURCE_PATH,
                "media_type": "text/typescript",
                "content": PATCHED_SOURCE,
                "suggested_filename": "normalize.ts",
                "presentation_preference": "canvas",
            },
        },
    ]


def g3_assert_code_artifact(
    session,
    artifact: ArtifactReference,
    run_id: str,
) -> None:
    detail = artifact_detail(session, artifact.artifact_id)
    record = detail.get("artifact")
    title = record.get("title") if isinstance(record, dict) else None
    assert title == SOURCE_PATH or detail.get("suggested_filename") == "normalize.ts", (
        "code artifact has the wrong identity"
    )
    content = read_artifact_bytes(session, artifact)
    assert content == PATCHED_SOURCE.encode("utf-8"), (
        "model did not publish the exact requested TypeScript bytes"
    )
    assert artifact.content_digest == hashlib.sha256(content).hexdigest()

    open_artifact_from_sources(session)
    assert session.wait_for("[data-testid=artifact-code-renderer]"), (
        "valid TypeScript did not render in the inert code viewer"
    )
    assert not session.present("[data-testid=artifact-code-fallback]"), (
        "valid code unexpectedly fell back instead of rendering as source"
    )
    assert not session.present("[data-testid=artifact-raw-fallback]"), (
        "valid code unexpectedly used the raw artifact fallback"
    )
    executable_descendants = session.evaluate(
        """(() => {
          const root=document.querySelector('[data-testid="artifact-code-renderer"]');
          return root ? root.querySelectorAll('iframe,script,object,embed').length : -1;
        })()"""
    )
    assert executable_descendants == 0, (
        "code artifact mounted an executable preview descendant"
    )
    source_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=artifact-code-renderer] code").innerText'
        )
        or ""
    )
    assert "replace(/\\s+/g" in source_text and "toLowerCase()" in source_text
    assert_run_surfaces(session, run_id, required_kinds={"artifact"})


def g3_assert_read_before_publish(events: list[dict[str, object]]) -> None:
    operations = event_operations(events)  # type: ignore[arg-type]
    reads = [
        index
        for index, operation in enumerate(operations)
        if operation in {"read_file", "artifact_read", "read_artifact"}
    ]
    publishes = [
        index
        for index, operation in enumerate(operations)
        if operation == "publish_artifact"
    ]
    assert len(reads) >= 2, "G3 did not record reading source and test before authoring"
    assert publishes and max(reads[:2]) < publishes[0], (
        "code artifact was published before the required source/test reads"
    )


def g3_create_phase(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
) -> CreatedState:
    deterministic_tools = (
        g3_deterministic_create_tools()
        if config.mode is JourneyMode.DETERMINISTIC
        else None
    )
    with journey_environment(
        config.mode,
        deterministic_parallel_tools=deterministic_tools,
    ):
        session = new_session(config, phase="create")
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g3")
                grant_workspace(session, workspace.root, label="G3 code fixture")
                session.shot(f"g3-{config.mode.value}-workspace-grant")
                conversation_id = create_conversation(
                    session, title=f"G3 code artifact ({config.mode.value})"
                )
                run_id, events = run_prompt(session, conversation_id, CREATE_PROMPT)
                assert_event_types(
                    events,
                    required=("artifact.created",),
                    forbidden=("effect.staged", "effect.applied", "write.applied"),
                )
                assert_no_execution_bypass(events)
                g3_assert_read_before_publish(events)
                artifact = artifact_from_events(events, kind="code")
                g3_assert_code_artifact(session, artifact, run_id)
                session.shot(f"g3-{config.mode.value}-code-renderer-provenance")
                if config.mode is JourneyMode.DETERMINISTIC:
                    assert_deterministic_model_attested(session)
                return CreatedState(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    artifact=artifact,
                    run_dir=session.run_dir,
                )
        finally:
            assert_no_plaintext_secret(
                config.key,
                (session.run_dir, session._user_data_dir, workspace.root),
            )


def g3_read_stage_material(
    session,
    stage: EffectStage,
    artifact: ArtifactReference,
) -> None:
    proposal_path = (
        session._user_data_dir
        / "agent-data"
        / "v1"
        / "objects"
        / "sha256"
        / stage.proposal_digest[:2]
        / stage.proposal_digest
    )
    assert proposal_path.is_file() and not proposal_path.is_symlink(), (
        "stage omitted immutable proposal material"
    )
    proposal = proposal_path.read_bytes()
    assert hashlib.sha256(proposal).hexdigest() == stage.proposal_digest
    material = json.loads(proposal)
    assert isinstance(material, dict)
    entries = material.get("entries")
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    assert entry.get("relative_path") == SOURCE_PATH
    assert entry.get("operation") == "replace"
    assert entry.get("content_digest") == artifact.content_digest
    assert (
        entry.get("content_ref") == f"artifact-blob://sha256/{artifact.content_digest}"
    )


def g3_assert_held_patch_surface(session, stage: EffectStage) -> None:
    required = (
        "[data-testid=tc-workspace-stage]",
        "[data-testid=tc-workspace-stage-path]",
        "[data-testid=tc-workspace-stage-revision]",
        "[data-testid=tc-workspace-stage-diff-text]",
        "[data-testid=tc-workspace-stage-approve]",
        "[data-testid=tc-workspace-stage-reject]",
    )
    assert all(session.present(selector) for selector in required), (
        "G3 held code patch is missing its exact review controls"
    )
    path_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-path]").innerText'
        )
        or ""
    )
    assert path_text.endswith(SOURCE_PATH)
    revision_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-revision]").innerText'
        )
        or ""
    )
    assert f"rev {stage.revision}" in revision_text
    diff_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-diff-text]").innerText'
        )
        or ""
    )
    assert "return value.trim();" in diff_text
    assert 'replace(/\\s+/g, " ").toLowerCase()' in diff_text


def g3_stage_phase(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
    created: CreatedState,
) -> Path:
    deterministic_tool = (
        (
            "write_file",
            {
                "file_path": f"/workspace/{SOURCE_PATH}",
                "content": PATCHED_SOURCE,
            },
        )
        if config.mode is JourneyMode.DETERMINISTIC
        else None
    )
    with journey_environment(
        config.mode,
        deterministic_tool=deterministic_tool,
    ):
        session = new_session(config, phase="stage")
        try:
            with session:
                status = session.rpc("status")
                assert status.get("target") == "installed-payload"
                assert status.get("posture") == "prod"
                session.evaluate(
                    f"window.location.hash={json.dumps(f'#/convo/{created.conversation_id}')}"
                )
                assert session.wait_for("[data-testid=composer-textarea]"), (
                    "reopened G3 conversation did not render its composer"
                )
                run_id, events = run_prompt(
                    session, created.conversation_id, STAGE_PROMPT
                )
                assert_event_types(
                    events,
                    required=("effect.staged",),
                    forbidden=("effect.applied", "write.applied"),
                )
                assert_no_execution_bypass(events)
                assert workspace.path(SOURCE_PATH).read_text() == INITIAL_SOURCE
                stages = effect_stages(events)
                assert len(stages) == 1, "G3 must create exactly one held code stage"
                stage = stages[0]
                assert stage.executor == "workspace"
                assert stage.target_ref.startswith("workspace-target://")
                assert str(workspace.root) not in stage.target_ref
                g3_read_stage_material(session, stage, created.artifact)
                g3_assert_held_patch_surface(session, stage)
                session.shot(f"g3-{config.mode.value}-unified-held-diff")

                approve_native_workspace_stage(session)
                applied_events = wait_for_stage_terminal(
                    session,
                    run_id,
                    stage,
                    decision="approve",
                    applied=True,
                )
                assert_no_execution_bypass(applied_events)
                actual = workspace.path(SOURCE_PATH).read_bytes()
                assert actual == PATCHED_SOURCE.encode("utf-8"), (
                    "host file differs from the exact approved code artifact"
                )
                assert (
                    hashlib.sha256(actual).hexdigest()
                    == created.artifact.content_digest
                )
                assert_receipt(
                    session,
                    expected_text=(
                        "Completed",
                        "1 proposed",
                        "1 approved",
                        "1 applied",
                    ),
                )
                session.click('[role=tab]:has-text("Sources")')
                assert session.wait_for("[data-testid=sources-v2-tab]")
                provenance = str(
                    session.evaluate(
                        'document.querySelector("[data-testid=sources-v2-tab]").innerText'
                    )
                    or ""
                )
                assert "Artifact" in provenance and "Workspace activity" in provenance
                session.shot(f"g3-{config.mode.value}-approved-receipt")
                if config.mode is JourneyMode.DETERMINISTIC:
                    assert_deterministic_model_attested(session)
                return session.run_dir
        finally:
            assert_no_plaintext_secret(
                config.key,
                (
                    created.run_dir,
                    session.run_dir,
                    session._user_data_dir,
                    workspace.root,
                ),
            )


def run_pass(config: PassConfig) -> None:
    with ThrowawayJourneyRoot(JOURNEY_ID, config.mode) as workspace:
        workspace.seed_text(SOURCE_PATH, INITIAL_SOURCE)
        workspace.seed_text(TEST_PATH, TEST_SOURCE)
        created = g3_create_phase(config, workspace)
        g3_stage_phase(config, workspace, created)


g4_JOURNEY_ID: Final = "G4"


g4_SLUG: Final = "g4-docx-artifact"


FILENAME: Final = "status-update.docx"


INITIAL_TITLE: Final = "Launch Status Update"


REVISED_TITLE: Final = "Launch Readiness Update"


BODY_TEXT: Final = (
    "Studio review is complete. The export workflow and approval walkthrough "
    "are ready for release review."
)


g4_CREATE_PROMPT: Final = f"""Create a reviewable binary DOCX artifact named
`{FILENAME}`. Its document title must be exactly `{INITIAL_TITLE}` and its body
must be exactly `{BODY_TEXT}`. Publish it as a binary document artifact; do not
encode the DOCX as chat text or base64. Do not write a workspace file, browse,
use connectors, or claim an export."""


REVISE_PROMPT: Final = f"""Load the latest `{FILENAME}` artifact from this
conversation and create one new immutable DOCX revision. Change only the
document title from `{INITIAL_TITLE}` to `{REVISED_TITLE}`; preserve the exact
body and valid DOCX package. Do not replace or delete revision 1, export, write
the workspace, browse, or use connectors."""


g4_STAGE_PROMPT: Final = f"""Load revision 2 of `{FILENAME}`, read back its exact
binary content and digest, then stage it as a held workspace replacement for
`/workspace/{FILENAME}`. Do not apply it, export it, browse, or use connectors."""


def g4_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            names = set(package.namelist())
            assert "[Content_Types].xml" in names
            assert "word/document.xml" in names
            assert all(not name.startswith("/") and ".." not in name for name in names)
            xml = package.read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise AssertionError("artifact is not a safe, valid DOCX package") from exc
    return xml


def g4_assert_docx(content: bytes, *, title: str) -> None:
    xml = g4_docx_text(content)
    assert title in xml, f"DOCX package omitted title {title!r}"
    assert BODY_TEXT in xml, "DOCX package omitted the exact status body"


def g4_assert_document_surface(session) -> None:
    assert session.wait_for("[data-testid=artifact-document-binary-preview]"), (
        "binary DOCX fixed preview did not render"
    )
    assert not session.present("iframe[src^='http']"), (
        "DOCX preview attempted a remote renderer"
    )
    assert session.present("[data-testid=artifact-document-safe-fallback]"), (
        "DOCX preview does not expose its safe fallback"
    )


def g4_export_and_assert_immutable(
    session,
    workspace: ThrowawayJourneyRoot,
    artifact: ArtifactReference,
    original: bytes,
) -> None:
    """Use the host download port and prove the canonical artifact did not change."""

    export_path = workspace.path("exports/status-update.docx")
    export_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    response = session.evaluate(
        f"""(async()=>{{
          const result=await window.bridge.ipc.invoke("artifact.download-to",{{
            artifactId:{json.dumps(artifact.artifact_id)},
            revision:{artifact.revision},
            destination:{json.dumps(str(export_path))}
          }});
          return JSON.stringify(result);
        }})()"""
    )
    assert isinstance(response, str), "DOCX export IPC returned no result"
    exported = json.loads(response)
    assert exported.get("status") == "saved", "DOCX export did not complete"
    assert export_path.read_bytes() == original, "export changed the DOCX bytes"
    reread = read_artifact_bytes(session, artifact)
    assert reread == original, "export mutated the canonical artifact revision"


def g4_current_revision(
    session, artifact_id: str, prior: ArtifactReference
) -> ArtifactReference:
    deadline = time.time() + 60
    while time.time() < deadline:
        detail = artifact_detail(session, artifact_id)
        current = detail.get("current_revision")
        if isinstance(current, dict):
            revision = current.get("revision")
            if isinstance(revision, int) and revision > prior.revision:
                digest = current.get("content_digest")
                content_ref = current.get("content_ref")
                assert isinstance(digest, str) and len(digest) == 64
                assert isinstance(content_ref, str) and content_ref
                return ArtifactReference(
                    artifact_id=artifact_id,
                    revision=revision,
                    kind="document",
                    content_ref=content_ref,
                    content_digest=digest,
                )
        time.sleep(0.5)
    raise AssertionError("DOCX title change did not create a new immutable revision")


def g4_run_pass(config: PassConfig) -> None:
    with ThrowawayJourneyRoot(g4_JOURNEY_ID, config.mode) as workspace:
        session = None
        try:
            with journey_environment(config.mode):
                session = new_session(config, phase="document")
                with session:
                    bootstrap_session(session, config, screenshot_prefix="g4")
                    grant_workspace(session, workspace.root, label="G4 DOCX fixture")
                    conversation_id = create_conversation(
                        session, title=f"G4 DOCX ({config.mode.value})"
                    )
                    create_run, create_events = run_prompt(
                        session, conversation_id, g4_CREATE_PROMPT
                    )
                    assert_event_types(
                        create_events,
                        required=("artifact.created",),
                        forbidden=("effect.staged", "effect.applied"),
                    )
                    first = artifact_from_events(create_events, kind="document")
                    first_bytes = read_artifact_bytes(session, first)
                    g4_assert_docx(first_bytes, title=INITIAL_TITLE)
                    open_artifact_from_sources(session)
                    g4_assert_document_surface(session)
                    assert_run_surfaces(
                        session, create_run, required_kinds={"artifact"}
                    )
                    session.shot(f"g4-{config.mode.value}-docx-preview")

                    g4_export_and_assert_immutable(
                        session, workspace, first, first_bytes
                    )
                    session.shot(f"g4-{config.mode.value}-docx-export")

                    revise_run, revise_events = run_prompt(
                        session, conversation_id, REVISE_PROMPT
                    )
                    assert_event_types(
                        revise_events,
                        required=("artifact.revised",),
                        forbidden=("effect.staged", "effect.applied"),
                    )
                    second = g4_current_revision(session, first.artifact_id, first)
                    assert second.revision == first.revision + 1
                    second_bytes = read_artifact_bytes(session, second)
                    g4_assert_docx(second_bytes, title=REVISED_TITLE)
                    assert read_artifact_bytes(session, first) == first_bytes, (
                        "revision switch mutated immutable DOCX revision 1"
                    )
                    assert (
                        hashlib.sha256(second_bytes).hexdigest()
                        == second.content_digest
                    )
                    session.shot(f"g4-{config.mode.value}-immutable-version-switch")

                    stage_run, stage_events = run_prompt(
                        session, conversation_id, g4_STAGE_PROMPT
                    )
                    assert_event_types(
                        stage_events,
                        required=("effect.staged",),
                        forbidden=("effect.applied",),
                    )
                    stages = effect_stages(stage_events)
                    assert len(stages) == 1 and stages[0].executor == "workspace"
                    stage = stages[0]
                    assert workspace.path(FILENAME).exists() is False
                    assert session.wait_for(
                        "[data-testid=tc-workspace-stage-diff-binary]"
                    )
                    binary_diff = str(
                        session.evaluate(
                            'document.querySelector("[data-testid=tc-workspace-stage-diff-binary]").innerText'
                        )
                        or ""
                    )
                    assert second.content_digest[:12] in binary_diff
                    session.shot(f"g4-{config.mode.value}-held-binary-replacement")

                    approve_native_workspace_stage(session)
                    wait_for_stage_terminal(
                        session,
                        stage_run,
                        stage,
                        decision="approve",
                        applied=True,
                    )
                    assert workspace.path(FILENAME).read_bytes() == second_bytes
                    assert_receipt(
                        session,
                        expected_text=("Completed", "1 proposed", "1 applied"),
                    )
                    session.shot(f"g4-{config.mode.value}-approved-docx-receipt")
        finally:
            if session is not None:
                assert_no_plaintext_secret(
                    config.key,
                    (session.run_dir, session._user_data_dir, workspace.root),
                )


g5_JOURNEY_ID: Final = "G5"


g5_SLUG: Final = "g5-local-email-triage"


THREAD_TARGET: Final = f"{FIXTURE_NAMESPACE}/mail/threads/thr_q3_renewal"


REVISED_REPLY: Final = (
    "Hi Sam — the launch analytics bundle includes the export workflow. "
    "I will share the release plan before the Q3 renewal review."
)


PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
First call fixture_reset. List the fixture inbox, open the thread whose subject
contains `Q3 renewal` at exact target `{THREAD_TARGET}`, and summarize it from
the returned messages. Draft (do not send) a reply to `sam@fixture.invalid`
that mentions both `export workflow` and `release plan`. Keep the draft held
for visible user review. Never use another connector, address, URL, or account,
and never claim the mail was sent."""


AUDIT_PROMPT: Final = """Using only the same local fixture connector, call
fixture_audit and report no new action. Do not draft, send, or modify anything."""


def g5_run_pass(config: PassConfig) -> None:
    # The deterministic fake currently cannot switch its scripted MCP call
    # sequence after an in-run user approval; the top-level supervised preflight
    # therefore remains blocked until that packaged lane is explicitly ready.
    with journey_environment(config.mode):
        session = new_session(config)
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g5")
                fixture = register_local_fixture(session)
                conversation_id = create_conversation(
                    session, title=f"G5 local mail ({config.mode.value})"
                )
                scope_fixture_conversation(session, conversation_id, fixture)

                run_id, events = run_prompt(session, conversation_id, PROMPT)
                assert_fixture_operations(
                    events,
                    required=(
                        "fixture_reset",
                        "mail_list_threads",
                        "mail_get_thread",
                        "mail_draft_reply",
                    ),
                    forbidden=("mail_send_draft",),
                )
                assert_event_types(events, required=("effect.staged",))
                assert session.wait_for(
                    "[data-testid=record-renderer]"
                ) or session.wait_for("[data-testid=surface-record-fallback]", 5), (
                    "mail read did not render an honest record surface"
                )
                assert session.wait_for("[data-testid=tc-staged-draft]"), (
                    "mail reply did not render as a held staged draft"
                )
                staged_text = str(
                    session.evaluate(
                        'document.querySelector("[data-testid=tc-staged-draft]").innerText'
                    )
                    or ""
                )
                assert "sam@fixture.invalid" in staged_text
                assert "held for approval" in staged_text
                session.shot(f"g5-{config.mode.value}-mail-record-draft")

                edited_revision = edit_staged_draft(session, REVISED_REPLY)
                assert edited_revision >= 2
                events = replay_events(session, run_id)
                stages = effect_stages(events)
                assert len(stages) == 1 and stages[0].executor == "mcp"
                stage = stages[0]
                assert stage.revision == edited_revision
                assert stage.target_ref.startswith("mcp-target://")
                session.shot(f"g5-{config.mode.value}-edited-reply-diff")

                session.click("[data-testid=tc-approve-bar-approve]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="approve", applied=True
                )
                assert session.wait_for("[data-testid=tc-staged-draft-applied]"), (
                    "approved fixture reply did not reach the sent terminal state"
                )

                _, audit_events = run_prompt(session, conversation_id, AUDIT_PROMPT)
                assert_fixture_operations(audit_events, required=("fixture_audit",))
                audit = extract_fixture_audit(audit_events)
                operations = audit_operations(audit)
                assert operations.count("mail.send_draft") == 1
                assert "mail.get_thread" in operations
                sends = [
                    entry
                    for entry in audit["entries"]
                    if entry.get("operation") == "mail.send_draft"
                ]
                payload = sends[0].get("payload")
                assert isinstance(payload, dict)
                assert payload.get("thread_id") == "thr_q3_renewal"
                assert payload.get("recipient") == "sam@fixture.invalid"
                assert isinstance(payload.get("revision"), str)
                assert str(payload.get("receipt", "")).startswith("fixture://")
                assert len(fixture_tool_payloads(audit_events, "fixture_audit")) == 1
                assert_receipt(
                    session,
                    expected_text=("Completed", "1 proposed", "1 approved"),
                )
                session.shot(f"g5-{config.mode.value}-local-send-receipt")
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir)
            )


g6_JOURNEY_ID: Final = "G6"


g6_SLUG: Final = "g6-local-x-timeline"


POST_TARGET: Final = f"{FIXTURE_NAMESPACE}/timeline/posts/post_northstar_launch"


g6_REVISED_REPLY: Final = (
    "A clear walkthrough shows the approval diff first, then the review "
    "decision and local receipt—calm, concrete, and reversible."
)


g6_PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
Call fixture_reset, list the local timeline, and open Northstar's post at exact
target `{POST_TARGET}`. Draft a reply from the fixture account `@aria` that
answers `@northstar` and contains `approval` and `review`. Keep it held; do not
publish it. Never use a browser, X account, URL, or non-fixture destination."""


g6_AUDIT_PROMPT: Final = """Call fixture_audit on the same local fixture and make
no other call or change."""


def g6_audit(session, conversation_id: str) -> dict:
    _, events = run_prompt(session, conversation_id, g6_AUDIT_PROMPT)
    assert_fixture_operations(events, required=("fixture_audit",))
    return extract_fixture_audit(events)


def g6_run_pass(config: PassConfig) -> None:
    with journey_environment(config.mode):
        session = new_session(config)
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g6")
                fixture = register_local_fixture(session)
                conversation_id = create_conversation(
                    session, title=f"G6 local timeline ({config.mode.value})"
                )
                scope_fixture_conversation(session, conversation_id, fixture)
                run_id, events = run_prompt(session, conversation_id, g6_PROMPT)
                assert_fixture_operations(
                    events,
                    required=(
                        "fixture_reset",
                        "timeline_list_posts",
                        "timeline_get_post",
                        "timeline_draft_reply_post",
                    ),
                    forbidden=("timeline_publish_draft",),
                )
                assert session.wait_for(
                    "[data-testid=record-renderer]"
                ) or session.wait_for("[data-testid=surface-record-fallback]", 5)
                assert session.wait_for("[data-testid=tc-staged-draft]")
                context = str(
                    session.evaluate(
                        'document.querySelector("[data-testid=tc-chat]").innerText'
                    )
                    or ""
                )
                assert "@northstar" in context and "@aria" in context
                session.shot(f"g6-{config.mode.value}-timeline-record")

                revision = edit_staged_draft(session, g6_REVISED_REPLY)
                stages = effect_stages(replay_events(session, run_id))
                assert len(stages) == 1
                stage = stages[0]
                assert stage.executor == "mcp" and stage.revision == revision
                session.shot(f"g6-{config.mode.value}-revised-post-diff")

                session.click("[data-testid=tc-approve-bar-reject]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="reject", applied=False
                )
                rejected_audit = g6_audit(session, conversation_id)
                assert "timeline.publish_draft" not in audit_operations(rejected_audit)
                assert session.wait_for("[data-testid=tc-approve-bar-restore]")
                session.shot(f"g6-{config.mode.value}-rejected-unchanged")

                session.click("[data-testid=tc-approve-bar-restore]")
                assert session.wait_for("[data-testid=tc-approve-bar-approve]")
                session.click("[data-testid=tc-approve-bar-approve]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="approve", applied=True
                )
                final_audit = g6_audit(session, conversation_id)
                operations = audit_operations(final_audit)
                assert operations.count("timeline.publish_draft") == 1
                publishes = [
                    entry
                    for entry in final_audit["entries"]
                    if entry.get("operation") == "timeline.publish_draft"
                ]
                payload = publishes[0].get("payload")
                assert isinstance(payload, dict)
                assert payload.get("in_reply_to") == "post_northstar_launch"
                assert str(payload.get("receipt", "")).startswith("fixture://")
                assert_receipt(
                    session,
                    expected_text=("Completed", "1 proposed", "1 approved"),
                )
                session.shot(f"g6-{config.mode.value}-fixture-publish-receipt")
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir)
            )


g7_JOURNEY_ID: Final = "G7"


g7_SLUG: Final = "g7-local-discord-moderation"


CHANNEL_TARGET: Final = f"{FIXTURE_NAMESPACE}/discord/channels/chn_launch_room"


g7_PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
Call fixture_reset, list channels in the `Launch Week` fixture guild, and read
`#launch-room` at exact target `{CHANNEL_TARGET}`. Summarize the decision, then
draft exactly one pinned announcement to that channel mentioning exactly
`@maya` and `@leo`; its body must include `Studio` and `approval`. Hold it for
approval and do not publish. Never access a real Discord account or URL."""


g7_AUDIT_PROMPT: Final = "Call fixture_audit only; do not draft or publish anything."


def g7_audit(session, conversation_id: str) -> dict:
    _, events = run_prompt(session, conversation_id, g7_AUDIT_PROMPT)
    assert_fixture_operations(events, required=("fixture_audit",))
    return extract_fixture_audit(events)


def g7_wait_for_retry_surface(session) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        if session.present("[data-testid=tc-staged-draft-failed]"):
            return
        time.sleep(0.5)
    raise AssertionError("first Discord fixture failure did not render honestly")


def g7_run_pass(config: PassConfig) -> None:
    with journey_environment(config.mode):
        session = new_session(config)
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g7")
                fixture = register_local_fixture(session)
                conversation_id = create_conversation(
                    session, title=f"G7 Discord moderation ({config.mode.value})"
                )
                scope_fixture_conversation(session, conversation_id, fixture)
                run_id, events = run_prompt(session, conversation_id, g7_PROMPT)
                assert_fixture_operations(
                    events,
                    required=(
                        "fixture_reset",
                        "discord_list_channels",
                        "discord_get_messages",
                        "discord_draft_announcement",
                    ),
                    forbidden=("discord_publish_announcement",),
                )
                assert session.wait_for(
                    "[data-testid=record-renderer]"
                ) or session.wait_for("[data-testid=surface-record-fallback]", 5)
                assert session.wait_for("[data-testid=tc-staged-draft]")
                visible = str(
                    session.evaluate(
                        'document.querySelector("[data-testid=tc-chat]").innerText'
                    )
                    or ""
                )
                for expected in ("Launch Week", "launch-room", "@maya", "@leo"):
                    assert expected in visible
                stages = effect_stages(events)
                assert len(stages) == 1 and stages[0].executor == "mcp"
                stage = stages[0]
                session.shot(f"g7-{config.mode.value}-announcement-diff")

                session.click("[data-testid=tc-approve-bar-approve]")
                g7_wait_for_retry_surface(session)
                retry_audit = g7_audit(session, conversation_id)
                retry_operations = audit_operations(retry_audit)
                assert (
                    retry_operations.count(
                        "discord.publish_announcement.retryable_failure"
                    )
                    == 1
                )
                assert "discord.publish_announcement" not in retry_operations
                assert session.present("[data-testid=tc-approve-bar-approve]")
                assert not session.present("[data-testid=tc-staged-draft-applied]")
                session.shot(f"g7-{config.mode.value}-retryable-failure")

                session.click("[data-testid=tc-approve-bar-approve]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="approve", applied=True
                )
                final_audit = g7_audit(session, conversation_id)
                operations = audit_operations(final_audit)
                assert operations.count("discord.publish_announcement") == 1
                assert (
                    operations.count("discord.publish_announcement.retryable_failure")
                    == 1
                )
                publishes = [
                    entry
                    for entry in final_audit["entries"]
                    if entry.get("operation") == "discord.publish_announcement"
                ]
                payload = publishes[0].get("payload")
                assert isinstance(payload, dict)
                assert payload.get("channel_id") == "chn_launch_room"
                assert payload.get("mentions") == ["@leo", "@maya"]
                assert str(payload.get("receipt", "")).startswith("fixture://")
                assert_receipt(
                    session,
                    expected_text=("Completed", "1 proposed", "1 applied"),
                )
                session.shot(f"g7-{config.mode.value}-retry-receipt")
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir)
            )


g8_JOURNEY_ID: Final = "G8"


g8_SLUG: Final = "g8-mixed-work"


PLAN: Final = (
    "# Launch plan\n\n"
    "Studio ships the editor and CSV flows first. Maya and Leo will review "
    "the approval walkthrough before the Q3 renewal release-plan follow-up.\n"
)


METRICS: Final = (
    "metric,target,status\n"
    "approval_walkthrough,ready,review\n"
    "export_workflow,included,confirmed\n"
)


g8_PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
Call fixture_reset. Read the Q3 renewal mail thread and the messages in the
Launch Week `#launch-room` channel before writing. Then stage exactly two
independent fixture workspace revisions, both held for user approval:

1. `launch-plan.md` with exact content:
```markdown
{PLAN}```
2. `launch-metrics.csv` with exact content:
```csv
{METRICS}```

Use exact targets `{FIXTURE_WORKSPACE_ROOT}/launch-plan.md` and
`{FIXTURE_WORKSPACE_ROOT}/launch-metrics.csv`. Do not apply either write, call
another connector, or make any communication change."""


VERIFY_PROMPT: Final = """Using only the same fixture, read
`launch-plan.md` and `launch-metrics.csv` at their exact fixture:// targets,
then call fixture_audit. Make no write or communication call."""


def g8_approve_from_rail(session, stage_id: str) -> None:
    selector = f'[data-testid="right-rail-approval-accept-{stage_id}"]'
    assert session.wait_for(selector), f"Approvals rail omitted stage {stage_id}"
    session.click(selector)


def g8_run_pass(config: PassConfig) -> None:
    with journey_environment(config.mode):
        session = new_session(config)
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g8")
                fixture = register_local_fixture(session)
                conversation_id = create_conversation(
                    session, title=f"G8 mixed local work ({config.mode.value})"
                )
                scope_fixture_conversation(session, conversation_id, fixture)
                run_id, events = run_prompt(session, conversation_id, g8_PROMPT)
                assert_fixture_operations(
                    events,
                    required=(
                        "fixture_reset",
                        "mail_get_thread",
                        "discord_get_messages",
                        "workspace_write_revision",
                    ),
                    forbidden=(
                        "mail_send_draft",
                        "timeline_publish_draft",
                        "discord_publish_announcement",
                    ),
                )
                stages = effect_stages(events)
                assert len(stages) == 2, "mixed run must stage exactly two writes"
                assert len({stage.stage_id for stage in stages}) == 2
                assert all(stage.executor == "mcp" for stage in stages)
                assert session.wait_for("[data-testid=tc-tabs]"), (
                    "two staged artifacts did not produce independent Studio tabs"
                )
                assert (
                    int(
                        session.evaluate(
                            'document.querySelectorAll("[data-testid=tc-workspace-stage],'
                            "[data-testid=tc-staged-draft],"
                            '[data-testid=tc-staged-table]").length'
                        )
                        or 0
                    )
                    >= 2
                )
                session.click('[role=tab]:has-text("Sources")')
                assert session.wait_for("[data-testid=sources-v2-tab]")
                session.click('[role=tab]:has-text("Approvals")')
                assert session.wait_for(
                    "[data-testid=run-rail-panel-approvals]"
                ) or session.wait_for("[data-testid=approvals-tab-content]", 5)
                session.click('[role=tab]:has-text("Agents")')
                assert session.wait_for(
                    "[data-testid=workspace-agents-tab]"
                ) or session.wait_for("[data-testid=workspace-agents-tab-empty]", 5)
                session.shot(f"g8-{config.mode.value}-multi-surface-rails")

                # Reverse the prompt order to prove independent effects do not
                # share mutable tab state or require an application order.
                for stage in reversed(stages):
                    session.click('[role=tab]:has-text("Approvals")')
                    g8_approve_from_rail(session, stage.stage_id)
                    wait_for_stage_terminal(
                        session,
                        run_id,
                        stage,
                        decision="approve",
                        applied=True,
                    )

                _, verify_events = run_prompt(session, conversation_id, VERIFY_PROMPT)
                assert_fixture_operations(
                    verify_events,
                    required=("workspace_read", "fixture_audit"),
                    forbidden=(
                        "workspace_write_revision",
                        "workspace_apply_rowset",
                    ),
                )
                serialized = json.dumps(verify_events, sort_keys=True)
                assert PLAN in serialized and METRICS in serialized, (
                    "fixture readback did not preserve both exact artifact contents"
                )
                audit = extract_fixture_audit(verify_events)
                operations = audit_operations(audit)
                assert operations.count("workspace.write_revision") == 2
                applied = [
                    entry.get("payload", {}).get("path")
                    for entry in audit["entries"]
                    if entry.get("operation") == "workspace.write_revision"
                    and isinstance(entry.get("payload"), dict)
                ]
                assert applied == ["launch-metrics.csv", "launch-plan.md"], (
                    "fixture audit did not preserve reverse approval order"
                )
                assert_receipt(
                    session,
                    expected_text=("Completed", "2 proposed", "2 approved"),
                )
                session.shot(f"g8-{config.mode.value}-two-effect-receipt")
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir)
            )


g9_JOURNEY_ID: Final = "G9"


g9_SLUG: Final = "g9-recovery-honesty"


BRIEF_TARGET: Final = f"{FIXTURE_WORKSPACE_ROOT}/project-brief.md"


READ_PROMPT: Final = f"""Use only the installed Generative Workflows Local
Fixture. Call fixture_reset, then read `project-brief.md` at exact target
`{BRIEF_TARGET}`. Its first read intentionally reports `grant_expired`: park
that same operation, show the access gate, and resume the identical operation
after the user reconnects. Report only content actually returned. Do not write."""


UNKNOWN_PROMPT: Final = """Through that same local fixture, invoke the
scenario-declared test operation `calendar.archive_nonexistent` exactly once.
Preserve its structured `unknown_operation` result in a raw fallback and state
plainly that nothing was archived. Do not substitute another tool or claim
success."""


g9_STAGE_PROMPT: Final = f"""Using only the fixture, stage a held revision of
`project-brief.md` at `{BRIEF_TARGET}` that appends exactly:

Recovery review remains pending.

Do not commit it."""


CANCEL_PROMPT: Final = """Begin a detailed streaming comparison of every local
fixture domain. Do not call a write tool. Keep the answer in progress until
the user cancels it."""


def g9_wait_for_gate(session) -> str:
    assert session.wait_for("[data-testid=tc-gate-card]"), (
        "expired fixture grant did not render a gate card"
    )
    gate_id = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-gate-ledger-id]").innerText'
        )
        or ""
    ).strip()
    assert gate_id, "gate card omitted its durable gate identity"
    return gate_id


def g9_assert_same_parked_operation(events: list[dict[str, Any]]) -> None:
    opened = [
        event_payload(event)
        for event in events
        if event.get("event_type") == "gate.opened.v2"
    ]
    resolved = [
        event_payload(event)
        for event in events
        if event.get("event_type") == "gate.resolved.v2"
    ]
    assert len(opened) == len(resolved) == 1
    assert opened[0].get("gate_id") == resolved[0].get("gate_id")
    operation_id = opened[0].get("operation_id")
    assert isinstance(operation_id, str) and operation_id
    matching = [
        event.get("event_type")
        for event in events
        if event_payload(event).get("operation_id") == operation_id
    ]
    assert "operation.requested" in matching
    assert "operation.completed" in matching
    assert matching.index("operation.requested") < matching.index(
        "operation.completed"
    ), "gate recovery replaced or completed the parked operation out of order"


def g9_revised_stage(
    first: EffectStage,
    all_events: list[dict[str, Any]],
) -> EffectStage:
    stages = effect_stages(all_events)
    assert len(stages) == 1 and stages[0].stage_id == first.stage_id
    revised = stages[0]
    assert revised.revision == first.revision + 1
    return revised


def g9_cancel_stream(session, conversation_id: str) -> tuple[str, list[dict[str, Any]]]:
    run_id = submit_prompt(session, conversation_id, CANCEL_PROMPT)
    cancelled = transport_json(session, "POST", f"/v1/agent/runs/{run_id}/cancel")
    assert isinstance(cancelled, dict)
    wait_for_terminal_run(session, run_id, expected="cancelled")
    events = replay_events(session, run_id)
    assert_event_types(events, required=("run_cancelled",))
    return run_id, events


def g9_run_pass(config: PassConfig) -> None:
    with journey_environment(config.mode):
        session = new_session(config)
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g9")
                fixture = register_local_fixture(session)
                conversation_id = create_conversation(
                    session, title=f"G9 recovery and honesty ({config.mode.value})"
                )
                scope_fixture_conversation(session, conversation_id, fixture)

                read_run = submit_prompt(session, conversation_id, READ_PROMPT)
                gate_id = g9_wait_for_gate(session)
                session.shot(f"g9-{config.mode.value}-expired-grant-gate")
                session.click("[data-testid=tc-gate-connect]")
                wait_for_terminal_run(session, read_run)
                read_events = replay_events(session, read_run)
                g9_assert_same_parked_operation(read_events)
                assert_fixture_operations(
                    read_events,
                    required=("fixture_reset", "workspace_read"),
                    forbidden=("workspace_write_revision",),
                )
                assert gate_id in json.dumps(read_events, sort_keys=True)
                session.shot(f"g9-{config.mode.value}-same-call-resumed")

                _, unknown_events = run_prompt(session, conversation_id, UNKNOWN_PROMPT)
                assert "unknown_operation" in json.dumps(unknown_events, sort_keys=True)
                assert session.wait_for("[data-testid=tc-raw-fallback]")
                visible = str(
                    session.evaluate(
                        'document.querySelector("[data-testid=tc-chat]").innerText'
                    )
                    or ""
                ).lower()
                assert "unknown" in visible and "nothing" in visible
                assert not any(
                    claim in visible
                    for claim in ("archive completed", "successfully archived")
                )
                session.shot(f"g9-{config.mode.value}-unknown-operation-raw")

                stage_run, stage_events = run_prompt(
                    session, conversation_id, g9_STAGE_PROMPT
                )
                stages = effect_stages(stage_events)
                assert len(stages) == 1 and stages[0].executor == "mcp"
                first = stages[0]
                revise_prompt = (
                    f"Revise held stage `{first.stage_id}` without creating a new "
                    "stage. Append `Still requires fresh approval.` and keep it held."
                )
                revise_run, revise_events = run_prompt(
                    session, conversation_id, revise_prompt
                )
                current = g9_revised_stage(first, [*stage_events, *revise_events])
                stale_status = transport_error_status(
                    session,
                    "POST",
                    f"/v1/agent/effect-stages/{first.stage_id}/decision"
                    f"?run_id={revise_run}",
                    body={
                        "revision": first.revision,
                        "decision": "approve",
                        "proposal_digest": first.proposal_digest,
                        "target_digest": first.target_digest,
                    },
                )
                assert stale_status == 409, (
                    "stale fixture revision was not rejected as a conflict"
                )
                session.click("[data-testid=tc-workspace-stage-reject]")
                terminal = wait_for_stage_terminal(
                    session,
                    revise_run,
                    current,
                    decision="reject",
                    applied=False,
                )
                assert_stage_decision(
                    terminal, current, decision="reject", applied=False
                )
                assert not any(
                    event.get("event_type") == "effect.applied"
                    and event_payload(event).get("stage_id") == first.stage_id
                    for event in [*stage_events, *revise_events, *terminal]
                )
                session.shot(f"g9-{config.mode.value}-stale-rejected-diff")

                _, cancel_events = g9_cancel_stream(session, conversation_id)
                assert not any(
                    event.get("event_type") == "final_response"
                    for event in cancel_events
                ), "cancelled stream persisted a fabricated terminal answer"
                session.shot(f"g9-{config.mode.value}-cancelled-stream")

                _, audit_events = run_prompt(
                    session,
                    conversation_id,
                    "Call fixture_audit only. Make no other tool call.",
                )
                audit = extract_fixture_audit(audit_events)
                operations = audit_operations(audit)
                assert operations.count("workspace.read.grant_expired") == 1
                assert operations.count("fixture.unknown_operation") == 1
                assert "workspace.write_revision" not in operations
                assert_fixture_operations(
                    audit_events,
                    required=("fixture_audit",),
                    forbidden=("workspace_write_revision",),
                )
                assert stage_run != revise_run
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir)
            )


g10_JOURNEY_ID: Final = "G10"


g10_SLUG: Final = "g10-retention-reopen"


MARKDOWN_PATH: Final = "project-brief.md"


CSV_PATH: Final = "pipeline.csv"


INITIAL_MARKDOWN: Final = "# Project Brief\n\nThis draft has not been reviewed.\n"


FINAL_MARKDOWN: Final = (
    "# Reviewed Project Brief\n\n"
    "The Desktop workflow keeps every local mutation held for explicit review.\n"
)


INITIAL_CSV: Final = (
    "account,stage,amount\nNorthstar,qualification,10000\nBeacon,proposal,15000\n"
)


FINAL_CSV: Final = (
    "account,stage,amount\nNorthstar,proposal,12000\nBeacon,closed_won,15000\n"
)


PROMPT_MARKER: Final = "g10-usage-redaction-marker-7d41"


g10_CREATE_PROMPT: Final = f"""Create exactly two reviewable artifacts in Studio:

1. a Markdown document named `{MARKDOWN_PATH}` with exact content:
```markdown
{FINAL_MARKDOWN}```
2. a CSV dataset named `{CSV_PATH}` with exact content:
```csv
{FINAL_CSV}```

The private request marker is `{PROMPT_MARKER}`. It must never appear in usage
metadata. Publish both artifacts but do not stage or write workspace files."""


@dataclass(frozen=True)
class Created:
    conversation_id: str
    run_id: str
    markdown: ArtifactReference
    dataset: ArtifactReference


@dataclass(frozen=True)
class Applied:
    run_id: str
    stage: EffectStage


def g10_create_artifacts(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
    run_dirs: list[Path],
) -> Created:
    deterministic_tools = [
        {
            "name": "publish_artifact",
            "args": {
                "kind": "document",
                "title": MARKDOWN_PATH,
                "media_type": "text/markdown",
                "content": FINAL_MARKDOWN,
                "suggested_filename": MARKDOWN_PATH,
                "presentation_preference": "canvas",
            },
        },
        {
            "name": "publish_artifact",
            "args": {
                "kind": "dataset",
                "title": CSV_PATH,
                "media_type": "text/csv",
                "content": FINAL_CSV,
                "suggested_filename": CSV_PATH,
                "presentation_preference": "canvas",
            },
        },
    ]
    with journey_environment(
        config.mode,
        deterministic_parallel_tools=(
            deterministic_tools if config.mode is JourneyMode.DETERMINISTIC else None
        ),
    ):
        session = new_session(config, phase="create")
        try:
            with session:
                bootstrap_session(session, config, screenshot_prefix="g10")
                grant_workspace(session, workspace.root, label="G10 reopen fixture")
                conversation_id = create_conversation(
                    session, title=f"G10 retention reopen ({config.mode.value})"
                )
                run_id, events = run_prompt(session, conversation_id, g10_CREATE_PROMPT)
                assert_event_types(
                    events,
                    required=("artifact.created",),
                    forbidden=("effect.staged", "effect.applied"),
                )
                assert (
                    sum(
                        event.get("event_type") == "artifact.created"
                        for event in events
                    )
                    == 2
                ), "G10 must publish exactly one document and one dataset"
                markdown = artifact_from_events(events, kind="document")
                dataset = artifact_from_events(events, kind="dataset")
                assert read_artifact_bytes(session, markdown) == FINAL_MARKDOWN.encode()
                assert read_artifact_bytes(session, dataset) == FINAL_CSV.encode()
                assert session.wait_for("[data-testid=tc-tabs]")
                session.click(f'[role=tab]:has-text("{MARKDOWN_PATH}")')
                assert session.wait_for("[data-testid=artifact-document-renderer]")
                session.click(f'[role=tab]:has-text("{CSV_PATH}")')
                assert session.wait_for("[data-testid=artifact-dataset-renderer]")
                session.shot(f"g10-{config.mode.value}-document-dataset-created")
                if config.mode is JourneyMode.DETERMINISTIC:
                    assert_deterministic_model_attested(session)
                run_dirs.append(session.run_dir)
                return Created(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    markdown=markdown,
                    dataset=dataset,
                )
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir, workspace.root)
            )


def g10_stage_and_apply(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
    created: Created,
    *,
    phase: str,
    relative_path: str,
    content: str,
    artifact: ArtifactReference,
    run_dirs: list[Path],
) -> Applied:
    prompt = f"""Load immutable artifact `{artifact.artifact_id}` revision
{artifact.revision} from this conversation. Stage its exact bytes as a held
workspace replacement for `/workspace/{relative_path}`. Do not change or apply
it; wait for explicit native approval."""
    deterministic_tool = (
        (
            "write_file",
            {"file_path": f"/workspace/{relative_path}", "content": content},
        )
        if config.mode is JourneyMode.DETERMINISTIC
        else None
    )
    with journey_environment(config.mode, deterministic_tool=deterministic_tool):
        session = new_session(config, phase=phase)
        try:
            with session:
                assert session.rpc("status").get("target") == "installed-payload"
                assert session.rpc("status").get("posture") == "prod"
                assert_main_production_posture(session)
                session.evaluate(
                    "window.location.hash="
                    + json.dumps(f"#/convo/{created.conversation_id}")
                )
                assert session.wait_for("[data-testid=composer-textarea]")
                run_id, events = run_prompt(session, created.conversation_id, prompt)
                assert_event_types(
                    events,
                    required=("effect.staged",),
                    forbidden=("effect.applied", "write.applied"),
                )
                assert_no_execution_bypass(events)
                stages = effect_stages(events)
                assert len(stages) == 1 and stages[0].executor == "workspace"
                stage = stages[0]
                assert stage.target_ref.startswith("workspace-target://")
                assert str(workspace.root) not in stage.target_ref
                assert workspace.path(relative_path).read_text() != content
                assert session.wait_for("[data-testid=tc-workspace-stage]")
                session.shot(f"g10-{config.mode.value}-{relative_path}-held")
                approve_native_workspace_stage(session)
                terminal = wait_for_stage_terminal(
                    session,
                    run_id,
                    stage,
                    decision="approve",
                    applied=True,
                )
                assert_no_execution_bypass(terminal)
                assert workspace.path(relative_path).read_text() == content
                assert_receipt(
                    session,
                    expected_text=(
                        "Completed",
                        "1 proposed",
                        "1 approved",
                        "1 applied",
                    ),
                )
                if config.mode is JourneyMode.DETERMINISTIC:
                    assert_deterministic_model_attested(session)
                run_dirs.append(session.run_dir)
                return Applied(run_id=run_id, stage=stage)
        finally:
            assert_no_plaintext_secret(
                config.key, (session.run_dir, session._user_data_dir, workspace.root)
            )


def g10_assert_no_pending(
    session,
    created: Created,
    applied: tuple[Applied, Applied],
) -> None:
    terminal_runs = {item.run_id for item in applied}
    for path in ("/v1/agent/pending-work", "/v1/agent/pending-work-v2"):
        response = session.transport("GET", path)
        items = response.get("items", [])
        assert isinstance(items, list)
        assert not any(
            isinstance(item, dict)
            and (
                item.get("conversation_id") == created.conversation_id
                or item.get("run_id") in terminal_runs
                or item.get("stage_id") in {effect.stage.stage_id for effect in applied}
            )
            for item in items
        ), f"{path} retained a terminal G10 approval"


def g10_assert_usage(
    session,
    config: PassConfig,
    created: Created,
    applied: tuple[Applied, Applied],
) -> None:
    if config.mode is not JourneyMode.LIVE:
        return
    run_ids = (created.run_id, *(item.run_id for item in applied))
    usage_payloads: list[object] = []
    for run_id in run_ids:
        breakdown = session.transport("GET", f"/v1/usage/runs/{run_id}")
        calls = session.transport("GET", f"/v1/usage/runs/{run_id}/calls")
        assert breakdown.get("run_id") == run_id
        assert breakdown.get("conversation_id") == created.conversation_id
        assert calls.get("run_id") == run_id
        rows = calls.get("calls", [])
        assert isinstance(rows, list) and rows
        assert all(
            isinstance(row, dict)
            and isinstance(row.get("purpose"), str)
            and bool(row["purpose"])
            for row in rows
        ), "live usage calls omitted purpose attribution"
        usage_payloads.extend((breakdown, calls))
    conversation = session.transport(
        "GET", f"/v1/usage/conversations/{created.conversation_id}"
    )
    assert conversation.get("conversation_id") == created.conversation_id
    by_run = conversation.get("by_run", [])
    assert isinstance(by_run, list)
    assert set(run_ids).issubset(
        {row.get("run_id") for row in by_run if isinstance(row, dict)}
    )
    usage_payloads.append(conversation)
    serialized = json.dumps(usage_payloads, sort_keys=True)
    assert PROMPT_MARKER not in serialized
    assert FINAL_MARKDOWN not in serialized and FINAL_CSV not in serialized
    if config.key is not None:
        assert config.key not in serialized


def g10_reopen_and_verify(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
    created: Created,
    applied: tuple[Applied, Applied],
    run_dirs: list[Path],
) -> None:
    with journey_environment(config.mode):
        session = new_session(config, phase="reopen")
        try:
            with session:
                assert session.rpc("status").get("target") == "installed-payload"
                assert session.rpc("status").get("posture") == "prod"
                assert_main_production_posture(session)
                session.evaluate(
                    "window.location.hash="
                    + json.dumps(f"#/convo/{created.conversation_id}/{created.run_id}")
                )
                assert session.wait_for("[data-testid=composer-textarea]")
                for run_id in (
                    created.run_id,
                    applied[0].run_id,
                    applied[1].run_id,
                ):
                    events = replay_events(session, run_id)
                    assert events, f"reopen lost event replay for {run_id}"
                assert session.wait_for("[data-testid=tc-tabs]")
                session.click(f'[role=tab]:has-text("{MARKDOWN_PATH}")')
                assert session.wait_for("[data-testid=artifact-document-renderer]")
                session.click(f'[role=tab]:has-text("{CSV_PATH}")')
                assert session.wait_for("[data-testid=artifact-dataset-renderer]")
                assert (
                    read_artifact_bytes(session, created.markdown)
                    == FINAL_MARKDOWN.encode()
                )
                assert (
                    read_artifact_bytes(session, created.dataset) == FINAL_CSV.encode()
                )
                session.click('[role=tab]:has-text("Sources")')
                assert session.wait_for("[data-testid=sources-v2-tab]")
                session.shot(f"g10-{config.mode.value}-reopened-sources")

                for effect in applied:
                    session.evaluate(
                        "window.location.hash="
                        + json.dumps(
                            f"#/convo/{created.conversation_id}/{effect.run_id}"
                        )
                    )
                    assert session.wait_for("[data-testid=receipt-v2-launch]")
                    assert_receipt(
                        session,
                        expected_text=("Completed", "1 approved", "1 applied"),
                    )
                session.click('[role=tab]:has-text("Approvals")')
                assert session.wait_for("[data-testid=run-rail-panel-approvals]")
                g10_assert_no_pending(session, created, applied)
                retention = session.transport("GET", "/v1/retention/effective")
                effective = retention.get("effective")
                assert isinstance(effective, dict) and effective, (
                    "reopened conversation has no facade retention truth"
                )
                g10_assert_usage(session, config, created, applied)
                session.shot(f"g10-{config.mode.value}-receipt-no-pending")
                run_dirs.append(session.run_dir)
        finally:
            assert_no_plaintext_secret(
                config.key,
                (
                    *run_dirs,
                    session.run_dir,
                    session._user_data_dir,
                    workspace.root,
                ),
            )


def g10_run_pass(config: PassConfig) -> None:
    run_dirs: list[Path] = []
    with ThrowawayJourneyRoot(g10_JOURNEY_ID, config.mode) as workspace:
        workspace.seed_text(MARKDOWN_PATH, INITIAL_MARKDOWN)
        workspace.seed_text(CSV_PATH, INITIAL_CSV)
        created = g10_create_artifacts(config, workspace, run_dirs)
        markdown = g10_stage_and_apply(
            config,
            workspace,
            created,
            phase="markdown-stage",
            relative_path=MARKDOWN_PATH,
            content=FINAL_MARKDOWN,
            artifact=created.markdown,
            run_dirs=run_dirs,
        )
        dataset = g10_stage_and_apply(
            config,
            workspace,
            created,
            phase="dataset-stage",
            relative_path=CSV_PATH,
            content=FINAL_CSV,
            artifact=created.dataset,
            run_dirs=run_dirs,
        )
        g10_reopen_and_verify(config, workspace, created, (markdown, dataset), run_dirs)


JOURNEY = "FS-J"


NAME = "fs-j-principal"


ATTACH = ".aui-folder-bar__attach"


CARD = "[data-testid^=tc-chat-approval-]"


APPROVE = "[data-testid^=tc-chat-approval-approve-]"


SEED_CSV = "region,q3\nnorth,120\nsouth,90\n"


WRITE_PROMPT = (
    "Run `ls /workspace/` to find the mounted folder, read seed.csv inside it, "
    "then write that file back to the same path with one extra column named "
    "`note` whose value is `checked` on every row. Use your filesystem tools."
)


def jj_user_data_dir() -> Path:
    """The dir BOTH boots share — `fresh=False` pins the `-reuse` suffix."""

    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "0xCopilot"
        / f"journey-{NAME}-reuse"
    )


def jj_stub_dialogs(session: DriverSession, folder: Path) -> None:
    session.rpc(
        "mainEval",
        js="""({ dialog }, folder) => {
          dialog.showOpenDialog = async () => ({
            canceled: false, filePaths: [folder],
          });
          dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
          return { stubbed: folder };
        }""",
        arg=str(folder),
    )


def jj_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def jj_approve_for(session: DriverSession, seconds: int) -> int:
    clicked = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        if session.present(CARD) and session.present(APPROVE):
            try:
                session.rpc("clickLast", selector=APPROVE)
                clicked += 1
            except Exception:  # noqa: BLE001 — a card may resolve mid-click
                pass
        time.sleep(2.0)
    return clicked


def jj_tombstone_reasons(user_data: Path) -> list[str]:
    """Every degrade reason the ai-backend recorded, newest last."""

    log = user_data / "logs" / "ai-backend.log"
    if not log.is_file():
        return []
    reasons: list[str] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "workspace_effect.tombstone "
        if marker in line:
            reasons.append(line.split(marker, 1)[1].strip().rstrip('"}'))
    return reasons


def jj_boot(
    *, provider: str, key: str, folder: Path, send_write: bool
) -> dict[str, Any]:
    """One full boot. Returns what that boot observed."""

    observed: dict[str, Any] = {}
    # INSTALLED PAYLOAD, not the source tree. C2 write authority requires a
    # signed-build attestation (`unsafe_dev_workspace_tcb` must be false and
    # the helper must pass Apple's designated-requirement check), which a
    # source launch cannot produce BY DESIGN. Proving writes therefore has to
    # happen against the artifact `copilot install` stages.
    session = DriverSession(name=NAME, fresh=False, installed_payload=True)
    with session:
        observed["target"] = session.rpc("status").get("target")
        # SETTLE FIRST, then branch. Probing `present()` the instant the driver
        # reports ready races the renderer: it answered False for a sign-in gate
        # that painted moments later, so the journey skipped sign-in and then
        # failed downstream looking for an FTUE card that was never going to
        # render. Three earlier failures were all this one bug wearing different
        # hats. Wait for SOME known screen before asking which one it is.
        assert session.wait_for(
            "[data-testid=sign-in-gate], .aui-folder-bar__attach", timeout_s=120
        ), "the app never reached a known screen"
        if session.present("[data-testid=sign-in-button]"):
            session.sign_in_local()
        else:
            observed["already_signed_in"] = True
        # Same rule: let the post-sign-in screen settle before deciding whether
        # a key still has to be added. The macOS keychain is MACHINE-scoped, so
        # a wiped userData does not imply a keyless install — "fresh install"
        # and "fresh profile" are different things.
        session.wait_for(
            "[data-testid=first-run-add-key], .aui-folder-bar__attach", timeout_s=120
        )
        # The key survives in the keychain, so boot 2 has no FTUE gate to fill.
        # A blanket try/except here is what made boot 1 fail SILENTLY at the
        # folder bar: it swallowed a real add-key failure and left the gate up.
        # So a failure is only tolerated when the composer is demonstrably
        # already past it.
        if not session.present(ATTACH):
            try:
                session.ftue_add_key(provider, key)
            except Exception:  # noqa: BLE001 — re-raised below unless benign
                if not session.wait_for(ATTACH, timeout_s=20):
                    raise
                observed["key_already_present"] = True
        assert session.wait_for(ATTACH, timeout_s=60), "no folder bar"
        jj_stub_dialogs(session, folder)
        session.click(ATTACH)
        time.sleep(2.5)
        observed["attached"] = session.evaluate(
            "(() => { const n = document.querySelector('.aui-folder-bar__name');"
            " return n ? n.textContent : null; })()"
        )

        session.send_first_run_message(
            WRITE_PROMPT if send_write else "Say READY and nothing else."
        )
        conversation_id = wait_for_conversation_id(session)
        run_id = wait_for_new_run(session, conversation_id, 0)
        observed["approvals_clicked"] = jj_approve_for(
            session, 90 if send_write else 20
        )
        settle_run(session, run_id, timeout_s=300)
        observed["tools"] = tool_calls(events(session, run_id))
    return observed


# ── the shipped artifact itself ──────────────────────────────────────────────
def ip1_the_installed_payload_boots(s: DriverSession) -> None:
    """The global npm payload launches its OWN Electron and reaches sign-in.

    The driver reports `target`, its CLI package root and the `payload/desktop`
    app directory through `status`; those are asserted before the DOM is
    touched, so a run that silently fell back to the checkout fails here rather
    than passing as an "installed" result.
    """

    status = s.rpc("status")
    assert status["target"] == "installed-payload", status
    assert status["cliPackageRoot"], status
    assert status["appDir"].endswith("payload/desktop"), status
    assert s.wait_for("[data-testid=sign-in-gate]"), (
        "installed payload did not reach the production sign-in gate"
    )
    s.shot("installed-payload-sign-in")
    log(f"cliPackageRoot={status['cliPackageRoot']}")


# ── the G3–G10 release matrix ────────────────────────────────────────────────
# Each is a one-line call because the originals already were: `run_matrix`
# owns preflight, the deterministic/live pass split, evidence and the verdict.
def _matrix(journey_id: str, slug: str, run_pass, *, needs_fixture: bool = False):
    def phase(_s: DriverSession) -> None:
        adopt_matrix(
            run_matrix(
                journey_id, slug, run_pass, argv=[], needs_fixture=needs_fixture
            ),
            journey_id,
        )

    return phase


# ── FS-J: two boots of ONE install ───────────────────────────────────────────
def ip10_the_adopted_principal_unlocks_writes(_s: DriverSession) -> None:
    """Boot 2 behaves differently from boot 1 BECAUSE boot 1 left a principal.

    That asymmetry is the mechanism, and it is why this phase owns its own two
    boots rather than sharing the file's: `resolveLocalPrincipal` writes
    `local-principal.json` the moment it ADOPTS, so the file is absent after
    boot 1 (nothing to adopt yet) and present after boot 2.

    Deliberately wipes its own userData first — a genuinely fresh install is
    the precondition, and the directory is this journey's alone.
    """

    preflight_staged_runtime(target=INSTALLED_PAYLOAD_TARGET)
    provider, key = byok_provider()

    nonce = uuid.uuid4().hex[:8]
    folder = Path.home() / ".0xcopilot-journey-fixtures" / f"fs-j-{nonce}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "seed.csv"
    target.write_text(SEED_CSV, encoding="utf-8")

    user_data = jj_user_data_dir()
    shutil.rmtree(user_data, ignore_errors=True)
    evidence: dict[str, Any] = {
        "user_data_dir": str(user_data),
        "granted_folder": str(folder),
        "digest_before": jj_digest(target),
    }

    with lane(ENFORCE_LANE):
        # Boot 1 — no principal exists; this boot is what creates one.
        evidence["boot1"] = jj_boot(
            provider=provider, key=key, folder=folder, send_write=False
        )
        evidence["boot1_reasons"] = jj_tombstone_reasons(user_data)
        principal = user_data / "local-principal.json"
        evidence["principal_after_boot1"] = (
            principal.read_text(encoding="utf-8") if principal.is_file() else None
        )
        # Boot 2 — the principal is now adoptable, so the cohort names it.
        evidence["boot2"] = jj_boot(
            provider=provider, key=key, folder=folder, send_write=True
        )
        evidence["boot2_reasons"] = jj_tombstone_reasons(user_data)
        evidence["principal_after_boot2"] = (
            principal.read_text(encoding="utf-8") if principal.is_file() else None
        )

    evidence["digest_after"] = jj_digest(target)
    from _lib import RUNS_DIR

    dump(RUNS_DIR / "installed-payload", "fs-j-evidence.json", evidence)

    newly = evidence["boot2_reasons"][len(evidence["boot1_reasons"]) :]
    admitted = not any("rollout_admission_denied" in reason for reason in newly)
    changed = evidence["digest_before"] != evidence["digest_after"]
    failures: list[str] = []
    if evidence["principal_after_boot1"] is not None:
        failures.append(
            "boot 1 already adopted a principal — there was nothing to adopt, so "
            "boot 2 proves nothing"
        )
    if evidence["principal_after_boot2"] is None:
        failures.append("boot 2 never adopted a principal")
    if not admitted:
        failures.append(f"boot 2 was still admission-denied: {newly}")
    if not changed:
        failures.append("the enforced write never changed the file on disk")
    if failures:
        raise AssertionError("; ".join(failures))


def main() -> int:
    plan = JourneyPlan("installed-payload")
    plan.boot(
        "installed-payload · fresh",
        lambda: DriverSession(name="installed-payload", installed_payload=True),
        phases=[
            (
                "IP-1",
                "the installed npm payload boots its own Electron",
                ip1_the_installed_payload_boots,
            ),
            (
                "IP-2",
                "G3 code artifact: inert review, exact patch, held stage",
                _matrix("G3", "g3-code-artifact", run_pass),
            ),
            (
                "IP-3",
                "G4 DOCX: binary preview/export, immutable revision",
                _matrix("G4", "g4-docx-artifact", g4_run_pass),
            ),
            (
                "IP-4",
                "G5 local email triage: fixture-only send + audit receipt",
                _matrix("G5", "g5-local-email-triage", g5_run_pass, needs_fixture=True),
            ),
            (
                "IP-5",
                "G6 local timeline: reject/restore then publish",
                _matrix("G6", "g6-local-x-timeline", g6_run_pass, needs_fixture=True),
            ),
            (
                "IP-6",
                "G7 local Discord: pinned announcement, idempotent retry",
                _matrix(
                    "G7",
                    "g7-local-discord-moderation",
                    g7_run_pass,
                    needs_fixture=True,
                ),
            ),
            (
                "IP-7",
                "G8 mixed work: two isolated staged effects",
                _matrix("G8", "g8-mixed-work", g8_run_pass, needs_fixture=True),
            ),
            (
                "IP-8",
                "G9 recovery and honesty: gate, unknown op, stale review, cancel",
                _matrix("G9", "g9-recovery-honesty", g9_run_pass, needs_fixture=True),
            ),
            (
                "IP-9",
                "G10 retention and reopen: replay, receipts, attributed usage",
                _matrix("G10", "g10-retention-reopen", g10_run_pass),
            ),
        ],
    )
    # FS-J owns its own boots (that IS its claim), so it runs outside the shared
    # one rather than inside it.
    plan.boot(
        "installed-payload · two boots of one install · ENFORCE lane",
        lambda: _NoSession(),
        phases=[
            (
                "IP-10",
                "the adopted principal unlocks the enforced workspace lane",
                ip10_the_adopted_principal_unlocks_writes,
            ),
        ],
    )
    return plan.finish()


class _NoSession:
    """A null session for a phase that launches its own app.

    FS-J's whole claim is that boot 2 differs from boot 1, so it cannot borrow
    a session — but the phase runner's isolation, ordering and reporting are
    still worth having, and they only need something context-manager shaped.
    """

    phase_prefix = ""
    run_dir = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
