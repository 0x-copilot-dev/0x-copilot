#!/usr/bin/env python3
"""G2 — real supervised Desktop CSV lifecycle.

This is a live release journey, not a mock or seeded-run test. It launches the
installed supervised Electron payload, creates a dataset artifact, changes one
cell through the shipping dataset grid, then makes the agent reload that exact
artifact revision. The agent first stages the edited CSV and then revises the
same held workspace stage with a validated derived column. Only an explicit
native approval can cause Electron main to create forecast.csv in the
journey-owned temporary workspace.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import (  # noqa: E402
    INSTALLED_PAYLOAD_TARGET,
    EXIT_SKIPPED,
    DriverSession,
    load_env_key,
    staged_runtime_dir,
)


JOURNEY_ID: Final = "G2"
ARTIFACT_NAME: Final = "forecast.csv"

# The Sources rail no longer paints `sources-v2-open-artifact`: the compact
# source card that replaced the old rows (357836ce) marks an owner-routed row
# with `data-openable` and puts the open action on the row's own button. Three
# journeys were still driving the retired testid.
OPEN_ARTIFACT_SOURCE: Final = "[data-testid=sources-v2-row][data-openable=true] button"
TERMINAL_STATUSES: Final = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
)
ARTIFACT_EVENTS: Final = frozenset({"artifact.created", "artifact.revised"})
READ_EVENTS: Final = frozenset({"read.executed", "tool_result", "tool_call_completed"})
APPLY_EVENTS: Final = frozenset({"write.applied", "effect.applied"})
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
JOURNEY_ENVIRONMENT: Final = {
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM": "1",
    "SURFACES_V2": "true",
    "ARTIFACT_EFFECTS_V2": "true",
    "ARTIFACT_DRAFTS_V2": "true",
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}
INITIAL_HEADERS: Final = ("month", "region", "bookings", "forecast")
DERIVED_HEADER: Final = "variance"
EDITED_BOOKINGS: Final = "126"
ARTIFACT_CONTENT_REF: Final = re.compile(
    r"^artifact://(?P<artifact_id>[^/]+)/revisions/(?P<revision>[1-9][0-9]*)$"
)

CREATE_PROMPT: Final = """Create a reviewable CSV dataset artifact named
forecast.csv. It must be a valid UTF-8 RFC-4180-style CSV with exactly these
headers in this order: month,region,bookings,forecast. Include at least three
monthly rows and integer bookings and forecast values. Keep it as an editable
dataset/table in Studio. Do not write any local workspace file, do not stage an
effect, do not browse, and do not use connectors or unrelated tools."""

STAGE_EDITED_PROMPT: Final = """Load the latest user-edited forecast.csv
dataset artifact from this Studio conversation. Stage its exact current CSV
bytes as a held workspace revision for forecast.csv in the granted local
workspace. Do not change the dataset in this step, do not apply it, do not
claim a local file was written, do not browse, and do not use connectors or
unrelated tools."""

REVISE_STAGE_PROMPT: Final = """Reload the exact user-edited forecast.csv
dataset artifact revision from this Studio conversation. Revise the existing
held workspace stage {stage_id} (do not create another stage) to make a new
second staged revision. Its CSV must retain all existing columns and rows and
append an integer variance column where every value equals forecast minus
bookings. Validate that the revised CSV is structured and has no malformed
rows. Keep revision 2 held for the user's explicit review: do not apply it,
do not claim any local write, do not browse, and do not use connectors or
unrelated tools."""


class PreflightSkip(RuntimeError):
    """A documented prerequisite is absent; this is explicitly not a pass."""


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    revision: int
    kind: str
    content_ref: str


@dataclass(frozen=True)
class WorkspaceStage:
    stage_id: str
    revision: int
    proposal_digest: str
    target_digest: str
    proposal_content_ref: str
    display_target: str
    executor: str
    target_ref: str


class FixtureWorkspace:
    """An empty, private workspace; this journey never writes it directly."""

    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    @property
    def forecast_path(self) -> Path:
        if self.root is None:
            raise RuntimeError("fixture workspace has not been entered")
        return self.root / ARTIFACT_NAME

    def __enter__(self) -> "FixtureWorkspace":
        self._temporary = tempfile.TemporaryDirectory(prefix="0xcopilot-g2-")
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
    return EXIT_SKIPPED


def _preflight_packaged_supervisor() -> None:
    """Skip only absent staged-runtime prerequisites; configuration bypasses fail."""

    target = os.environ.get("COPILOT_DESKTOP_TEST_TARGET", INSTALLED_PAYLOAD_TARGET)
    if target != INSTALLED_PAYLOAD_TARGET:
        raise AssertionError(
            "G2 requires COPILOT_DESKTOP_TEST_TARGET=installed-payload because "
            "Electron-main workspace authority is packaged-only"
        )
    if os.environ.get("APP_DIR"):
        raise AssertionError(
            "G2 must not set APP_DIR; the installed Electron payload must own "
            "the application path"
        )
    if os.environ.get("COPILOT_FACADE_URL"):
        raise AssertionError(
            "G2 must not use COPILOT_FACADE_URL; it requires the embedded "
            "supervised facade"
        )
    _preflight_staged_runtime(target=INSTALLED_PAYLOAD_TARGET)


def _preflight_staged_runtime(*, target: str = INSTALLED_PAYLOAD_TARGET) -> None:
    """Require the real supervised services without constraining the shell build.

    ``target`` selects WHICH stage to require, and must match the target the
    caller's ``DriverSession`` will launch: G2 itself runs the installed
    payload, while the artifact-only G2A–G2D reuse this check against the
    source checkout's stage. Gating on the other one would skip on a perfectly
    good stage — or, worse, green-light a stage that is not under test.
    """

    runtime = staged_runtime_dir(target=target)
    manifest_path = runtime / "staging-manifest.json"
    if not manifest_path.is_file():
        raise PreflightSkip(
            "host staged runtime is absent; run make desktop-supervised or stage "
            "the host runtime with tools/desktop-runtime/stage.mjs"
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
    requested = os.environ.get("G2_PROVIDER", "auto").strip().lower()
    if requested not in {"auto", "openai", "anthropic"}:
        raise AssertionError("G2_PROVIDER must be auto, openai, or anthropic")
    providers = (requested,) if requested != "auto" else ("openai", "anthropic")
    for provider in providers:
        try:
            return provider, load_env_key(provider)
        except SystemExit:
            # load_env_key emits only a variable/path; never a provider key.
            continue
    provider_label = requested if requested != "auto" else "OpenAI or Anthropic"
    raise PreflightSkip(
        f"no local {provider_label} BYOK key is available through "
        "services/ai-backend/.env"
    )


@contextmanager
def _journey_environment() -> Iterator[None]:
    """Enable the real workspace lane without inheriting plaintext provider keys."""

    changed = set(JOURNEY_ENVIRONMENT) | set(SECRET_ENVIRONMENT_NAMES)
    previous = {name: os.environ.get(name) for name in changed}
    os.environ.update(JOURNEY_ENVIRONMENT)
    for name in SECRET_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


_FOLDER_PICKER_APPLESCRIPT: Final = r"""
on waitForSheet(processId, attempts)
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
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
  set processId to (item 2 of argv) as integer
  if my waitForSheet(processId, 200) is false then error "folder picker did not appear"
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
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
on waitForSheet(processId, attempts)
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
      repeat with attempt from 1 to attempts
        if exists sheet 1 of window 1 then return true
        delay 0.1
      end repeat
    end tell
  end tell
  return false
