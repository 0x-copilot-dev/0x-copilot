#!/usr/bin/env python3
"""G9 — fixture gate recovery, honest unknown operation, stale review, cancel."""

from __future__ import annotations

import json
import sys
from typing import Any, Final

from _g3_g10_support import (
    FIXTURE_WORKSPACE_ROOT,
    EffectStage,
    PassConfig,
    assert_event_types,
    assert_fixture_operations,
    assert_no_plaintext_secret,
    assert_stage_decision,
    audit_operations,
    bootstrap_session,
    create_conversation,
    effect_stages,
    event_payload,
    extract_fixture_audit,
    journey_environment,
    new_session,
    register_local_fixture,
    replay_events,
    run_matrix,
    run_prompt,
    scope_fixture_conversation,
    submit_prompt,
    transport_error_status,
    transport_json,
    wait_for_stage_terminal,
    wait_for_terminal_run,
)


JOURNEY_ID: Final = "G9"
SLUG: Final = "g9-recovery-honesty"
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
STAGE_PROMPT: Final = f"""Using only the fixture, stage a held revision of
`project-brief.md` at `{BRIEF_TARGET}` that appends exactly:

Recovery review remains pending.

Do not commit it."""
CANCEL_PROMPT: Final = """Begin a detailed streaming comparison of every local
fixture domain. Do not call a write tool. Keep the answer in progress until
the user cancels it."""


def _wait_for_gate(session) -> str:
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


def _assert_same_parked_operation(events: list[dict[str, Any]]) -> None:
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


def _revised_stage(
    first: EffectStage,
    all_events: list[dict[str, Any]],
) -> EffectStage:
    stages = effect_stages(all_events)
    assert len(stages) == 1 and stages[0].stage_id == first.stage_id
    revised = stages[0]
    assert revised.revision == first.revision + 1
    return revised


def _cancel_stream(session, conversation_id: str) -> tuple[str, list[dict[str, Any]]]:
    run_id = submit_prompt(session, conversation_id, CANCEL_PROMPT)
    cancelled = transport_json(session, "POST", f"/v1/agent/runs/{run_id}/cancel")
    assert isinstance(cancelled, dict)
    wait_for_terminal_run(session, run_id, expected="cancelled")
    events = replay_events(session, run_id)
    assert_event_types(events, required=("run_cancelled",))
    return run_id, events


def run_pass(config: PassConfig) -> None:
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
                gate_id = _wait_for_gate(session)
                session.shot(f"g9-{config.mode.value}-expired-grant-gate")
                session.click("[data-testid=tc-gate-connect]")
                wait_for_terminal_run(session, read_run)
                read_events = replay_events(session, read_run)
                _assert_same_parked_operation(read_events)
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
                    session, conversation_id, STAGE_PROMPT
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
                current = _revised_stage(first, [*stage_events, *revise_events])
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

                _, cancel_events = _cancel_stream(session, conversation_id)
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


def main(argv: list[str] | None = None) -> int:
    return run_matrix(JOURNEY_ID, SLUG, run_pass, argv=argv, needs_fixture=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
