#!/usr/bin/env python3
"""G3 — code artifact, inert review, exact patch, and held workspace stage.

The live pass asks a real BYOK model to read two files from a fresh granted
workspace, publish the exact TypeScript patch as a code artifact, read it back,
and stage (never execute) the mutation.  The deterministic structure uses the
same installed Desktop and facade ledger with scripted built-in tool calls when
the supervised deterministic lane is available.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from _g3_g10_support import (
    ArtifactReference,
    EffectStage,
    JourneyMode,
    PassConfig,
    ThrowawayJourneyRoot,
    approve_native_workspace_stage,
    artifact_detail,
    artifact_from_events,
    assert_deterministic_model_attested,
    assert_event_types,
    assert_no_execution_bypass,
    assert_no_plaintext_secret,
    assert_receipt,
    assert_run_surfaces,
    bootstrap_session,
    create_conversation,
    effect_stages,
    event_operations,
    grant_workspace,
    journey_environment,
    new_session,
    open_artifact_from_sources,
    read_artifact_bytes,
    run_matrix,
    run_prompt,
    wait_for_stage_terminal,
)


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


def _deterministic_create_tools() -> list[dict[str, object]]:
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


def _assert_code_artifact(
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


def _assert_read_before_publish(events: list[dict[str, object]]) -> None:
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


def _create_phase(
    config: PassConfig,
    workspace: ThrowawayJourneyRoot,
) -> CreatedState:
    deterministic_tools = (
        _deterministic_create_tools()
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
                _assert_read_before_publish(events)
                artifact = artifact_from_events(events, kind="code")
                _assert_code_artifact(session, artifact, run_id)
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


def _read_stage_material(
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


def _assert_held_patch_surface(session, stage: EffectStage) -> None:
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


def _stage_phase(
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
                _read_stage_material(session, stage, created.artifact)
                _assert_held_patch_surface(session, stage)
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
        created = _create_phase(config, workspace)
        _stage_phase(config, workspace, created)


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
