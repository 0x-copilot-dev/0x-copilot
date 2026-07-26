#!/usr/bin/env python3
"""G0 — real supervised Studio plain chat, deliberately without rich UI.

This is the first Generative Workflows release journey.  It launches the real
packaged, supervised Electron desktop stack in production posture, performs the
local sign-in and first-run BYOK flow through the UI, then asks one ordinary
knowledge question.  It passes only when both the desktop and the authenticated
facade agree that the run contains exactly one normal assistant answer and no
tool, surface, staged-write, artifact, or receipt activity.

The provider key is obtained only through ``load_env_key`` and sent only to the
real password field.  Its value is never printed, interpolated into diagnostics,
or used in a screenshot name.

Typical isolated-worktree invocation (the staged runtime and ignored .env may
live in the primary checkout):

    APP_DIR="$PWD/apps/desktop" \
    COPILOT_HOME=/path/to/enterprise-search/apps/desktop/resources \
    COPILOT_JOURNEY_DOTENV=/path/to/enterprise-search/services/ai-backend/.env \
      python3 tools/desktop-journeys/generative-workflows/g0_plain_chat.py

Set G0_PROVIDER=openai or G0_PROVIDER=anthropic to require one provider.
Without it, OpenAI is preferred and Anthropic is used when it is the only local
BYOK prerequisite available.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import DriverSession, REPO_ROOT, load_env_key  # noqa: E402


PROMPT = (
    "What is the difference between a Python tuple and a list? "
    "Answer in exactly three concise bullet points from your internal knowledge. "
    "Do not browse, call tools, read files, create artifacts, or make changes."
)

TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
)
# This is a positive grammar, not a tool-event denylist.  It mirrors the
# RuntimeApiEventType and RuntimeEventPresentationProjector contracts: a plain
# answer is its run/model/reasoning lifecycle, exactly one final response, then
# an optional E1 terminal receipt pair and completion.  No other transport or
# ledger type is valid in G0.
PLAIN_CHAT_EVENT_ACTIVITY_KINDS = {
    "run_queued": "run",
    "run_started": "run",
    "model_call_started": "run",
    "model_call_completed": "event",
    "model_delta": "message",
    "reasoning_summary": "reasoning",
    "reasoning_summary_delta": "reasoning",
    "final_response": "message",
    "surface.created": "event",
    "receipt.emitted": "event",
    "run_completed": "run",
}
# RuntimeEventEnvelope persists ``source`` plus the projector-owned
# ``parent_task_id``, ``task_id``, and ``subagent_id`` fields.  These are the
# production sources for the direct path: queue creation, lifecycle/receipt
# emitters, and direct model streaming.  A tool or delegated source must never
# be made to look like plain chat merely by projecting it as ``message``.
PLAIN_CHAT_EVENT_SOURCES = {
    "run_queued": "runtime",
    "run_started": "system",
    "model_call_started": "system",
    "model_call_completed": "model",
    "model_delta": "model",
    "reasoning_summary": "model",
    "reasoning_summary_delta": "model",
    "final_response": "system",
    "surface.created": "system",
    "receipt.emitted": "system",
    "run_completed": "system",
}
DELEGATED_PROVENANCE_FIELDS = (
    "parent_task_id",
    "task_id",
    "subagent_id",
)
PLAIN_CHAT_IN_FLIGHT_EVENT_TYPES = frozenset(
    {
        "model_call_started",
        "model_call_completed",
        "model_delta",
        "reasoning_summary",
        "reasoning_summary_delta",
    }
)

# These are intentionally stable product contracts, not text or CSS heuristics.
RICH_UI_SELECTORS = {
    "tool card": '[data-testid^="tc-chat-tool-"]',
    "subagent fleet card": '[data-testid^="tc-chat-fleet-"]',
    "surface tab strip": "[data-testid=tc-tabs]",
    "artifact frame": "[data-testid=artifact-frame]",
    "staged-write card": "[data-testid=effect-stage-card]",
    "staged-write approval bar": "[data-testid=tc-approve-bar]",
    "staged draft": "[data-testid=tc-staged-draft]",
    "staged row table": "[data-testid=tc-staged-table]",
    "workspace stage": "[data-testid=tc-workspace-stage]",
    "receipt launcher": "[data-testid=receipt-v2-launch]",
    "receipt surface": "[data-testid=receipt-v2-surface]",
}

ASSISTANT_SELECTOR = '[data-testid^="tc-chat-message-"][data-role="assistant"]'


class PreflightSkip(RuntimeError):
    """A documented missing package/provider prerequisite, never a test pass."""


def _skip(reason: str) -> int:
    print(f"SKIP G0: {reason}")
    return 0


def _host_runtime_key() -> str:
    platform_name = sys.platform
    if platform_name == "darwin":
        platform_name = "darwin"
    elif platform_name == "win32":
        platform_name = "win32"
    else:
        platform_name = sys.platform
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(
        machine, machine
    )
    return f"{platform_name}-{arch}"


def _source_app_dir() -> Path:
    return Path(os.environ.get("APP_DIR", REPO_ROOT / "apps" / "desktop"))


def _copilot_home() -> Path:
    return Path(
        os.environ.get("COPILOT_HOME", REPO_ROOT / "apps" / "desktop" / "resources")
    )


def _preflight_packaged_supervisor() -> None:
    """Permit SKIP only when an explicitly documented local prerequisite is absent."""

    if os.environ.get("COPILOT_DESKTOP_TEST_TARGET", "source") != "source":
        raise AssertionError(
            "G0 requires the source packaged-supervisor target; installed-payload "
            "has its own release journey and cannot prove this worktree's build"
        )
    if os.environ.get("COPILOT_FACADE_URL"):
        raise AssertionError(
            "G0 must not use COPILOT_FACADE_URL: it has to prove the embedded "
            "supervised stack, not an externally started facade"
        )

    app_dir = _source_app_dir()
    desktop_entry = app_dir / "out" / "main" / "index.js"
    if not desktop_entry.is_file():
        raise PreflightSkip(
            "desktop bundle is absent (run `npm run build --workspace "
            "@0x-copilot/desktop` or `make desktop-supervised`)"
        )

    runtime = _copilot_home() / "runtime" / _host_runtime_key()
    manifest_path = runtime / "staging-manifest.json"
    if not manifest_path.is_file():
        raise PreflightSkip(
            "host packaged runtime is absent (run `make desktop-supervised` or "
            "stage the host runtime with tools/desktop-runtime/stage.mjs)"
        )
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"staging manifest is malformed at {manifest_path}"
        ) from exc
    if manifest.get("host_exec") is not True:
        raise PreflightSkip(
            "staged runtime is not host-executable; re-stage it for this host "
            "with tools/desktop-runtime/stage.mjs"
        )
    required = (
        runtime / "python" / "bin" / "python3.13",
        runtime / "postgres" / "bin" / "postgres",
        runtime / "services" / "backend",
        runtime / "services" / "ai-backend",
        runtime / "services" / "backend-facade",
    )
    missing = [
        path.relative_to(runtime).as_posix() for path in required if not path.exists()
    ]
    if missing:
        raise PreflightSkip(
            "staged runtime is incomplete (missing " + ", ".join(missing) + ")"
        )


def _byok_provider() -> tuple[str, str]:
    requested = os.environ.get("G0_PROVIDER", "auto").strip().lower()
    if requested not in {"auto", "openai", "anthropic"}:
        raise AssertionError("G0_PROVIDER must be auto, openai, or anthropic")
    providers = (requested,) if requested != "auto" else ("openai", "anthropic")
    for provider in providers:
        try:
            return provider, load_env_key(provider)
        except SystemExit:
            # `load_env_key` never emits the value.  A missing local provider
            # credential is an allowed prerequisite skip, not a false success.
            continue
    label = requested if requested != "auto" else "OpenAI or Anthropic"
    raise PreflightSkip(
        f"no local {label} BYOK key is available through services/ai-backend/.env"
    )


def _assistant_count(session: DriverSession) -> int:
    return int(
        session.evaluate(
            f"document.querySelectorAll({json.dumps(ASSISTANT_SELECTOR)}).length"
        )
        or 0
    )


def _assistant_text(session: DriverSession) -> str:
    return str(
        session.evaluate(
            f"[...document.querySelectorAll({json.dumps(ASSISTANT_SELECTOR)})]"
            ".map((node) => node.innerText.trim()).join('\\n')"
        )
        or ""
    )


def _conversation_id(session: DriverSession) -> str:
    route = str(session.evaluate("window.location.hash") or "")
    match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", route)
    assert match is not None, f"expected a bound #/convo/<id> route, got {route!r}"
    return match.group(1)


def _wait_for_new_assistant(session: DriverSession, before: int) -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        count = _assistant_count(session)
        if count > before:
            return
        assert not session.present("[data-testid*=error]"), (
            "an error surface appeared while the plain-chat run was streaming"
        )
        time.sleep(0.5)
    raise AssertionError("no assistant answer appeared in the UI within 120 seconds")


def _run_for_conversation(session: DriverSession, conversation_id: str) -> str:
    listing = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/runs"
    )
    runs = listing.get("runs", [])
    assert isinstance(runs, list) and len(runs) == 1, (
        "fresh G0 conversation must have exactly one facade run; "
        f"got {len(runs) if isinstance(runs, list) else type(runs).__name__}"
    )
    run_id = runs[0].get("run_id")
    assert isinstance(run_id, str) and run_id, "facade run list omitted run_id"
    return run_id


def _wait_for_terminal_run(session: DriverSession, run_id: str) -> dict:
    deadline = time.time() + 120
    last: dict = {}
    while time.time() < deadline:
        result = session.transport("GET", f"/v1/agent/runs/{run_id}")
        assert isinstance(result, dict), "run inspection returned a non-object response"
        last = result
        status = result.get("status")
        if status in TERMINAL_STATUSES:
            assert status == "completed", (
                f"plain-chat run ended {status!r}: {result.get('safe_error')!r}"
            )
            return result
        time.sleep(0.5)
    raise AssertionError(
        f"run {run_id!r} never became terminal; last status={last.get('status')!r}"
    )


def _assert_no_rich_ui(session: DriverSession) -> None:
    leaked = [
        name
        for name, selector in RICH_UI_SELECTORS.items()
        if session.present(selector)
    ]
    assert not leaked, f"ordinary chat leaked rich Studio UI: {', '.join(leaked)}"


def _event_payload(event: dict, event_type: str) -> dict:
    payload = event.get("payload")
    assert isinstance(payload, dict), (
        f"plain chat {event_type!r} event has no object payload"
    )
    return payload


def _receipt_surface_id(payload: dict, event_type: str) -> str:
    surface_id = payload.get("surface_id")
    assert isinstance(surface_id, str) and surface_id, (
        f"plain chat {event_type!r} event has no receipt surface_id"
    )
    return surface_id


def _assert_direct_agent_provenance(event: dict, event_type: str) -> None:
    expected_source = PLAIN_CHAT_EVENT_SOURCES[event_type]
    assert event.get("source") == expected_source, (
        f"plain chat event {event_type!r} has source {event.get('source')!r}, "
        f"expected direct-agent source {expected_source!r}"
    )
    delegated_fields = [
        field for field in DELEGATED_PROVENANCE_FIELDS if event.get(field) is not None
    ]
    assert not delegated_fields, (
        f"plain chat event {event_type!r} has delegated provenance fields: "
        f"{delegated_fields!r}"
    )


def _assert_plain_chat_event_grammar(events: list[dict]) -> str | None:
    """Validate the only persisted sequence that represents a plain answer."""

    event_types: list[str] = []
    previous_sequence_no: int | None = None
    for index, event in enumerate(events):
        assert isinstance(event, dict), f"event replay item {index} is not an object"
        sequence_no = event.get("sequence_no")
        assert isinstance(sequence_no, int) and not isinstance(sequence_no, bool), (
            f"event replay item {index} has no integer sequence_no"
        )
        if previous_sequence_no is not None:
            assert sequence_no > previous_sequence_no, (
                "event replay is not strictly ordered by sequence_no: "
                f"{previous_sequence_no} then {sequence_no}"
            )
        previous_sequence_no = sequence_no

        event_type = event.get("event_type")
        assert isinstance(event_type, str), (
            f"event replay item {index} has no string event_type"
        )
        expected_activity_kind = PLAIN_CHAT_EVENT_ACTIVITY_KINDS.get(event_type)
        assert expected_activity_kind is not None, (
            f"plain chat event type is outside the grammar: {event_type!r}"
        )
        assert event.get("activity_kind") == expected_activity_kind, (
            f"plain chat event {event_type!r} has activity_kind "
            f"{event.get('activity_kind')!r}, expected {expected_activity_kind!r}"
        )
        _assert_direct_agent_provenance(event, event_type)
        event_types.append(event_type)

    assert event_types[0] == "run_queued", (
        f"plain chat must begin with run_queued, got {event_types[0]!r}"
    )
    assert len(event_types) > 1 and event_types[1] == "run_started", (
        "plain chat must start immediately after queuing"
    )

    index = 2
    saw_model_call = False
    model_call_open = False
    while index < len(event_types) and event_types[index] != "final_response":
        event_type = event_types[index]
        assert event_type in PLAIN_CHAT_IN_FLIGHT_EVENT_TYPES, (
            f"plain chat event {event_type!r} cannot appear before final_response"
        )
        if event_type == "model_call_started":
            assert not model_call_open, "plain chat opened a second model call"
            model_call_open = True
            saw_model_call = True
        elif event_type == "model_call_completed":
            assert model_call_open, (
                "plain chat completed a model call that never started"
            )
            model_call_open = False
        else:
            assert model_call_open, (
                f"plain chat emitted {event_type!r} outside a model call"
            )
        index += 1

    assert saw_model_call, "plain chat has no model-call lifecycle"
    assert index < len(event_types), "plain chat has no final_response"
    assert event_types[index] == "final_response"
    final_response_index = index
    index += 1

    receipt_surface_id: str | None = None
    if index < len(event_types) and event_types[index] == "surface.created":
        receipt_payload = _event_payload(events[index], "surface.created")
        assert receipt_payload.get("kind") == "receipt", (
            "plain chat may persist only an audit receipt surface"
        )
        receipt_surface_id = _receipt_surface_id(receipt_payload, "surface.created")
        index += 1
        assert index < len(event_types) and event_types[index] == "receipt.emitted", (
            "plain chat receipt.emitted must immediately follow surface.created"
        )
        emitted_id = _receipt_surface_id(
            _event_payload(events[index], "receipt.emitted"), "receipt.emitted"
        )
        assert emitted_id == receipt_surface_id, (
            "plain chat terminal receipt pair has mismatched surface ids"
        )
        index += 1

    assert index < len(event_types) and event_types[index] == "run_completed", (
        "plain chat must complete immediately after final_response or its receipt pair"
    )
    assert index == len(event_types) - 1, "plain chat has events after run_completed"
    assert final_response_index < index, "run_completed precedes final_response"
    return receipt_surface_id


def _assert_facade_plain_chat(session: DriverSession, run_id: str) -> None:
    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    assert replay.get("run_status") == "completed", (
        f"event replay does not report completed: {replay.get('run_status')!r}"
    )
    events = replay.get("events", [])
    assert isinstance(events, list) and events, (
        "completed run has no persisted event replay"
    )
    receipt_surface_id = _assert_plain_chat_event_grammar(events)

    surfaces = session.transport("GET", f"/v1/agent/runs/{run_id}/surfaces")
    assert surfaces.get("run_id") == run_id, (
        "surface projection is not bound to the verified run"
    )
    projected_surfaces = surfaces.get("surfaces")
    assert isinstance(projected_surfaces, list), "surfaces projection omitted its list"
    expected_projected_count = 1 if receipt_surface_id is not None else 0
    assert len(projected_surfaces) == expected_projected_count, (
        "plain chat receipt persistence and projection disagree: "
        f"expected {expected_projected_count} surface(s), got {len(projected_surfaces)}"
    )
    if receipt_surface_id is not None:
        projected_receipt = projected_surfaces[0]
        assert isinstance(projected_receipt, dict), (
            "plain chat projected receipt is not an object"
        )
        assert projected_receipt.get("kind") == "receipt", (
            "plain chat projected a non-receipt surface"
        )
        assert projected_receipt.get("surface_id") == receipt_surface_id, (
            "plain chat persisted and projected receipt surface ids differ"
        )


def main() -> int:
    try:
        _preflight_packaged_supervisor()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        return _skip(str(exc))

    # Deliberately show only metadata—not a key value, prompt, or reply text.
    print(f"G0 preflight: supervised source package ready; provider={provider}")
    with DriverSession(name="generative-workflows-g0-plain-chat") as session:
        status = session.rpc("status")
        assert status.get("target") == "source", (
            f"wrong driver target: {status.get('target')!r}"
        )
        assert status.get("posture") == "prod", (
            f"wrong desktop posture: {status.get('posture')!r}"
        )

        session.sign_in_local()
        session.shot("g0-sign-in")
        session.ftue_add_key(provider, key)
        session.shot("g0-byok-composer")

        catalog = session.transport("GET", "/v1/agent/models")
        models = catalog.get("models", [])
        assert any(
            isinstance(model, dict)
            and model.get("provider") == provider
            and model.get("configured") is True
            for model in models
        ), (
            f"authenticated facade model catalog did not configure {provider} after BYOK entry"
        )

        before = _assistant_count(session)
        assert before == 0, (
            f"fresh first-run composer unexpectedly already has {before} assistant turns"
        )
        session.send_first_run_message(PROMPT)
        _wait_for_new_assistant(session, before)
        conversation_id = _conversation_id(session)
        run_id = _run_for_conversation(session, conversation_id)
        _wait_for_terminal_run(session, run_id)

        answer_count = _assistant_count(session)
        assert answer_count == 1, (
            f"expected exactly one assistant answer in UI, got {answer_count}"
        )
        assert _assistant_text(session), "assistant UI turn is empty"
        _assert_no_rich_ui(session)
        _assert_facade_plain_chat(session, run_id)
        session.shot("g0-plain-answer-no-rich-ui")

    print(
        "PASS G0: supervised plain chat answered once with no tool or rich-surface leak"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
