#!/usr/bin/env python3
"""G1 — real supervised Desktop Markdown lifecycle.

This release journey is intentionally a live product journey: it launches the
installed, supervised Electron payload because its production workspace
authority is deliberately packaged-only. It enters a real local BYOK value
through the first-run UI, then asks the agent to create and revise a Markdown
artifact. It never seeds a run, writes an effect through the facade, or
substitutes mock transport. The only host workspace is a new
``TemporaryDirectory`` created by this process. The app's Electron-main
capability service grants that folder through its normal native picker and is
the only authority that can write ``brief.md`` after the exact staged revision
is approved.

The native picker and confirmation are Electron sheets, outside Playwright's
DOM.  On macOS the journey drives those two sheets with a narrowly scoped
AppleScript process.  The workspace path is an argv value (not interpolated
into source or a shell command), while the app continues to own grant creation,
approval receipt validation, permit hand-off, and the physical write.

Do not run this as part of unit tests.  It makes one real BYOK model request
when its local prerequisites are present.  The hermetic tests in
``tests/test_g1_markdown_lifecycle.py`` cover all local helpers without a
provider, Electron, native dialog, or host-file write.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import DriverSession, load_env_key  # noqa: E402


JOURNEY_ID: Final = "G1"
ARTIFACT_NAME: Final = "brief.md"
APP_PROCESS_NAME: Final = "0xCopilot"
INSTALLED_PAYLOAD_TARGET: Final = "installed-payload"
TERMINAL_STATUSES: Final = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
)
ARTIFACT_EVENTS: Final = frozenset({"artifact.created", "artifact.revised"})
WORKSPACE_STAGE_EVENTS: Final = frozenset(
    {
        "effect.staged",
        "effect.revised",
        "effect.decision_recorded",
        "effect.claimed",
        "effect.applied",
        "effect.indeterminate",
        "effect.reconciled",
    }
)
WRITE_APPLIED_EVENTS: Final = frozenset({"write.applied", "effect.applied"})
READ_EVENTS: Final = frozenset({"read.executed", "tool_result", "tool_call_completed"})
ALLOWED_CAPABILITIES: Final = frozenset({"workspace", "artifact"})
UNRELATED_TOOL_MARKERS: Final = frozenset(
    {
        "web_search",
        "browser",
        "mail",
        "discord",
        "timeline",
        "slack",
        "gmail",
        "twitter",
        "x.com",
    }
)
SECRET_ENVIRONMENT_NAMES: Final = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
)
DEV_OVERRIDE_ENVIRONMENT_NAMES: Final = (
    "COPILOT_DEV",
    "COPILOT_AUTH_MODE",
    "COPILOT_DEV_PERSONA",
)
RELEASE_RUNNER_ENVIRONMENT_NAMES: Final = ("CI", "G1_RELEASE", "G1_RELEASE_MODE")
WORKSPACE_MATERIAL_PREFIX: Final = "workspace-material://sha256/"
ARTIFACT_BLOB_PREFIX: Final = "artifact-blob://sha256/"
WORKSPACE_JOURNAL_CIPHER_MARKER: Final = b"COPILOT_WORKSPACE_JOURNAL_V1:cipher:"
WORKSPACE_JOURNAL_PLAINTEXT_MARKER: Final = b"COPILOT_WORKSPACE_JOURNAL_V1:plaintext:"
NATIVE_CLAIM_RECORD: Final = re.compile(r"c2c-[0-9a-f]{64}$")

# These are the existing legacy gates that raise the immutable E2 workspace
# lanes.  Do not add an explicit E2 mode here: that would conflict with the
# private broker the real Electron main process publishes at boot.
JOURNEY_ENVIRONMENT: Final = {
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM": "1",
    "SURFACES_V2": "true",
    "ARTIFACT_EFFECTS_V2": "true",
    "ARTIFACT_DRAFTS_V2": "true",
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}

CREATE_PROMPT: Final = """Create a reviewable Markdown document artifact named `brief.md`.
Use a clear heading and one concise paragraph about a staged Desktop workspace
review. Keep it editable in Studio. Do not apply or claim any local workspace
change, do not browse, and do not use connectors or unrelated tools."""

STAGE_PROMPT: Final = """Load the latest user-edited `brief.md` artifact from this Studio
conversation. Use the exact current artifact content to create a new staged
workspace revision for `brief.md` in the granted local workspace. Keep it held
for explicit review and approval only: do not apply it, do not claim success,
do not browse, and do not use connectors or unrelated tools."""

USER_EDITED_MARKDOWN: Final = (
    "# Reviewed Desktop Brief\n\n"
    "This paragraph was edited in Studio and must remain reviewable before "
    "the Electron-main workspace authority writes it.\n"
)


class PreflightSkip(RuntimeError):
    """A documented local prerequisite is absent; this is never a pass."""


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    revision: int
    kind: str
    content_ref: str
    content_digest: str


@dataclass(frozen=True)
class WorkspaceStage:
    stage_id: str
    revision: int
    proposal_digest: str
    target_digest: str
    proposal_content_ref: str
    display_target: str


@dataclass(frozen=True)
class MainWorkspaceJournalSnapshot:
    """Opaque, main-owned commit evidence; journal payloads stay unread."""

    journal_digest: str | None
    native_claim_records: frozenset[str]


class FixtureWorkspace:
    """One empty local workspace whose cleanup is unconditional and scoped."""

    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    @property
    def brief_path(self) -> Path:
        if self.root is None:
            raise RuntimeError("fixture workspace has not been entered")
        return self.root / ARTIFACT_NAME

    def __enter__(self) -> "FixtureWorkspace":
        self._temporary = tempfile.TemporaryDirectory(prefix="0xcopilot-g1-")
        self.root = Path(self._temporary.name)
        return self

    def __exit__(self, *_: object) -> None:
        temporary, self._temporary = self._temporary, None
        self.root = None
        if temporary is not None:
            temporary.cleanup()


def _structured_result(outcome: str, *, reason: str | None = None) -> None:
    payload: dict[str, str] = {"journey": JOURNEY_ID, "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True))


def _skip(reason: str) -> int:
    _structured_result("skipped", reason=reason)
    return 0


def _is_release_runner(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return any(
        source.get(name, "").strip().lower() in {"1", "true", "yes", "on", "release"}
        for name in RELEASE_RUNNER_ENVIRONMENT_NAMES
    )


def _prerequisite_result(reason: str) -> int:
    """Manual runs may report SKIP; release runners must fail closed."""

    if _is_release_runner():
        _structured_result("failed", reason=f"unmet prerequisite: {reason}")
        return 2
    return _skip(reason)


def _host_runtime_key() -> str:
    platform_name = sys.platform
    machine = platform.machine().lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(machine, machine)
    return f"{platform_name}-{arch}"


def _copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", Path.home() / ".0xcopilot"))


def _preflight_packaged_supervisor() -> None:
    """Skip only a missing package/runtime prerequisite; all other faults fail."""

    target = os.environ.get("COPILOT_DESKTOP_TEST_TARGET", INSTALLED_PAYLOAD_TARGET)
    if target != INSTALLED_PAYLOAD_TARGET:
        raise AssertionError(
            "G1 requires COPILOT_DESKTOP_TEST_TARGET=installed-payload because "
            "Electron-main workspace authority is packaged-only"
        )
    if os.environ.get("APP_DIR"):
        raise AssertionError(
            "G1 must not set APP_DIR; the installed payload must own its Electron "
            "application path"
        )
    if os.environ.get("COPILOT_FACADE_URL"):
        raise AssertionError(
            "G1 must not use COPILOT_FACADE_URL; it requires Electron's embedded "
            "supervised facade"
        )

    runtime = _copilot_home() / "runtime" / _host_runtime_key()
    manifest_path = runtime / "staging-manifest.json"
    if not manifest_path.is_file():
        raise PreflightSkip(
            "host staged runtime is absent (run `make desktop-supervised` or stage "
            "the host runtime with tools/desktop-runtime/stage.mjs)"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"staging manifest is malformed at {manifest_path}"
        ) from exc
    if manifest.get("host_exec") is not True:
        raise PreflightSkip(
            "staged runtime is not host-executable; re-stage with "
            "tools/desktop-runtime/stage.mjs"
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
    requested = os.environ.get("G1_PROVIDER", "auto").strip().lower()
    if requested not in {"auto", "openai", "anthropic"}:
        raise AssertionError("G1_PROVIDER must be auto, openai, or anthropic")
    providers = (requested,) if requested != "auto" else ("openai", "anthropic")
    for provider in providers:
        try:
            return provider, load_env_key(provider)
        except SystemExit:
            # ``load_env_key`` reports only a variable/path, never its value.
            continue
    provider_label = requested if requested != "auto" else "OpenAI or Anthropic"
    raise PreflightSkip(
        f"no local {provider_label} BYOK key is available through "
        "services/ai-backend/.env"
    )


def _sanitize_supervised_production_environment(env: MutableMapping[str, str]) -> None:
    """Remove inherited dev switches before the driver copies its environment."""

    env.pop("COPILOT_DEV", None)
    if env.get("COPILOT_AUTH_MODE", "").strip().lower() == "dev-mint":
        env.pop("COPILOT_AUTH_MODE", None)
    env.pop("COPILOT_DEV_PERSONA", None)
    env["COPILOT_PRODUCTION"] = "1"


@contextmanager
def _journey_environment() -> Iterator[None]:
    """Enable workspace production mode without exporting provider keys or dev flags."""

    changed = (
        set(JOURNEY_ENVIRONMENT)
        | set(SECRET_ENVIRONMENT_NAMES)
        | set(DEV_OVERRIDE_ENVIRONMENT_NAMES)
        | {"COPILOT_PRODUCTION"}
    )
    previous = {name: os.environ.get(name) for name in changed}
    os.environ.update(JOURNEY_ENVIRONMENT)
    for name in SECRET_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    _sanitize_supervised_production_environment(os.environ)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# The app owns both native dialogs.  This helper only types the *already
# created* temporary fixture path into the folder picker.  It never invokes a
# shell, reaches another process, or supplies a host path to Electron IPC.
_FOLDER_PICKER_APPLESCRIPT: Final = r"""
on waitForSheet(processName, attempts)
  tell application "System Events"
    tell process processName
      repeat with attempt from 1 to attempts
        if exists sheet 1 of window 1 then return true
        delay 0.1
      end repeat
    end tell
  end tell
  return false
