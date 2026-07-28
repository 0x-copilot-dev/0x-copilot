#!/usr/bin/env python3
"""G5 — fixture-only inbox read, edited reply, approval, and audit receipt."""

from __future__ import annotations

import sys
from typing import Final

from _g3_g10_support import (
    FIXTURE_NAMESPACE,
    PassConfig,
    audit_operations,
    assert_event_types,
    assert_fixture_operations,
    assert_no_plaintext_secret,
    assert_receipt,
    bootstrap_session,
    create_conversation,
    edit_staged_draft,
    effect_stages,
    extract_fixture_audit,
    fixture_tool_payloads,
    journey_environment,
    new_session,
    register_local_fixture,
    replay_events,
    run_matrix,
    run_prompt,
    scope_fixture_conversation,
    wait_for_stage_terminal,
)


JOURNEY_ID: Final = "G5"
SLUG: Final = "g5-local-email-triage"
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


def run_pass(config: PassConfig) -> None:
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv, needs_fixture=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
