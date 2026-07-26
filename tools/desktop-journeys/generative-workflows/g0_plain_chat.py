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
TOOL_EVENT_TYPES = frozenset(
    {
        "tool_call",
        "tool_call_started",
        "tool_call_delta",
        "tool_result",
        "tool_call_completed",
        "mcp_auth_required",
        "source_ingested",
        "sources_ingested",
        "citation_made",
    }
)
TOOL_EXECUTION_V2_EVENT_TYPES = frozenset(
    {
        "action.classified",
        "read.executed",
        "gate.opened",
        "gate.resolved",
    }
)
# These closed lifecycle events are emitted only after the supervisor's ``task``
# tool dispatches subagent work.  Keep this explicit rather than rejecting every
# event whose name or metadata mentions a subagent: ordinary model/run telemetry
# remains valid for G0 when it is not task execution.
SUBAGENT_TASK_EXECUTION_EVENT_TYPES = frozenset(
    {
        "subagent_update",
        "subagent_started",
        "subagent_progress",
        "subagent_completed",
        "subagent_fleet_started",
        "subagent_fleet_finished",
        "subagent_paused",
        "subagent_resumed",
    }
)
RICH_EVENT_TYPES = frozenset(
    {
        "view.derived",
        "view.preference",
        "shape.requested",
        "shape.resolved",
        "write.staged",
        "revision.added",
        "decision.recorded",
        "write.applied",
        "artifact.created",
        "artifact.revised",
        "artifact.promoted",
        "artifact.presentation_decided",
        "operation.requested",
        "operation.classified",
        "operation.completed",
        "operation.failed",
        "effect.staged",
        "effect.revised",
        "effect.decision_recorded",
        "effect.claimed",
        "effect.applied",
        "effect.indeterminate",
        "effect.reconciled",
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


def _assert_facade_plain_chat(session: DriverSession, run_id: str) -> None:
    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    assert replay.get("run_status") == "completed", (
        f"event replay does not report completed: {replay.get('run_status')!r}"
    )
    events = replay.get("events", [])
    assert isinstance(events, list) and events, (
        "completed run has no persisted event replay"
    )
    event_types = [event.get("event_type") for event in events]
    final_responses = [
        event for event in events if event.get("event_type") == "final_response"
    ]
    assert len(final_responses) == 1, (
        f"plain chat must persist exactly one final_response, got {len(final_responses)}"
    )
    legacy_tool_events = [
        event_type for event_type in event_types if event_type in TOOL_EVENT_TYPES
    ]
    assert not legacy_tool_events, (
        f"plain chat invoked legacy tool transport events: {legacy_tool_events!r}"
    )
    v2_tool_execution_events = [
        event_type
        for event_type in event_types
        if event_type in TOOL_EXECUTION_V2_EVENT_TYPES
    ]
    assert not v2_tool_execution_events, (
        f"plain chat persisted v2 tool execution events: {v2_tool_execution_events!r}"
    )
    subagent_task_execution_events = [
        event_type
        for event_type in event_types
        if event_type in SUBAGENT_TASK_EXECUTION_EVENT_TYPES
    ]
    assert not subagent_task_execution_events, (
        "plain chat dispatched subagent task execution events: "
        f"{subagent_task_execution_events!r}"
    )
    tool_activity = [
        event.get("event_type")
        for event in events
        if event.get("activity_kind") == "tool"
    ]
    assert not tool_activity, f"plain chat projected tool activity: {tool_activity!r}"
    subagent_activity = [
        event.get("event_type")
        for event in events
        if event.get("activity_kind") == "subagent"
    ]
    assert not subagent_activity, (
        f"plain chat projected subagent task activity: {subagent_activity!r}"
    )
    rich_events = [
        event_type for event_type in event_types if event_type in RICH_EVENT_TYPES
    ]
    assert not rich_events, (
        f"plain chat persisted rich-surface/effect events: {rich_events!r}"
    )

    # E1 deliberately seals every terminal run with an audit-only receipt pair:
    # ``surface.created {kind: receipt}`` and ``receipt.emitted``. That is not a
    # user-visible surface tab or receipt launcher (the UI assertion above proves
    # both absent). Any other surface kind is an ordinary-chat regression.
    receipt_surface_events = [
        event
        for event in events
        if event.get("event_type") == "surface.created"
        and event.get("payload", {}).get("kind") == "receipt"
    ]
    receipt_emitted_events = [
        event for event in events if event.get("event_type") == "receipt.emitted"
    ]
    assert len(receipt_surface_events) <= 1, (
        "plain chat persisted more than one terminal receipt surface: "
        f"{len(receipt_surface_events)}"
    )
    assert len(receipt_emitted_events) <= 1, (
        "plain chat persisted more than one terminal receipt emission: "
        f"{len(receipt_emitted_events)}"
    )
    assert len(receipt_surface_events) == len(receipt_emitted_events), (
        "plain chat terminal receipt is missing its matching surface/emission pair"
    )
    if receipt_surface_events:
        receipt_surface_id = (
            receipt_surface_events[0].get("payload", {}).get("surface_id")
        )
        receipt_emitted_id = (
            receipt_emitted_events[0].get("payload", {}).get("surface_id")
        )
        assert receipt_surface_id == receipt_emitted_id, (
            "plain chat terminal receipt pair has mismatched surface ids"
        )

    nonreceipt_surface_events = [
        event.get("payload", {}).get("kind")
        for event in events
        if event.get("event_type") == "surface.created"
        and event.get("payload", {}).get("kind") != "receipt"
    ]
    assert not nonreceipt_surface_events, (
        f"plain chat persisted a non-receipt surface: {nonreceipt_surface_events!r}"
    )

    surfaces = session.transport("GET", f"/v1/agent/runs/{run_id}/surfaces")
    assert surfaces.get("run_id") == run_id, (
        "surface projection is not bound to the verified run"
    )
    projected_surfaces = surfaces.get("surfaces")
    assert isinstance(projected_surfaces, list), "surfaces projection omitted its list"
    receipt_surfaces = [
        surface for surface in projected_surfaces if surface.get("kind") == "receipt"
    ]
    assert len(receipt_surfaces) <= 1, (
        "plain chat facade projection has more than one terminal receipt surface: "
        f"{len(receipt_surfaces)}"
    )
    nonreceipt_surfaces = [
        surface for surface in projected_surfaces if surface.get("kind") != "receipt"
    ]
    assert not nonreceipt_surfaces, (
        "plain chat facade projection has non-receipt surfaces: "
        f"{nonreceipt_surfaces!r}"
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
