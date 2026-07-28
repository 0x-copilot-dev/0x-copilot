#!/usr/bin/env python3
"""G4 — binary DOCX preview/export, immutable revision, and replacement stage.

This journey is fail-closed until the installed product exposes a binary DOCX
publication and preview contract.  The assertions beyond that preflight are the
release story: real DOCX bytes, a safe fixed preview plus fallback, export with
no source mutation, a title-only second revision, and an exact held workspace
replacement committed only after native approval.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import zipfile
from typing import Final

from _g3_g10_support import (
    ArtifactReference,
    PassConfig,
    ThrowawayJourneyRoot,
    approve_native_workspace_stage,
    artifact_detail,
    artifact_from_events,
    assert_event_types,
    assert_no_plaintext_secret,
    assert_receipt,
    assert_run_surfaces,
    bootstrap_session,
    create_conversation,
    effect_stages,
    grant_workspace,
    journey_environment,
    new_session,
    open_artifact_from_sources,
    read_artifact_bytes,
    run_matrix,
    run_prompt,
    wait_for_stage_terminal,
)


JOURNEY_ID: Final = "G4"
SLUG: Final = "g4-docx-artifact"
FILENAME: Final = "status-update.docx"
INITIAL_TITLE: Final = "Launch Status Update"
REVISED_TITLE: Final = "Launch Readiness Update"
BODY_TEXT: Final = (
    "Studio review is complete. The export workflow and approval walkthrough "
    "are ready for release review."
)
CREATE_PROMPT: Final = f"""Create a reviewable binary DOCX artifact named
`{FILENAME}`. Its document title must be exactly `{INITIAL_TITLE}` and its body
must be exactly `{BODY_TEXT}`. Publish it as a binary document artifact; do not
encode the DOCX as chat text or base64. Do not write a workspace file, browse,
use connectors, or claim an export."""

REVISE_PROMPT: Final = f"""Load the latest `{FILENAME}` artifact from this
conversation and create one new immutable DOCX revision. Change only the
document title from `{INITIAL_TITLE}` to `{REVISED_TITLE}`; preserve the exact
body and valid DOCX package. Do not replace or delete revision 1, export, write
the workspace, browse, or use connectors."""

STAGE_PROMPT: Final = f"""Load revision 2 of `{FILENAME}`, read back its exact
binary content and digest, then stage it as a held workspace replacement for
`/workspace/{FILENAME}`. Do not apply it, export it, browse, or use connectors."""


def _docx_text(content: bytes) -> str:
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


def _assert_docx(content: bytes, *, title: str) -> None:
    xml = _docx_text(content)
    assert title in xml, f"DOCX package omitted title {title!r}"
    assert BODY_TEXT in xml, "DOCX package omitted the exact status body"


def _assert_document_surface(session) -> None:
    assert session.wait_for("[data-testid=artifact-document-binary-preview]"), (
        "binary DOCX fixed preview did not render"
    )
    assert not session.present("iframe[src^='http']"), (
        "DOCX preview attempted a remote renderer"
    )
    assert session.present("[data-testid=artifact-document-safe-fallback]"), (
        "DOCX preview does not expose its safe fallback"
    )


def _export_and_assert_immutable(
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


def _current_revision(
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


def run_pass(config: PassConfig) -> None:
    with ThrowawayJourneyRoot(JOURNEY_ID, config.mode) as workspace:
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
                        session, conversation_id, CREATE_PROMPT
                    )
                    assert_event_types(
                        create_events,
                        required=("artifact.created",),
                        forbidden=("effect.staged", "effect.applied"),
                    )
                    first = artifact_from_events(create_events, kind="document")
                    first_bytes = read_artifact_bytes(session, first)
                    _assert_docx(first_bytes, title=INITIAL_TITLE)
                    open_artifact_from_sources(session)
                    _assert_document_surface(session)
                    assert_run_surfaces(
                        session, create_run, required_kinds={"artifact"}
                    )
                    session.shot(f"g4-{config.mode.value}-docx-preview")

                    _export_and_assert_immutable(session, workspace, first, first_bytes)
                    session.shot(f"g4-{config.mode.value}-docx-export")

                    revise_run, revise_events = run_prompt(
                        session, conversation_id, REVISE_PROMPT
                    )
                    assert_event_types(
                        revise_events,
                        required=("artifact.revised",),
                        forbidden=("effect.staged", "effect.applied"),
                    )
                    second = _current_revision(session, first.artifact_id, first)
                    assert second.revision == first.revision + 1
                    second_bytes = read_artifact_bytes(session, second)
                    _assert_docx(second_bytes, title=REVISED_TITLE)
                    assert read_artifact_bytes(session, first) == first_bytes, (
                        "revision switch mutated immutable DOCX revision 1"
                    )
                    assert (
                        hashlib.sha256(second_bytes).hexdigest()
                        == second.content_digest
                    )
                    session.shot(f"g4-{config.mode.value}-immutable-version-switch")

                    stage_run, stage_events = run_prompt(
                        session, conversation_id, STAGE_PROMPT
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(
        JOURNEY_ID,
        SLUG,
        run_pass,
        argv=argv,
        needs_docx=True,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
