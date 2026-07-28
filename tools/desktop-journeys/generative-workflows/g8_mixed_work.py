#!/usr/bin/env python3
"""G8 — fixture mail/Discord reads with two isolated staged artifacts."""

from __future__ import annotations

import json
import sys
from typing import Final

from _g3_g10_support import (
    FIXTURE_WORKSPACE_ROOT,
    PassConfig,
    audit_operations,
    assert_fixture_operations,
    assert_no_plaintext_secret,
    assert_receipt,
    bootstrap_session,
    create_conversation,
    effect_stages,
    extract_fixture_audit,
    journey_environment,
    new_session,
    register_local_fixture,
    run_matrix,
    run_prompt,
    scope_fixture_conversation,
    wait_for_stage_terminal,
)


JOURNEY_ID: Final = "G8"
SLUG: Final = "g8-mixed-work"
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
PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
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


def _approve_from_rail(session, stage_id: str) -> None:
    selector = f'[data-testid="right-rail-approval-accept-{stage_id}"]'
    assert session.wait_for(selector), f"Approvals rail omitted stage {stage_id}"
    session.click(selector)


def run_pass(config: PassConfig) -> None:
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
                run_id, events = run_prompt(session, conversation_id, PROMPT)
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
                    _approve_from_rail(session, stage.stage_id)
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv, needs_fixture=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
