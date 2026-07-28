#!/usr/bin/env python3
"""G10 — finish document/dataset effects, reopen, replay, and inspect usage."""

from __future__ import annotations

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
    artifact_from_events,
    assert_deterministic_model_attested,
    assert_event_types,
    assert_main_production_posture,
    assert_no_execution_bypass,
    assert_no_plaintext_secret,
    assert_receipt,
    bootstrap_session,
    create_conversation,
    effect_stages,
    grant_workspace,
    journey_environment,
    new_session,
    read_artifact_bytes,
    replay_events,
    run_matrix,
    run_prompt,
    wait_for_stage_terminal,
)


JOURNEY_ID: Final = "G10"
SLUG: Final = "g10-retention-reopen"
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
CREATE_PROMPT: Final = f"""Create exactly two reviewable artifacts in Studio:

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


def _create_artifacts(
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
                run_id, events = run_prompt(session, conversation_id, CREATE_PROMPT)
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


def _stage_and_apply(
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


def _assert_no_pending(
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


def _assert_usage(
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


def _reopen_and_verify(
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
                _assert_no_pending(session, created, applied)
                retention = session.transport("GET", "/v1/retention/effective")
                effective = retention.get("effective")
                assert isinstance(effective, dict) and effective, (
                    "reopened conversation has no facade retention truth"
                )
                _assert_usage(session, config, created, applied)
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


def run_pass(config: PassConfig) -> None:
    run_dirs: list[Path] = []
    with ThrowawayJourneyRoot(JOURNEY_ID, config.mode) as workspace:
        workspace.seed_text(MARKDOWN_PATH, INITIAL_MARKDOWN)
        workspace.seed_text(CSV_PATH, INITIAL_CSV)
        created = _create_artifacts(config, workspace, run_dirs)
        markdown = _stage_and_apply(
            config,
            workspace,
            created,
            phase="markdown-stage",
            relative_path=MARKDOWN_PATH,
            content=FINAL_MARKDOWN,
            artifact=created.markdown,
            run_dirs=run_dirs,
        )
        dataset = _stage_and_apply(
            config,
            workspace,
            created,
            phase="dataset-stage",
            relative_path=CSV_PATH,
            content=FINAL_CSV,
            artifact=created.dataset,
            run_dirs=run_dirs,
        )
        _reopen_and_verify(config, workspace, created, (markdown, dataset), run_dirs)


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
