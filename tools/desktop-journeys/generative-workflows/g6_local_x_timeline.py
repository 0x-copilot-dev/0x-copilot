#!/usr/bin/env python3
"""G6 — fixture timeline context, revised reply, reject/restore, and publish."""

from __future__ import annotations

import sys
from typing import Final

from _g3_g10_support import (
    FIXTURE_NAMESPACE,
    PassConfig,
    audit_operations,
    assert_fixture_operations,
    assert_no_plaintext_secret,
    assert_receipt,
    bootstrap_session,
    create_conversation,
    edit_staged_draft,
    effect_stages,
    extract_fixture_audit,
    journey_environment,
    new_session,
    register_local_fixture,
    replay_events,
    run_matrix,
    run_prompt,
    scope_fixture_conversation,
    wait_for_stage_terminal,
)


JOURNEY_ID: Final = "G6"
SLUG: Final = "g6-local-x-timeline"
POST_TARGET: Final = f"{FIXTURE_NAMESPACE}/timeline/posts/post_northstar_launch"
REVISED_REPLY: Final = (
    "A clear walkthrough shows the approval diff first, then the review "
    "decision and local receipt—calm, concrete, and reversible."
)
PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
Call fixture_reset, list the local timeline, and open Northstar's post at exact
target `{POST_TARGET}`. Draft a reply from the fixture account `@aria` that
answers `@northstar` and contains `approval` and `review`. Keep it held; do not
publish it. Never use a browser, X account, URL, or non-fixture destination."""
AUDIT_PROMPT: Final = """Call fixture_audit on the same local fixture and make
no other call or change."""


def _audit(session, conversation_id: str) -> dict:
    _, events = run_prompt(session, conversation_id, AUDIT_PROMPT)
    assert_fixture_operations(events, required=("fixture_audit",))
    return extract_fixture_audit(events)


def run_pass(config: PassConfig) -> None:
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
                run_id, events = run_prompt(session, conversation_id, PROMPT)
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

                revision = edit_staged_draft(session, REVISED_REPLY)
                stages = effect_stages(replay_events(session, run_id))
                assert len(stages) == 1
                stage = stages[0]
                assert stage.executor == "mcp" and stage.revision == revision
                session.shot(f"g6-{config.mode.value}-revised-post-diff")

                session.click("[data-testid=tc-approve-bar-reject]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="reject", applied=False
                )
                rejected_audit = _audit(session, conversation_id)
                assert "timeline.publish_draft" not in audit_operations(rejected_audit)
                assert session.wait_for("[data-testid=tc-approve-bar-restore]")
                session.shot(f"g6-{config.mode.value}-rejected-unchanged")

                session.click("[data-testid=tc-approve-bar-restore]")
                assert session.wait_for("[data-testid=tc-approve-bar-approve]")
                session.click("[data-testid=tc-approve-bar-approve]")
                wait_for_stage_terminal(
                    session, run_id, stage, decision="approve", applied=True
                )
                final_audit = _audit(session, conversation_id)
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv, needs_fixture=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
