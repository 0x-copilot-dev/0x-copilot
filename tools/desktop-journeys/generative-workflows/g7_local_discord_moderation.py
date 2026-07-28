#!/usr/bin/env python3
"""G7 — fixture Discord decision, pinned announcement, idempotent retry."""

from __future__ import annotations

import sys
import time
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


JOURNEY_ID: Final = "G7"
SLUG: Final = "g7-local-discord-moderation"
CHANNEL_TARGET: Final = f"{FIXTURE_NAMESPACE}/discord/channels/chn_launch_room"
PROMPT: Final = f"""Use only the installed Generative Workflows Local Fixture.
Call fixture_reset, list channels in the `Launch Week` fixture guild, and read
`#launch-room` at exact target `{CHANNEL_TARGET}`. Summarize the decision, then
draft exactly one pinned announcement to that channel mentioning exactly
`@maya` and `@leo`; its body must include `Studio` and `approval`. Hold it for
approval and do not publish. Never access a real Discord account or URL."""
AUDIT_PROMPT: Final = "Call fixture_audit only; do not draft or publish anything."


def _audit(session, conversation_id: str) -> dict:
    _, events = run_prompt(session, conversation_id, AUDIT_PROMPT)
    assert_fixture_operations(events, required=("fixture_audit",))
    return extract_fixture_audit(events)


def _wait_for_retry_surface(session) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        if session.present("[data-testid=tc-staged-draft-failed]"):
            return
        time.sleep(0.5)
    raise AssertionError("first Discord fixture failure did not render honestly")


def run_pass(config: PassConfig) -> None:
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
                run_id, events = run_prompt(session, conversation_id, PROMPT)
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
                _wait_for_retry_surface(session)
                retry_audit = _audit(session, conversation_id)
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
                final_audit = _audit(session, conversation_id)
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv, needs_fixture=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