end waitForSheet

on run argv
  set processId to (item 1 of argv) as integer
  if my waitForSheet(processId, 200) is false then error "workspace confirmation did not appear"
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
      click button "Approve" of sheet 1 of window 1
    end tell
  end tell
end run
"""


def _folder_picker_command(root: Path, process_id: int) -> list[str]:
    return [
        "/usr/bin/osascript",
        "-e",
        _FOLDER_PICKER_APPLESCRIPT,
        "--",
        str(root),
        str(process_id),
    ]


def _approval_command(process_id: int) -> list[str]:
    return [
        "/usr/bin/osascript",
        "-e",
        _APPROVAL_APPLESCRIPT,
        "--",
        str(process_id),
    ]


def _start_native_automation(command: list[str]) -> subprocess.Popen[bytes]:
    if sys.platform != "darwin":
        raise AssertionError("G2 native workspace approval requires macOS")
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
            f"native {action} automation failed; "
            "no folder grant or approval was assumed"
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


def _capability_invoke(
    session: DriverSession, channel: str, payload: Mapping[str, Any]
) -> Any:
    return _evaluate_json(
        session,
        "(async()=>{try{const value=await window.bridge.ipc.invoke("
        f"{json.dumps(channel)},{json.dumps(dict(payload))});"
        "return JSON.stringify(value);}catch(error){return 'ERR:'+error.message;}})()",
    )


def _desktop_process_id(session: DriverSession) -> int:
    status = session.rpc("status")
    process_id = status.get("pid")
    assert isinstance(process_id, int) and process_id > 0, (
        "desktop driver did not expose its launched Electron process id"
    )
    return process_id


def _grant_fixture_workspace(session: DriverSession, fixture: FixtureWorkspace) -> str:
    assert fixture.root is not None
    picker = _start_native_automation(
        _folder_picker_command(fixture.root, _desktop_process_id(session))
    )
    try:
        grant = _capability_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": "read_write_no_delete", "label": "G2 CSV fixture"},
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
    grants = _capability_invoke(session, "capability.list-grants", {})
    assert isinstance(grants, list) and len(grants) == 1, (
        "fresh G2 session must expose exactly one local fixture grant"
    )
    assert grants[0].get("grantId") == grant_id, "listed grant changed after creation"
    return grant_id


def _wait_for_conversation_id(session: DriverSession) -> str:
    """Wait until the first composer submission binds the new conversation.

    A fresh installed payload intentionally has no conversation route before
    the user sends their first message. The UI creates and selects that
    conversation as one atomic submission flow.
    """

    deadline = time.time() + 60
    last_route = ""
    while time.time() < deadline:
        last_route = str(session.evaluate("window.location.hash") or "")
        match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", last_route)
        if match is not None:
            return match.group(1)
        time.sleep(0.25)
    raise AssertionError(
        f"first CSV prompt did not bind a conversation route; got {last_route!r}"
    )


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
    raise AssertionError("Studio did not create a run for the G2 prompt")


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
                f"G2 agent run ended {status!r}: {result.get('safe_error')!r}"
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


def _run_prompt(
    session: DriverSession, conversation_id: str, prompt: str
) -> tuple[str, list[dict[str, Any]]]:
    before = len(_runs_for_conversation(session, conversation_id))
    session.send_first_run_message(prompt)
    run_id = _wait_for_new_run(session, conversation_id, before)
    _wait_for_terminal_run(session, run_id)
    return run_id, _events(session, run_id)


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


def _dataset_artifacts(events: list[dict[str, Any]]) -> list[ArtifactReference]:
    known_kinds: dict[str, str] = {}
    references: list[ArtifactReference] = []
    for event in events:
        if event.get("event_type") not in ARTIFACT_EVENTS:
            continue
        payload = _payload(event)
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        kind = payload.get("kind")
        if isinstance(kind, str):
            known_kinds[artifact_id] = kind
        resolved_kind = known_kinds.get(artifact_id)
        if resolved_kind != "dataset":
            continue
        references.append(
            ArtifactReference(
                artifact_id=artifact_id,
                revision=_required_positive_int(payload, "revision"),
                kind=resolved_kind,
                content_ref=_required_text(payload, "content_ref"),
            )
        )
    return references


def _dataset_artifact_from_run(events: list[dict[str, Any]]) -> ArtifactReference:
    artifacts = _dataset_artifacts(events)
    assert artifacts, "agent did not create a dataset artifact"
    return artifacts[-1]


def _artifact_detail(session: DriverSession, artifact_id: str) -> dict[str, Any]:
    detail = session.transport("GET", f"/v1/agent/artifacts/{artifact_id}")
    assert isinstance(detail, dict), "artifact detail is malformed"
    return detail


def _assert_artifact_named_forecast(detail: Mapping[str, Any]) -> None:
    artifact = detail.get("artifact")
    title = artifact.get("title") if isinstance(artifact, dict) else None
    filename = detail.get("suggested_filename")
    assert title == ARTIFACT_NAME or filename == ARTIFACT_NAME, (
        "agent did not create the requested forecast.csv artifact"
    )


def _read_artifact_bytes(session: DriverSession, artifact: ArtifactReference) -> bytes:
    """Read immutable artifact bytes through the real Electron-main IPC stream."""

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
            if (bytes.length>131072) throw new Error("artifact exceeds G2 bound");
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


def _artifact_from_content_ref(content_ref: str) -> ArtifactReference:
    match = ARTIFACT_CONTENT_REF.fullmatch(content_ref)
    assert match is not None, "workspace stage did not pin immutable artifact content"
    return ArtifactReference(
        artifact_id=match.group("artifact_id"),
        revision=int(match.group("revision")),
        kind="dataset",
        content_ref=content_ref,
    )


def _workspace_stages(events: list[dict[str, Any]]) -> list[WorkspaceStage]:
    staged: dict[str, WorkspaceStage] = {}
    for event in events:
        event_type = event.get("event_type")
        payload = _payload(event)
        if event_type == "effect.staged" and payload.get("executor") == "workspace":
            stage = WorkspaceStage(
                stage_id=_required_text(payload, "stage_id"),
                revision=1,
                proposal_digest=_required_text(payload, "proposal_digest"),
                target_digest=_required_text(payload, "target_digest"),
                proposal_content_ref=_required_text(payload, "proposal_content_ref"),
                display_target=_required_text(payload, "display_target"),
                executor="workspace",
                target_ref=_required_text(payload, "target_ref"),
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
                proposal_digest=_required_text(payload, "proposal_digest"),
                target_digest=_required_text(payload, "target_digest"),
                proposal_content_ref=_required_text(payload, "proposal_content_ref"),
                display_target=_required_text(payload, "display_target"),
                executor=prior.executor,
                target_ref=_required_text(payload, "target_ref"),
            )
    return list(staged.values())


def _single_workspace_stage(events: list[dict[str, Any]]) -> WorkspaceStage:
    stages = _workspace_stages(events)
    assert len(stages) == 1, f"G2 expected one workspace stage, got {len(stages)}"
    return stages[0]


def _assert_stage_uses_workspace_authority(
    stage: WorkspaceStage, fixture: FixtureWorkspace
) -> None:
    assert stage.executor == "workspace", "CSV stage bypassed the workspace executor"
    assert stage.target_ref.startswith("workspace-target://"), (
        "CSV stage target is not an opaque workspace capability reference"
    )
    assert stage.display_target.endswith(ARTIFACT_NAME), (
        "CSV stage is not pinned to forecast.csv"
    )
    assert fixture.root is not None
    fixture_root = str(fixture.root)
    assert fixture_root not in stage.target_ref
    assert fixture_root not in stage.display_target
    assert "http://" not in stage.target_ref and "https://" not in stage.target_ref


def _assert_workspace_stage_surface(
    session: DriverSession, stage: WorkspaceStage
) -> None:
    required = {
        "stage": "[data-testid=tc-workspace-stage]",
        "path": "[data-testid=tc-workspace-stage-path]",
        "revision": "[data-testid=tc-workspace-stage-revision]",
        "CSV preview": "[data-testid=tc-workspace-stage-preview-csv]",
        "CSV diff": "[data-testid=tc-workspace-stage-diff-csv]",
        "edit": "[data-testid=tc-workspace-stage-edit]",
    }
    missing = [
        name for name, selector in required.items() if not session.present(selector)
    ]
    assert not missing, f"G2 workspace stage is missing visible review UI: {missing}"
    path_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-workspace-stage-path]").innerText'
        )
    )
    assert path_text.endswith(ARTIFACT_NAME), "workspace stage is not forecast.csv"
    revision_text = str(
        session.evaluate(
            "document.querySelector("
            '"[data-testid=tc-workspace-stage-revision]").innerText'
        )
    )
    assert f"rev {stage.revision}" in revision_text, (
        "workspace stage UI is not pinned to the staged revision"
    )


def _assert_dataset_surface(session: DriverSession) -> None:
    required = {
        "artifact frame": "[data-testid=artifact-frame]",
        "dataset renderer": "[data-testid=artifact-dataset-renderer]",
        "cell editor": '[role=grid][aria-label="Dataset cell editor"]',
        "bookings cell": '[aria-label="bookings, row 2"]',
        "revision actions": '[aria-label="Dataset revision actions"]',
        "revision history": "[data-testid=artifact-revision-history]",
    }
    missing = [
        name for name, selector in required.items() if not session.present(selector)
    ]
    assert not missing, f"G2 dataset table/editor is missing: {missing}"


def _open_artifact_from_sources(session: DriverSession) -> None:
    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Studio did not show the Sources provenance rail for the dataset"
    )
    source_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=sources-v2-tab]").innerText'
        )
    )
    assert "Artifact" in source_text, "dataset provenance did not identify its artifact"
    if not session.present("[data-testid=artifact-frame]"):
        assert session.present(OPEN_ARTIFACT_SOURCE), (
            "dataset source is not user-openable from provenance"
        )
        session.click(OPEN_ARTIFACT_SOURCE)
        assert session.wait_for("[data-testid=artifact-frame]"), (
            "opening the dataset source did not render an artifact surface"
        )


def _parse_csv_bytes(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("CSV artifact is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames
    assert headers is not None and all(headers), "CSV has no complete header row"
    assert len(headers) == len(set(headers)), "CSV has duplicate headers"
    rows = list(reader)
    assert rows, "CSV has no data rows"
    assert all(None not in row for row in rows), "CSV contains malformed extra cells"
    assert all(all(value is not None for value in row.values()) for row in rows), (
        "CSV contains a short row"
    )
    return list(headers), rows


def _assert_initial_csv_semantics(content: bytes) -> None:
    headers, rows = _parse_csv_bytes(content)
    assert tuple(headers) == INITIAL_HEADERS, (
        "dataset artifact does not use the required forecast CSV columns"
    )
    assert len(rows) >= 3, "dataset artifact must contain at least three forecast rows"
    for row in rows:
        assert row["month"] and row["region"], "forecast row is missing its identity"
        int(row["bookings"])
        int(row["forecast"])


def _assert_derived_csv_semantics(content: bytes) -> None:
    headers, rows = _parse_csv_bytes(content)
    assert headers == [*INITIAL_HEADERS, DERIVED_HEADER], (
        "derived CSV must retain its structured columns and append variance"
    )
    assert rows[0]["bookings"] == EDITED_BOOKINGS, (
        "the agent did not reload the user-edited dataset cell"
    )
    for row in rows:
        assert int(row[DERIVED_HEADER]) == (
            int(row["forecast"]) - int(row["bookings"])
        ), "derived variance is not forecast minus bookings"


def _save_user_csv_cell(
    session: DriverSession, artifact: ArtifactReference
) -> ArtifactReference:
    _assert_dataset_surface(session)
    session.fill('[aria-label="bookings, row 2"]', EDITED_BOOKINGS)
    session.click('button:has-text("Save patched revision")')
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
                    kind="dataset",
                    content_ref=(
                        f"artifact://{artifact.artifact_id}/revisions/{revision}"
                    ),
                )
                content = _read_artifact_bytes(session, edited)
                _assert_initial_csv_semantics(content)
                headers, rows = _parse_csv_bytes(content)
                assert tuple(headers) == INITIAL_HEADERS
                assert rows[0]["bookings"] == EDITED_BOOKINGS, (
                    "Studio did not persist the user's structured CSV cell edit"
                )
                return edited
        time.sleep(0.5)
    raise AssertionError("Studio dataset editor did not save a new immutable revision")


def _assert_agent_reloaded_dataset(
    events: list[dict[str, Any]], edited: ArtifactReference
) -> None:
    for event in events:
        if event.get("event_type") not in READ_EVENTS:
            continue
        payload = _payload(event)
        if (
            payload.get("artifact_id") == edited.artifact_id
            and payload.get("revision") == edited.revision
        ):
            return
        path = payload.get("path") or payload.get("virtual_path")
        if (
            payload.get("capability") == "artifact"
            and path == ARTIFACT_NAME
            and payload.get("revision") == edited.revision
        ):
            return
    raise AssertionError("agent did not record loading the user-edited CSV artifact")


def _assert_only_workspace_or_artifact_tools(events: list[dict[str, Any]]) -> None:
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
        assert not leaked, f"G2 used unrelated tooling: {leaked}"
        capability = payload.get("capability")
        if isinstance(capability, str):
            assert capability in {"workspace", "artifact"}, (
                f"G2 used unsupported capability {capability!r}"
            )


def _assert_no_workspace_apply(events: list[dict[str, Any]]) -> None:
    for event in events:
        if event.get("event_type") not in APPLY_EVENTS:
            continue
        # G2 permits only artifact and workspace activity. There can therefore
        # be no benign effect application before the native approval decision.
        raise AssertionError("workspace write was applied before explicit approval")


def _assert_no_fixture_root_leak(
    events: list[dict[str, Any]], fixture: FixtureWorkspace
) -> None:
    assert fixture.root is not None
    fixture_root = str(fixture.root)
    for event in events:
        payload = _payload(event)
        for key in ("target_ref", "display_target", "path", "virtual_path"):
            value = payload.get(key)
            assert not (isinstance(value, str) and fixture_root in value), (
                "workspace event exposed the fixture's physical host path"
            )


def _assert_approved_and_applied(
    events: list[dict[str, Any]], stage: WorkspaceStage
) -> None:
    approved = False
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
        if event.get("event_type") == "effect.applied":
            assert payload.get("revision") == stage.revision
            assert payload.get("outcome") in {"applied", "already_applied"}
            applied = True
    assert approved, "approval receipt was not bound to the reviewed CSV revision"
    assert applied, "Electron-main workspace authority did not apply the approved CSV"


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
    raise AssertionError("approved CSV stage did not reach a terminal host apply")


def _assert_exact_file_bytes(path: Path, approved: bytes) -> None:
    assert path.is_file(), "Electron-main approval did not create forecast.csv"
    actual = path.read_bytes()
    assert actual == approved, (
        "local forecast.csv bytes differ from the approved revision"
    )
    _assert_derived_csv_semantics(actual)


def _assert_receipt_and_sources(session: DriverSession) -> None:
    assert session.wait_for("[data-testid=receipt-v2-launch]"), (
        "terminal workspace effect did not expose a receipt launcher"
    )
    session.click("[data-testid=receipt-v2-open]")
    assert session.wait_for("[data-testid=receipt-v2-surface]"), (
        "receipt launcher did not open the Studio receipt"
    )
    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Studio did not expose Sources/provenance after the workspace receipt"
    )
    source_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=sources-v2-tab]").innerText'
        )
    )
    assert "Artifact" in source_text and "Workspace activity" in source_text, (
        "Sources/provenance did not identify both the dataset and workspace effect"
    )


def _assert_no_plaintext_secret(secret: str, roots: tuple[Path, ...]) -> None:
    """Search journey-owned files without ever returning a matching secret value."""

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
                # Shutdown can remove an owned transient file; it cannot make a
                # missing file evidence of a credential leak.
                continue


def _approve_stage(session: DriverSession) -> None:
    confirmation = _start_native_automation(
        _approval_command(_desktop_process_id(session))
    )
    try:
        session.click("[data-testid=tc-workspace-stage-approve]")
    finally:
        _wait_native_automation(confirmation, action="workspace confirmation")


def main() -> int:
    try:
        _preflight_packaged_supervisor()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        return _skip(str(exc))

    _structured_result(
        "running", reason=f"supervised packaged stack; provider={provider}"
    )
    with _journey_environment(), FixtureWorkspace() as fixture:
        assert fixture.root is not None
        session = DriverSession(
            name="generative-workflows-g2-csv-lifecycle", installed_payload=True
        )
        completed = False
        try:
            with session:
                status = session.rpc("status")
                assert status.get("target") == INSTALLED_PAYLOAD_TARGET, (
                    "G2 did not launch the installed desktop payload"
                )
                assert status.get("posture") == "prod", "wrong desktop posture"

                session.sign_in_local()
                session.shot("g2-sign-in")
                session.ftue_add_key(provider, key)
                # No screenshot is taken while the password field is present.
                session.shot("g2-composer")
                catalog = session.transport("GET", "/v1/agent/models")
                models = catalog.get("models", [])
                model_configured = any(
                    isinstance(model, dict)
                    and model.get("provider") == provider
                    and model.get("configured") is True
                    for model in models
                )
                assert model_configured, (
                    "authenticated model catalog did not configure the entered "
                    "BYOK provider"
                )

                _grant_fixture_workspace(session, fixture)
                assert not fixture.forecast_path.exists(), (
                    "granting the fixture must not create forecast.csv"
                )
                session.shot("g2-local-grant")

                # The fresh first-run composer is deliberately unbound. Its
                # first submission creates both the conversation and its run.
                session.send_first_run_message(CREATE_PROMPT)
                conversation_id = _wait_for_conversation_id(session)
                create_run_id = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, create_run_id)
                create_events = _events(session, create_run_id)
                _assert_only_workspace_or_artifact_tools(create_events)
                _assert_no_workspace_apply(create_events)
                artifact = _dataset_artifact_from_run(create_events)
                _assert_artifact_named_forecast(
                    _artifact_detail(session, artifact.artifact_id)
                )
                _open_artifact_from_sources(session)
                _assert_dataset_surface(session)
                initial_bytes = _read_artifact_bytes(session, artifact)
                _assert_initial_csv_semantics(initial_bytes)
                session.shot("g2-dataset-provenance")

                edited = _save_user_csv_cell(session, artifact)
                session.shot("g2-user-cell-edit")
                assert not fixture.forecast_path.exists(), (
                    "editing a staged artifact must not write forecast.csv"
                )

                _, first_stage_events = _run_prompt(
                    session, conversation_id, STAGE_EDITED_PROMPT
                )
                _assert_only_workspace_or_artifact_tools(first_stage_events)
                _assert_agent_reloaded_dataset(first_stage_events, edited)
                _assert_no_workspace_apply(first_stage_events)
                first_stage = _single_workspace_stage(first_stage_events)
                assert first_stage.revision == 1, (
                    "first CSV workspace stage is not rev 1"
                )
                _assert_stage_uses_workspace_authority(first_stage, fixture)
                _assert_workspace_stage_surface(session, first_stage)
                assert not fixture.forecast_path.exists(), (
                    "first held CSV stage wrote the fixture before approval"
                )
                session.shot("g2-first-held-stage")

                revise_prompt = REVISE_STAGE_PROMPT.format(
                    stage_id=first_stage.stage_id
                )
                revise_run_id, revise_events = _run_prompt(
                    session, conversation_id, revise_prompt
                )
                _assert_only_workspace_or_artifact_tools(revise_events)
                _assert_agent_reloaded_dataset(revise_events, edited)
                _assert_no_workspace_apply(revise_events)
                final_stage = _single_workspace_stage(
                    [*first_stage_events, *revise_events]
                )
                assert final_stage.stage_id == first_stage.stage_id, (
                    "agent created a second workspace stage instead of revising "
                    "the held one"
                )
                assert final_stage.revision == first_stage.revision + 1, (
                    "agent did not produce the required second staged CSV revision"
                )
                _assert_stage_uses_workspace_authority(final_stage, fixture)
                _assert_workspace_stage_surface(session, final_stage)
                approved_artifact = _artifact_from_content_ref(
                    final_stage.proposal_content_ref
                )
                approved_bytes = _read_artifact_bytes(session, approved_artifact)
                assert (
                    hashlib.sha256(approved_bytes).hexdigest()
                    == final_stage.proposal_digest
                ), "stage digest does not bind the immutable approved CSV bytes"
                _assert_derived_csv_semantics(approved_bytes)
                preapproval_events = [
                    *create_events,
                    *first_stage_events,
                    *revise_events,
                ]
                _assert_no_fixture_root_leak(preapproval_events, fixture)
                assert not fixture.forecast_path.exists(), (
                    "revised CSV stage wrote the fixture before approval"
                )
                session.shot("g2-revised-csv-diff")

                _approve_stage(session)
                applied_events = _wait_for_stage_apply(
                    session, revise_run_id, final_stage
                )
                _assert_no_fixture_root_leak(applied_events, fixture)
                _assert_exact_file_bytes(fixture.forecast_path, approved_bytes)
                _assert_receipt_and_sources(session)
                session.shot("g2-approved-receipt")
                completed = True
        finally:
            _assert_no_plaintext_secret(
                key, (session.run_dir, session._user_data_dir, fixture.root)
            )
        if completed:
            _structured_result("passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