end waitForSheet

on run argv
  set fixtureRoot to item 1 of argv
  set processName to item 2 of argv
  if my waitForSheet(processName, 200) is false then error "folder picker did not appear"
  tell application "System Events"
    tell process processName
      set frontmost to true
      keystroke "g" using {command down, shift down}
      delay 0.2
      keystroke fixtureRoot
      key code 36
      delay 0.4
      key code 36
    end tell
  end tell
end run
"""

_APPROVAL_APPLESCRIPT: Final = r"""
on waitForSheet(processName, attempts)
  tell application "System Events"
    tell process processName
      repeat with attempt from 1 to attempts
        if exists sheet 1 of window 1 then return true
        delay 0.1
      end repeat
    end tell
  end tell
  return false
end waitForSheet

on run argv
  set processName to item 1 of argv
  if my waitForSheet(processName, 200) is false then error "workspace confirmation did not appear"
  tell application "System Events"
    tell process processName
      click button "Approve" of sheet 1 of window 1
    end tell
  end tell
end run
"""


def _folder_picker_command(
    root: Path, process_name: str = APP_PROCESS_NAME
) -> list[str]:
    return [
        "/usr/bin/osascript",
        "-e",
        _FOLDER_PICKER_APPLESCRIPT,
        "--",
        str(root),
        process_name,
    ]


def _approval_command(process_name: str = APP_PROCESS_NAME) -> list[str]:
    return [
        "/usr/bin/osascript",
        "-e",
        _APPROVAL_APPLESCRIPT,
        "--",
        process_name,
    ]


def _start_native_automation(command: list[str]) -> subprocess.Popen[bytes]:
    if sys.platform != "darwin":
        raise AssertionError("G1 native workspace approval requires macOS")
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_native_automation(
    process: subprocess.Popen[bytes], *, action: str, timeout_s: int = 30
) -> None:
    try:
        exit_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError(f"native {action} automation timed out") from exc
    if exit_code != 0:
        raise AssertionError(
            f"native {action} automation failed; no folder grant or approval was assumed"
        )


def _evaluate_json(session: DriverSession, javascript: str) -> Any:
    raw = session.evaluate(javascript)
    assert isinstance(raw, str), "renderer IPC did not return JSON"
    if raw.startswith("ERR:"):
        raise AssertionError("renderer IPC rejected the journey action")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError("renderer IPC returned malformed JSON") from exc


def _ipc_invoke(
    session: DriverSession, channel: str, payload: Mapping[str, Any]
) -> Any:
    return _evaluate_json(
        session,
        "(async()=>{try{const value=await window.bridge.ipc.invoke("
        f"{json.dumps(channel)},{json.dumps(dict(payload))});"
        "return JSON.stringify(value);}catch(error){return 'ERR:'+error.message;}})()",
    )


def _assert_main_production_posture(session: DriverSession) -> None:
    """Read production posture from Electron main, never the driver label."""

    posture = _ipc_invoke(session, "auth.get-posture", {})
    assert isinstance(posture, dict), "Electron main did not return auth posture"
    assert set(posture) == {"productionPosture"}, (
        "Electron main posture response leaked fields or changed shape"
    )
    assert posture["productionPosture"] is True, (
        "Electron main is not enforcing production supervisor posture"
    )


def _grant_fixture_workspace(session: DriverSession, fixture: FixtureWorkspace) -> str:
    assert fixture.root is not None
    picker = _start_native_automation(_folder_picker_command(fixture.root))
    try:
        grant = _ipc_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": "read_write_no_delete", "label": "G1 fixture"},
        )
    finally:
        _wait_native_automation(picker, action="folder picker")

    assert isinstance(grant, dict), "folder picker did not yield a renderer grant"
    assert set(grant) == {"grantId", "mode", "label", "status"}, (
        "folder grant leaked an authority field or did not use the renderer-safe shape"
    )
    assert grant.get("mode") == "read_write_no_delete"
    assert grant.get("status") == "active"
    grant_id = grant.get("grantId")
    assert isinstance(grant_id, str) and grant_id, "folder grant omitted its id"

    grants = _ipc_invoke(session, "capability.list-grants", {})
    assert isinstance(grants, list) and len(grants) == 1, (
        "fresh G1 session must expose exactly one local fixture grant"
    )
    assert grants[0].get("grantId") == grant_id, "listed grant changed after creation"
    return grant_id


def _conversation_id(session: DriverSession) -> str:
    route = str(session.evaluate("window.location.hash") or "")
    match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", route)
    assert match is not None, f"expected a bound #/convo/<id> route, got {route!r}"
    return match.group(1)


def _runs_for_conversation(
    session: DriverSession, conversation_id: str
) -> list[dict[str, Any]]:
    listing = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/runs"
    )
    runs = listing.get("runs", [])
    assert isinstance(runs, list), "facade run list omitted its run array"
    assert all(isinstance(run, dict) for run in runs), "facade run list is malformed"
    return runs


def _wait_for_new_run(
    session: DriverSession, conversation_id: str, before_count: int
) -> str:
    deadline = time.time() + 120
    while time.time() < deadline:
        runs = _runs_for_conversation(session, conversation_id)
        if len(runs) > before_count:
            run_id = runs[0].get("run_id")
            assert isinstance(run_id, str) and run_id, "facade run omitted run_id"
            return run_id
        time.sleep(0.5)
    raise AssertionError("Studio did not create a run for the G1 prompt")


def _wait_for_terminal_run(session: DriverSession, run_id: str) -> dict[str, Any]:
    deadline = time.time() + 180
    last: dict[str, Any] = {}
    while time.time() < deadline:
        result = session.transport("GET", f"/v1/agent/runs/{run_id}")
        assert isinstance(result, dict), "run inspection returned a non-object response"
        last = result
        status = result.get("status")
        if status in TERMINAL_STATUSES:
            assert status == "completed", (
                f"G1 agent run ended {status!r}: {result.get('safe_error')!r}"
            )
            return result
        time.sleep(0.5)
    raise AssertionError(
        f"run did not become terminal; last status={last.get('status')!r}"
    )


def _events(session: DriverSession, run_id: str) -> list[dict[str, Any]]:
    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = replay.get("events", [])
    assert isinstance(events, list), "event replay omitted events"
    assert all(isinstance(event, dict) for event in events), "event replay is malformed"
    return events


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    assert isinstance(value, str) and value, f"event payload omitted {key}"
    return value


def _required_positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    assert isinstance(value, int) and value > 0, f"event payload omitted {key}"
    return value


def _required_digest(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    assert re.fullmatch(r"[0-9a-f]{64}", value), (
        f"event payload has an invalid immutable {key}"
    )
    return value


def _workspace_stages(events: list[dict[str, Any]]) -> list[WorkspaceStage]:
    staged: dict[str, WorkspaceStage] = {}
    for event in events:
        event_type = event.get("event_type")
        payload = _payload(event)
        if event_type == "effect.staged" and payload.get("executor") == "workspace":
            stage = WorkspaceStage(
                stage_id=_required_text(payload, "stage_id"),
                revision=1,
                proposal_digest=_required_digest(payload, "proposal_digest"),
                target_digest=_required_digest(payload, "target_digest"),
                proposal_content_ref=_required_text(payload, "proposal_content_ref"),
                display_target=_required_text(payload, "display_target"),
            )
            staged[stage.stage_id] = stage
        elif event_type == "effect.revised":
            stage_id = payload.get("stage_id")
            prior = staged.get(stage_id) if isinstance(stage_id, str) else None
            if prior is None:
                continue
            staged[stage_id] = WorkspaceStage(
                stage_id=prior.stage_id,
                revision=_required_positive_int(payload, "revision"),
                proposal_digest=_required_digest(payload, "proposal_digest"),
                target_digest=_required_digest(payload, "target_digest"),
                proposal_content_ref=_required_text(payload, "proposal_content_ref"),
                display_target=_required_text(payload, "display_target"),
            )
    return list(staged.values())


def _assert_workspace_stage_surface(
    session: DriverSession, stage: WorkspaceStage
) -> None:
    required = {
        "stage": "[data-testid=tc-workspace-stage]",
        "path": "[data-testid=tc-workspace-stage-path]",
        "revision": "[data-testid=tc-workspace-stage-revision]",
        "diff": "[data-testid=tc-workspace-stage-diff]",
        "edit": "[data-testid=tc-workspace-stage-edit]",
        "approve": "[data-testid=tc-workspace-stage-approve]",
    }
    missing = [
        name for name, selector in required.items() if not session.present(selector)
    ]
    assert not missing, f"G1 workspace stage is missing visible review UI: {missing}"
    path_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-path]").innerText'
        )
    )
    assert path_text == stage.display_target, (
        "workspace stage target does not exactly match its staged ledger target"
    )
    revision_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-revision]").innerText'
        )
    )
    assert revision_text == f"rev {stage.revision} · Agent", (
        "workspace stage UI is not pinned to the exact staged revision"
    )


def _read_staged_proposal_bytes(user_data_dir: Path, stage: WorkspaceStage) -> bytes:
    """Read the supervisor-owned immutable manifest, never a host workspace file."""

    assert (
        stage.proposal_content_ref == WORKSPACE_MATERIAL_PREFIX + stage.proposal_digest
    ), "workspace stage does not use its digest-pinned immutable proposal ref"
    proposal_path = (
        user_data_dir
        / "agent-data"
        / "v1"
        / "objects"
        / "sha256"
        / stage.proposal_digest[:2]
        / stage.proposal_digest
    )
    assert proposal_path.is_file() and not proposal_path.is_symlink(), (
        "supervised runtime did not persist the staged immutable proposal material"
    )
    proposal_bytes = proposal_path.read_bytes()
    assert hashlib.sha256(proposal_bytes).hexdigest() == stage.proposal_digest, (
        "staged proposal material bytes do not match the recorded proposal digest"
    )
    return proposal_bytes


def _assert_stage_binds_immutable_artifact(
    stage: WorkspaceStage,
    artifact: ArtifactReference,
    artifact_bytes: bytes,
    proposal_bytes: bytes,
) -> None:
    """Verify the stage manifest points at the exact edited artifact blob bytes."""

    assert hashlib.sha256(proposal_bytes).hexdigest() == stage.proposal_digest, (
        "staged proposal bytes do not match the approved immutable proposal digest"
    )
    assert hashlib.sha256(artifact_bytes).hexdigest() == artifact.content_digest, (
        "artifact stream bytes do not match the immutable edited revision digest"
    )
    try:
        material = json.loads(proposal_bytes)
    except json.JSONDecodeError as exc:
        raise AssertionError("staged immutable proposal is not JSON material") from exc
    assert isinstance(material, dict), "staged immutable proposal is not an object"
    assert material.get("target_digest") == stage.target_digest, (
        "staged proposal material is not bound to the reviewed target digest"
    )
    entries = material.get("entries")
    assert isinstance(entries, list) and len(entries) == 1, (
        "G1 staged proposal must contain exactly one workspace file entry"
    )
    entry = entries[0]
    assert isinstance(entry, dict), "staged proposal entry is not structured"
    assert entry.get("operation") == "create", (
        "G1 staged proposal is not the new brief.md create revision"
    )
    assert entry.get("relative_path") == ARTIFACT_NAME, (
        "G1 staged proposal targets a file other than brief.md"
    )
    assert entry.get("content_ref") == ARTIFACT_BLOB_PREFIX + artifact.content_digest, (
        "workspace stage entry is not bound to the edited immutable content ref"
    )
    assert entry.get("content_digest") == artifact.content_digest, (
        "workspace stage entry is not bound to the edited immutable content digest"
    )
    assert entry.get("content_size") == len(artifact_bytes), (
        "workspace stage entry byte size does not match the edited revision"
    )


def _assert_editor_surface(session: DriverSession) -> None:
    required = {
        "artifact frame": "[data-testid=artifact-frame]",
        "artifact editor": "[data-testid=artifact-editor]",
        "revision history": "[data-testid=artifact-revision-history]",
        "editor field": "#artifact-editor-text",
    }
    missing = [
        name for name, selector in required.items() if not session.present(selector)
    ]
    assert not missing, f"G1 artifact editor is missing: {missing}"


def _open_first_artifact_from_sources(session: DriverSession) -> None:
    if session.present("[data-testid=artifact-frame]"):
        return
    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Studio did not show the Sources provenance rail for the artifact"
    )
    assert session.present("[data-testid=sources-v2-open-artifact]"), (
        "artifact source is not user-openable from provenance"
    )
    session.click("[data-testid=sources-v2-open-artifact]")
    assert session.wait_for("[data-testid=artifact-frame]"), (
        "opening the artifact source did not render an artifact surface"
    )


def _read_artifact_bytes(session: DriverSession, artifact: ArtifactReference) -> bytes:
    """Read exact bytes through real main-process artifact streaming IPC."""

    javascript = f"""(async()=>{{
      const opened=await window.bridge.ipc.invoke("transport.artifact-content.open",{{
        artifactId:{json.dumps(artifact.artifact_id)},revision:{artifact.revision}
      }});
      const bytes=[];
      try {{
        for (;;) {{
          const next=await window.bridge.ipc.invoke("transport.artifact-content.read",{{handle:opened.handle}});
          if (next.done) break;
          if (next.chunk===null) throw new Error("empty artifact chunk");
          for (const value of next.chunk) {{
            bytes.push(value);
            if (bytes.length>131072) throw new Error("artifact exceeds G1 bound");
          }}
        }}
      }} finally {{
        await window.bridge.ipc.invoke("transport.artifact-content.close",{{handle:opened.handle}});
      }}
      let binary="";
      for (const value of bytes) binary+=String.fromCharCode(value);
      return btoa(binary);
    }})()"""
    raw = session.evaluate(javascript)
    assert isinstance(raw, str), "artifact stream did not return base64"
    try:
        return base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise AssertionError("artifact stream returned invalid base64") from exc


def _artifact_detail(session: DriverSession, artifact_id: str) -> dict[str, Any]:
    detail = session.transport("GET", f"/v1/agent/artifacts/{artifact_id}")
    assert isinstance(detail, dict), "artifact detail is malformed"
    return detail


def _assert_artifact_named_brief(detail: Mapping[str, Any]) -> None:
    artifact = detail.get("artifact")
    title = artifact.get("title") if isinstance(artifact, dict) else None
    filename = detail.get("suggested_filename")
    assert title == ARTIFACT_NAME or filename == ARTIFACT_NAME, (
        "agent did not create the requested brief.md artifact"
    )


def _editable_artifact_from_run(events: list[dict[str, Any]]) -> ArtifactReference:
    references: list[ArtifactReference] = []
    for event in events:
        if event.get("event_type") not in ARTIFACT_EVENTS:
            continue
        payload = _payload(event)
        kind = payload.get("kind")
        if kind not in {"document", "code"}:
            continue
        references.append(
            ArtifactReference(
                artifact_id=_required_text(payload, "artifact_id"),
                revision=_required_positive_int(payload, "revision"),
                kind=str(kind),
                content_ref=_required_text(payload, "content_ref"),
                content_digest=_required_digest(payload, "content_digest"),
            )
        )
    assert references, "agent did not create an editable Markdown artifact"
    return references[-1]


def _assert_only_workspace_or_artifact_tools(events: list[dict[str, Any]]) -> None:
    """Reject browsing/connector activity instead of loosely accepting a reply."""

    for event in events:
        event_type = event.get("event_type")
        payload = _payload(event)
        values = [
            str(event_type or "").lower(),
            *(
                str(payload[key]).lower()
                for key in ("capability", "tool", "tool_name", "name", "operation")
                if isinstance(payload.get(key), str)
            ),
        ]
        joined = " ".join(values)
        leaked = sorted(marker for marker in UNRELATED_TOOL_MARKERS if marker in joined)
        assert not leaked, f"G1 used unrelated tooling: {leaked}"
        if event_type in READ_EVENTS or event_type in WORKSPACE_STAGE_EVENTS:
            capability = payload.get("capability")
            if isinstance(capability, str):
                assert capability in ALLOWED_CAPABILITIES, (
                    f"G1 used unsupported capability {capability!r}"
                )


def _assert_no_workspace_apply(events: list[dict[str, Any]]) -> None:
    for event in events:
        if event.get("event_type") not in WRITE_APPLIED_EVENTS:
            continue
        payload = _payload(event)
        if (
            event.get("event_type") == "write.applied"
            or payload.get("executor") == "workspace"
        ):
            raise AssertionError("workspace write was applied before explicit approval")


def _assert_agent_loaded_edited_artifact(
    events: list[dict[str, Any]], edited_artifact: ArtifactReference
) -> None:
    for event in events:
        if event.get("event_type") not in READ_EVENTS:
            continue
        payload = _payload(event)
        artifact_id = payload.get("artifact_id")
        path = payload.get("path") or payload.get("virtual_path")
        capability = payload.get("capability")
        looks_like_brief = (
            path == ARTIFACT_NAME or artifact_id == edited_artifact.artifact_id
        )
        if not looks_like_brief:
            continue
        assert capability == "artifact", "artifact load used an unexpected capability"
        assert artifact_id == edited_artifact.artifact_id, (
            "agent loaded a same-name or same-revision foreign artifact"
        )
        assert payload.get("revision") == edited_artifact.revision, (
            "agent loaded a different artifact revision than the edited one"
        )
        assert payload.get("content_ref") == edited_artifact.content_ref, (
            "agent loaded an artifact with a different immutable content ref"
        )
        assert payload.get("content_digest") == edited_artifact.content_digest, (
            "agent loaded an artifact with a different immutable content digest"
        )
        return
    raise AssertionError(
        "agent did not record loading the user-edited Markdown artifact"
    )


def _main_workspace_journal_snapshot(
    user_data_dir: Path,
) -> MainWorkspaceJournalSnapshot:
    """Observe only opaque main-owned journal evidence, never its secret payload."""

    journal_path = user_data_dir / "capabilities" / "workspace-journal.bin"
    journal_digest: str | None = None
    if journal_path.exists():
        assert journal_path.is_file() and not journal_path.is_symlink(), (
            "Electron-main workspace journal is not a regular private file"
        )
        journal = journal_path.read_bytes()
        assert journal.startswith(WORKSPACE_JOURNAL_CIPHER_MARKER), (
            "Electron-main workspace journal is not encrypted in production posture"
        )
        assert not journal.startswith(WORKSPACE_JOURNAL_PLAINTEXT_MARKER), (
            "Electron-main workspace journal used a plaintext fallback"
        )
        journal_digest = hashlib.sha256(journal).hexdigest()

    native_directory = (
        user_data_dir / "capabilities" / "workspace-v2" / "native-journal"
    )
    claim_records: set[str] = set()
    if native_directory.exists():
        assert native_directory.is_dir() and not native_directory.is_symlink(), (
            "Electron-main native workspace journal is not a private directory"
        )
        for path in native_directory.iterdir():
            if NATIVE_CLAIM_RECORD.fullmatch(path.name) is None:
                continue
            assert (
                path.is_file() and not path.is_symlink() and path.stat().st_size > 0
            ), "Electron-main native claim/receipt journal record is invalid"
            claim_records.add(path.name)
    return MainWorkspaceJournalSnapshot(
        journal_digest=journal_digest,
        native_claim_records=frozenset(claim_records),
    )


def _assert_main_authority_commit(
    before: MainWorkspaceJournalSnapshot, after: MainWorkspaceJournalSnapshot
) -> None:
    """A generic effect event or host write cannot substitute this main witness."""

    assert after.journal_digest is not None, (
        "Electron-main encrypted workspace journal did not record the commit"
    )
    assert after.journal_digest != before.journal_digest, (
        "Electron-main workspace journal did not advance for the approved revision"
    )
    assert after.native_claim_records - before.native_claim_records, (
        "Electron-main native claim/receipt journal did not record an approved commit"
    )


def _assert_approved_and_applied(
    events: list[dict[str, Any]], stage: WorkspaceStage
) -> None:
    approved = False
    claimed = False
    applied = False
    for event in events:
        payload = _payload(event)
        if payload.get("stage_id") != stage.stage_id:
            continue
        if event.get("event_type") == "effect.decision_recorded":
            if payload.get("decision") != "approve":
                continue
            assert payload.get("revision") == stage.revision
            assert payload.get("proposal_digest") == stage.proposal_digest
            assert payload.get("target_digest") == stage.target_digest
            approved = True
        if event.get("event_type") == "effect.claimed":
            assert approved, "workspace claim appeared before the exact approval record"
            assert payload.get("executor") == "workspace", (
                "approved workspace stage was claimed by a different executor"
            )
            assert isinstance(payload.get("claim_id"), str) and payload["claim_id"], (
                "Electron-main workspace claim omitted its durable claim id"
            )
            assert payload.get("revision") == stage.revision
            claimed = True
        if event.get("event_type") == "effect.applied":
            assert claimed, "workspace apply appeared without an exact approved claim"
            assert payload.get("revision") == stage.revision
            assert payload.get("outcome") == "applied", (
                "G1 requires a new Electron-main workspace commit, not a generic replay"
            )
            _required_digest(payload, "result_digest")
            applied = True
    assert approved, "approval receipt was not bound to the reviewed stage revision"
    assert claimed, "Electron-main workspace authority never claimed the approved stage"
    assert applied, "Electron-main workspace authority did not apply the approved stage"


def _wait_for_stage_apply(
    session: DriverSession, run_id: str, stage: WorkspaceStage
) -> list[dict[str, Any]]:
    deadline = time.time() + 120
    while time.time() < deadline:
        events = _events(session, run_id)
        try:
            _assert_approved_and_applied(events, stage)
        except AssertionError:
            time.sleep(0.5)
            continue
        return events
    raise AssertionError("approved workspace stage did not reach a terminal host apply")


def _assert_exact_file_bytes(path: Path, approved: bytes) -> None:
    assert path.is_file(), "Electron-main approval did not create brief.md"
    actual = path.read_bytes()
    assert actual == approved, "local brief.md bytes differ from the approved revision"


def _assert_receipt_and_sources(session: DriverSession) -> None:
    assert session.wait_for("[data-testid=receipt-v2-launch]"), (
        "terminal workspace effect did not expose a receipt launcher"
    )
    session.click("[data-testid=receipt-v2-open]")
    assert session.wait_for("[data-testid=receipt-v2-surface]"), (
        "receipt launcher did not open the Studio receipt"
    )
    receipt_status = str(
        session.evaluate(
            'document.querySelector("[data-testid=receipt-v2-status]").textContent'
        )
    )
    assert receipt_status == "Completed", "receipt did not report a completed run"
    metrics = _evaluate_json(
        session,
        "JSON.stringify(Array.from(document.querySelectorAll("
        "'[data-testid=receipt-v2-metric]')).map((metric)=>({"
        "label:metric.querySelector('.ui-section-label')?.textContent,"
        "value:metric.querySelector('.ui-item-title')?.textContent})))",
    )
    assert isinstance(metrics, list) and all(
        isinstance(metric, dict) for metric in metrics
    ), "receipt metrics are not a structured receipt surface"
    effect_metrics = [metric for metric in metrics if metric.get("label") == "Effects"]
    assert effect_metrics == [
        {
            "label": "Effects",
            "value": "1 proposed · 1 approved · 1 applied · 0 rejected",
        }
    ], "receipt does not account for the exact approved workspace effect"

    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Studio did not expose Sources/provenance after the workspace receipt"
    )
    source_labels = _evaluate_json(
        session,
        "JSON.stringify(Array.from(document.querySelectorAll("
        "'[data-testid=sources-v2-row] .ui-item-title')).map((node)=>node.textContent))",
    )
    assert isinstance(source_labels, list) and all(
        isinstance(label, str) for label in source_labels
    ), "Sources/provenance rows are not structured labels"
    assert (
        source_labels.count("Artifact") == 1
        and source_labels.count("Workspace activity") == 1
    ), "Sources/provenance did not identify exactly one artifact and workspace activity"


def _assert_no_plaintext_secret(secret: str, roots: tuple[Path, ...]) -> None:
    """Search only journey-owned material; never echo a matching value."""

    needle = secret.encode("utf-8")
    assert needle, "BYOK value unexpectedly empty"
    for root in roots:
        if not root.exists():
            continue
        paths = (root,) if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                with path.open("rb") as handle:
                    previous = b""
                    while chunk := handle.read(64 * 1024):
                        if needle in previous + chunk:
                            raise AssertionError(
                                "plaintext BYOK material appeared in journey-owned "
                                "logs, screenshots, user data, or fixture workspace"
                            )
                        previous = (previous + chunk)[-(len(needle) - 1) :]
            except OSError:
                # A concurrent app shutdown can remove an owned transient file;
                # it cannot turn a missing file into a credential leak.
                continue


def _save_user_edit(
    session: DriverSession, artifact: ArtifactReference
) -> ArtifactReference:
    _assert_editor_surface(session)
    session.fill("#artifact-editor-text", USER_EDITED_MARKDOWN)
    session.click('button:has-text("Save new revision")')
    deadline = time.time() + 60
    while time.time() < deadline:
        detail = _artifact_detail(session, artifact.artifact_id)
        current = detail.get("current_revision")
        if isinstance(current, dict) and isinstance(current.get("revision"), int):
            revision = current["revision"]
            if revision > artifact.revision:
                edited = ArtifactReference(
                    artifact_id=artifact.artifact_id,
                    revision=revision,
                    kind=artifact.kind,
                    content_ref=_required_text(current, "content_ref"),
                    content_digest=_required_digest(current, "content_digest"),
                )
                edited_bytes = _read_artifact_bytes(session, edited)
                assert edited_bytes == USER_EDITED_MARKDOWN.encode("utf-8"), (
                    "Studio editor did not persist the user-edited artifact bytes"
                )
                assert (
                    hashlib.sha256(edited_bytes).hexdigest() == edited.content_digest
                ), "Studio editor returned bytes that do not match its immutable digest"
                return edited
        time.sleep(0.5)
    raise AssertionError("Studio editor did not save a new artifact revision")


def _show_revision_diff(session: DriverSession) -> None:
    assert session.wait_for("[data-testid=artifact-revision-history]"), (
        "edited artifact has no revision history"
    )
    assert session.present('button:has-text("Compare to current")'), (
        "edited artifact did not expose a revision-pinned diff"
    )
    session.click('button:has-text("Compare to current")')
    assert session.wait_for('[aria-label="Artifact revision comparison"]'), (
        "artifact revision comparison did not render"
    )


def _approve_stage(session: DriverSession) -> None:
    confirmation = _start_native_automation(_approval_command())
    try:
        session.click("[data-testid=tc-workspace-stage-approve]")
    finally:
        _wait_native_automation(confirmation, action="workspace confirmation")


def main() -> int:
    try:
        _preflight_packaged_supervisor()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        return _prerequisite_result(str(exc))

    _structured_result(
        "running", reason=f"supervised packaged stack; provider={provider}"
    )
    with _journey_environment(), FixtureWorkspace() as fixture:
        assert fixture.root is not None
        session = DriverSession(
            name="generative-workflows-g1-markdown-lifecycle",
            installed_payload=True,
        )
        try:
            with session:
                status = session.rpc("status")
                assert status.get("target") == INSTALLED_PAYLOAD_TARGET, (
                    "G1 did not launch the installed desktop payload"
                )
                _assert_main_production_posture(session)

                session.sign_in_local()
                session.shot("g1-sign-in")
                session.ftue_add_key(provider, key)
                # No screenshot is taken while the password field is present.
                session.shot("g1-composer")

                catalog = session.transport("GET", "/v1/agent/models")
                models = catalog.get("models", [])
                assert any(
                    isinstance(model, dict)
                    and model.get("provider") == provider
                    and model.get("configured") is True
                    for model in models
                ), (
                    "authenticated model catalog did not configure the entered BYOK provider"
                )

                _grant_fixture_workspace(session, fixture)
                assert not fixture.brief_path.exists(), (
                    "granting the local fixture must not create brief.md"
                )
                session.shot("g1-local-grant")

                conversation_id = _conversation_id(session)
                first_before = len(_runs_for_conversation(session, conversation_id))
                session.send_first_run_message(CREATE_PROMPT)
                first_run_id = _wait_for_new_run(session, conversation_id, first_before)
                _wait_for_terminal_run(session, first_run_id)
                first_events = _events(session, first_run_id)
                _assert_only_workspace_or_artifact_tools(first_events)
                _assert_no_workspace_apply(first_events)
                assert not fixture.brief_path.exists(), (
                    "agent created a host file before user approval"
                )

                first_artifact = _editable_artifact_from_run(first_events)
                _assert_artifact_named_brief(
                    _artifact_detail(session, first_artifact.artifact_id)
                )
                _open_first_artifact_from_sources(session)
                _assert_editor_surface(session)
                session.shot("g1-artifact-editor")
                edited_artifact = _save_user_edit(session, first_artifact)
                _show_revision_diff(session)
                session.shot("g1-artifact-edit-diff")

                second_before = len(_runs_for_conversation(session, conversation_id))
                session.send_first_run_message(STAGE_PROMPT)
                second_run_id = _wait_for_new_run(
                    session, conversation_id, second_before
                )
                _wait_for_terminal_run(session, second_run_id)
                second_events = _events(session, second_run_id)
                _assert_only_workspace_or_artifact_tools(second_events)
                _assert_agent_loaded_edited_artifact(second_events, edited_artifact)
                _assert_no_workspace_apply(second_events)
                assert not fixture.brief_path.exists(), (
                    "agent wrote brief.md before the exact staged revision was approved"
                )

                stages = _workspace_stages(second_events)
                assert len(stages) == 1, (
                    "second G1 run must produce exactly one workspace stage"
                )
                stage = stages[0]
                approved_bytes = _read_artifact_bytes(session, edited_artifact)
                assert approved_bytes, "staged artifact revision is empty"
                _assert_stage_binds_immutable_artifact(
                    stage,
                    edited_artifact,
                    approved_bytes,
                    _read_staged_proposal_bytes(session._user_data_dir, stage),
                )
                _assert_workspace_stage_surface(session, stage)
                session.shot("g1-held-workspace-stage")

                journal_before = _main_workspace_journal_snapshot(
                    session._user_data_dir
                )
                _approve_stage(session)
                _wait_for_stage_apply(session, second_run_id, stage)
                _assert_exact_file_bytes(fixture.brief_path, approved_bytes)
                _assert_main_authority_commit(
                    journal_before,
                    _main_workspace_journal_snapshot(session._user_data_dir),
                )
                _assert_receipt_and_sources(session)
                session.shot("g1-approved-receipt-sources")
        finally:
            # The test owns only this run's artifacts/user-data subdirectory and
            # temporary fixture.  Scan those roots without reporting the key.
            _assert_no_plaintext_secret(
                key,
                (session.run_dir, session._user_data_dir, fixture.root),
            )

    _structured_result("passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
