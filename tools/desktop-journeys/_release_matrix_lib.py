#!/usr/bin/env python3
"""Shared, fail-closed support for the G3--G10 Desktop release journeys.

The module is intentionally local to this journey suite.  It launches only the
installed supervised Desktop payload, makes service reads through the app's
authenticated facade transport, confines authored files to a newly-created
temporary root, and never loads provider credentials except through
``load_env_key`` for a live pass.

Two harness capabilities are deliberately explicit:

* ``GENUI_DETERMINISTIC_SUPERVISED_READY=stdio-v1`` attests that the installed
  Desktop supervisor propagates the env-gated deterministic model to its
  ai-backend child.  The current shipped supervisor does not, so deterministic
  passes block before Electron launch.
* ``GENUI_LOCAL_FIXTURE_BRIDGE=stdio-v1`` attests that the public facade accepts
  the fixture-only stdio registration shape used below and keeps that process
  scoped to the fresh Desktop profile.  G5--G9 block before Electron launch.

  Half of this flag's original justification is now stale and the correction
  matters, because it says where the remaining work is.  "The current public
  registry is URL-only" was true when written and is not any more: the contract
  carries ``McpStdioRequest`` and a stdio server is addressed by ``stdio``
  alone.  Registration WORKS -- verified by posting the corrected body through
  the app and reading the response back (``url: null``, the launch config
  persisted, a ``name`` assigned).  What is still missing is EXECUTION: with the
  fixture registered and scoped to the conversation, its tools never enter the
  model's catalog, so the agent reaches for the built-ins instead.  The flag
  stays gated on that.

Setting either flag cannot manufacture a pass: the subsequent authenticated
facade, event-ledger, DOM, and fixture-target assertions still fail hard.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final
from uuid import uuid4


from _lib import (  # noqa: E402
    INSTALLED_PAYLOAD_TARGET,
    EXIT_BLOCKED,
    DriverSession,
    load_env_key,
    staged_runtime_dir,
)


HERE: Final = Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[2]
FIXTURE_SERVER: Final = HERE / "local-fixture-connector" / "server.py"
SCENARIO_PATH: Final = HERE / "scenarios" / "local-communications.json"
FIXTURE_NAMESPACE: Final = "fixture://generative-workflows/launch-week"
FIXTURE_WORKSPACE_ROOT: Final = "fixture://workspace/launch-week"
TERMINAL_STATUSES: Final = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
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
STUDIO_ENVIRONMENT: Final = {
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM": "1",
    "SURFACES_V2": "true",
    "ARTIFACT_EFFECTS_V2": "true",
    "ARTIFACT_DRAFTS_V2": "true",
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}
FAKE_MODEL_ENVIRONMENT_NAMES: Final = (
    "RUNTIME_FAKE_MODEL",
    "RUNTIME_FAKE_MODEL_TOOL_CALLS",
    "RUNTIME_FAKE_MODEL_TOOL_NAME",
    "RUNTIME_FAKE_MODEL_TOOL_ARGS",
    "RUNTIME_FAKE_MODEL_PARALLEL_TOOL_CALLS",
    "RUNTIME_FAKE_MODEL_PARALLEL_TRIGGER",
)
FIXTURE_REMOTE_SCHEMES: Final = (
    "http://",
    "https://",
    "smtp://",
    "smtps://",
    "discord://",
    "x://",
)
EVENT_OPERATION_FIELDS: Final = (
    "op",
    "operation",
    "tool",
    "tool_name",
    "resolved_tool_name",
    "display_operation",
)


class JourneyMode(StrEnum):
    DETERMINISTIC = "deterministic"
    LIVE = "live"


class JourneyBlocked(RuntimeError):
    """A missing product/harness prerequisite; never a passing result."""


@dataclass(frozen=True)
class PassConfig:
    journey_id: str
    slug: str
    mode: JourneyMode
    provider: str | None
    key: str | None
    user_data_token: str

    @property
    def run_name(self) -> str:
        return f"generative-workflows-{self.slug}-{self.mode.value}"


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    revision: int
    kind: str
    content_ref: str
    content_digest: str


@dataclass(frozen=True)
class EffectStage:
    stage_id: str
    revision: int
    proposal_digest: str
    target_digest: str
    target_ref: str
    executor: str


@dataclass(frozen=True)
class FixtureRegistration:
    server_id: str
    name: str


class ThrowawayJourneyRoot:
    """A fresh filesystem root that rejects traversal and cleans unconditionally."""

    def __init__(self, journey_id: str, mode: JourneyMode) -> None:
        prefix = f"0xcopilot-{journey_id.lower()}-{mode.value}-"
        self._temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._temporary.name).resolve()
        if not self.root.is_dir() or self.root.is_symlink():
            self._temporary.cleanup()
            raise AssertionError("temporary journey root is not a private directory")

    def path(self, relative: str) -> Path:
        parsed = PurePosixPath(relative)
        if (
            not relative
            or parsed.is_absolute()
            or ".." in parsed.parts
            or "." in parsed.parts
        ):
            raise AssertionError("journey fixture path must be a safe relative path")
        candidate = (self.root / Path(*parsed.parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise AssertionError("journey fixture escaped its temporary root") from exc
        return candidate

    def seed_bytes(self, relative: str, content: bytes) -> Path:
        path = self.path(relative)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def seed_text(self, relative: str, content: str) -> Path:
        return self.seed_bytes(relative, content.encode("utf-8"))

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "ThrowawayJourneyRoot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def emit_result(
    journey_id: str,
    outcome: str,
    *,
    mode: JourneyMode | None = None,
    reason: str | None = None,
) -> None:
    payload: dict[str, str] = {"journey": journey_id, "outcome": outcome}
    if mode is not None:
        payload["mode"] = mode.value
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def requested_modes(
    journey_id: str, argv: list[str] | None = None
) -> tuple[JourneyMode, ...]:
    parser = argparse.ArgumentParser(
        description=f"Run the {journey_id} installed Desktop release journey."
    )
    parser.add_argument(
        "--mode",
        choices=("deterministic", "live", "all"),
        default=os.environ.get("GENUI_JOURNEY_MODE", "all"),
        help="all runs the keyless deterministic pass before the BYOK live pass",
    )
    parsed = parser.parse_args(argv)
    if parsed.mode == "all":
        return (JourneyMode.DETERMINISTIC, JourneyMode.LIVE)
    return (JourneyMode(parsed.mode),)


def preflight_installed_supervisor(*, native_dialogs: bool = False) -> None:
    target = os.environ.get("COPILOT_DESKTOP_TEST_TARGET", INSTALLED_PAYLOAD_TARGET)
    if target != INSTALLED_PAYLOAD_TARGET:
        raise AssertionError(
            "journey requires COPILOT_DESKTOP_TEST_TARGET=installed-payload"
        )
    if os.environ.get("APP_DIR"):
        raise AssertionError(
            "installed-payload journey must not set APP_DIR or launch checkout Electron"
        )
    if os.environ.get("COPILOT_FACADE_URL"):
        raise AssertionError(
            "journey must use Electron's embedded supervised facade, not "
            "COPILOT_FACADE_URL"
        )
    if native_dialogs and sys.platform != "darwin":
        raise JourneyBlocked(
            "native workspace grant/approval automation is currently implemented "
            "only for the macOS installed Desktop payload"
        )

    runtime = staged_runtime_dir(target=INSTALLED_PAYLOAD_TARGET)
    manifest_path = runtime / "staging-manifest.json"
    if not manifest_path.is_file():
        raise JourneyBlocked(
            "host staged runtime is absent; run make desktop-supervised or stage "
            "tools/desktop-runtime/stage.mjs for this host"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"staging manifest is malformed at {manifest_path}"
        ) from exc
    if manifest.get("host_exec") is not True:
        raise JourneyBlocked(
            "staged runtime is not host-executable; re-stage it for this host"
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
        raise JourneyBlocked(
            "staged runtime is incomplete (missing " + ", ".join(missing) + ")"
        )


def require_deterministic_supervisor() -> None:
    if os.environ.get("GENUI_DETERMINISTIC_SUPERVISED_READY") != "stdio-v1":
        raise JourneyBlocked(
            "installed Desktop does not currently propagate the env-gated "
            "deterministic model to supervised ai-backend; set "
            "GENUI_DETERMINISTIC_SUPERVISED_READY=stdio-v1 only after that "
            "packaged harness lane exists"
        )


def require_local_fixture_bridge() -> None:
    if not FIXTURE_SERVER.is_file() or not SCENARIO_PATH.is_file():
        raise JourneyBlocked("checked-in local communications fixture is incomplete")
    scenario = load_scenario()
    if scenario.get("namespace") != FIXTURE_NAMESPACE:
        raise AssertionError("local communications fixture namespace changed")
    workspace = scenario.get("workspace")
    if (
        not isinstance(workspace, dict)
        or workspace.get("root") != FIXTURE_WORKSPACE_ROOT
    ):
        raise AssertionError("local communications fixture workspace root changed")
    _assert_scenario_identities_are_local(scenario)
    if os.environ.get("GENUI_LOCAL_FIXTURE_BRIDGE") != "stdio-v1":
        raise JourneyBlocked(
            "the public facade cannot yet register/execute the checked-in local "
            "stdio fixture in a fresh installed Desktop profile; set "
            "GENUI_LOCAL_FIXTURE_BRIDGE=stdio-v1 only after the URL-only MCP "
            "registration and process-lifetime gaps are implemented"
        )


def require_binary_docx_artifacts() -> None:
    if os.environ.get("GENUI_BINARY_ARTIFACTS_READY") != "docx-v1":
        raise JourneyBlocked(
            "binary DOCX publication/preview is not yet a model-visible artifact "
            "contract (publish_artifact currently accepts inline text only and "
            "the document renderer supports Markdown/plain text); set "
            "GENUI_BINARY_ARTIFACTS_READY=docx-v1 only after the packaged "
            "publication, preview, export, and fallback lane exists"
        )


def require_deterministic_docx_phases() -> None:
    raise JourneyBlocked(
        "the deterministic binary-DOCX harness cannot yet switch create, "
        "revise, and workspace-stage scripts across one persisted profile"
    )


def _live_provider(journey_id: str) -> tuple[str, str]:
    requested = (
        os.environ.get(
            f"{journey_id}_PROVIDER",
            os.environ.get("GENUI_PROVIDER", "auto"),
        )
        .strip()
        .lower()
    )
    if requested not in {"auto", "openai", "anthropic"}:
        raise AssertionError(
            f"{journey_id}_PROVIDER/GENUI_PROVIDER must be auto, openai, or anthropic"
        )
    candidates = (requested,) if requested != "auto" else ("openai", "anthropic")
    for provider in candidates:
        try:
            return provider, load_env_key(provider)
        except SystemExit:
            continue
    label = requested if requested != "auto" else "OpenAI or Anthropic"
    raise JourneyBlocked(
        f"no local {label} BYOK value is available through load_env_key"
    )


def build_pass_config(journey_id: str, slug: str, mode: JourneyMode) -> PassConfig:
    preflight_installed_supervisor(native_dialogs=journey_id in {"G3", "G4", "G10"})
    provider: str | None = None
    key: str | None = None
    if mode is JourneyMode.DETERMINISTIC:
        require_deterministic_supervisor()
        if journey_id == "G4":
            require_deterministic_docx_phases()
    else:
        provider, key = _live_provider(journey_id)
    token = f"{slug}-{mode.value}-{uuid4().hex}"
    return PassConfig(
        journey_id=journey_id,
        slug=slug,
        mode=mode,
        provider=provider,
        key=key,
        user_data_token=token,
    )


def run_matrix(
    journey_id: str,
    slug: str,
    runner: Callable[[PassConfig], None],
    *,
    argv: list[str] | None = None,
    needs_fixture: bool = False,
    needs_docx: bool = False,
) -> int:
    try:
        modes = requested_modes(journey_id, argv)
        if needs_fixture:
            require_local_fixture_bridge()
        if needs_docx:
            require_binary_docx_artifacts()
    except JourneyBlocked as exc:
        emit_result(journey_id, "blocked", reason=str(exc))
        return EXIT_BLOCKED

    for mode in modes:
        try:
            config = build_pass_config(journey_id, slug, mode)
        except JourneyBlocked as exc:
            emit_result(journey_id, "blocked", mode=mode, reason=str(exc))
            return EXIT_BLOCKED
        emit_result(
            journey_id,
            "running",
            mode=config.mode,
            reason=(
                "installed supervised deterministic pass"
                if config.mode is JourneyMode.DETERMINISTIC
                else f"installed supervised BYOK pass; provider={config.provider}"
            ),
        )
        runner(config)
        emit_result(journey_id, "passed", mode=config.mode)
    return 0


@contextmanager
def journey_environment(
    mode: JourneyMode,
    *,
    deterministic_tool: tuple[str, Mapping[str, Any]] | None = None,
    deterministic_parallel_tools: list[dict[str, Any]] | None = None,
) -> Iterator[None]:
    changed = (
        set(STUDIO_ENVIRONMENT)
        | set(SECRET_ENVIRONMENT_NAMES)
        | set(DEV_OVERRIDE_ENVIRONMENT_NAMES)
        | set(FAKE_MODEL_ENVIRONMENT_NAMES)
        | {"COPILOT_PRODUCTION"}
    )
    previous = {name: os.environ.get(name) for name in changed}
    os.environ.update(STUDIO_ENVIRONMENT)
    for name in SECRET_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    for name in DEV_OVERRIDE_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    for name in FAKE_MODEL_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    os.environ["COPILOT_PRODUCTION"] = "1"
    if mode is JourneyMode.DETERMINISTIC:
        os.environ["RUNTIME_FAKE_MODEL"] = "1"
        if deterministic_tool is not None:
            name, arguments = deterministic_tool
            os.environ["RUNTIME_FAKE_MODEL_TOOL_CALLS"] = "1"
            os.environ["RUNTIME_FAKE_MODEL_TOOL_NAME"] = name
            os.environ["RUNTIME_FAKE_MODEL_TOOL_ARGS"] = json.dumps(
                dict(arguments), sort_keys=True, separators=(",", ":")
            )
        if deterministic_parallel_tools:
            os.environ["RUNTIME_FAKE_MODEL_TOOL_CALLS"] = "1"
            os.environ["RUNTIME_FAKE_MODEL_PARALLEL_TOOL_CALLS"] = json.dumps(
                deterministic_parallel_tools,
                sort_keys=True,
                separators=(",", ":"),
            )
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def new_session(config: PassConfig, *, phase: str = "main") -> DriverSession:
    """Create a phase-specific driver while pinning one unique profile per pass."""

    session = DriverSession(
        name=f"{config.run_name}-{phase}",
        installed_payload=True,
    )
    session.user_data_subdir = f"journey-{config.user_data_token}"
    session._env["COPILOT_DESKTOP_USER_DATA_SUBDIR"] = session.user_data_subdir
    return session


def bootstrap_session(
    session: DriverSession,
    config: PassConfig,
    *,
    screenshot_prefix: str,
) -> None:
    status = session.rpc("status")
    assert status.get("target") == INSTALLED_PAYLOAD_TARGET, (
        "journey did not launch the installed Desktop payload"
    )
    assert status.get("posture") == "prod", "Desktop driver is not in prod posture"
    assert_main_production_posture(session)
    session.sign_in_local()
    session.shot(f"{screenshot_prefix}-{config.mode.value}-sign-in")
    if config.mode is JourneyMode.LIVE:
        assert config.provider is not None and config.key is not None
        session.ftue_add_key(config.provider, config.key)
        catalog = session.transport("GET", "/v1/agent/models")
        models = catalog.get("models", [])
        assert any(
            isinstance(model, dict)
            and model.get("provider") == config.provider
            and model.get("configured") is True
            for model in models
        ), "authenticated facade did not configure the entered BYOK provider"
    else:
        assert session.wait_for("[data-testid=first-run-skip]"), (
            "deterministic first-run skip did not render"
        )
        session.click("[data-testid=first-run-skip]")
        assert session.wait_for("[data-testid=composer-textarea]"), (
            "deterministic first-run path did not reveal a composer"
        )
    session.shot(f"{screenshot_prefix}-{config.mode.value}-composer")


def _evaluate_json(session: DriverSession, javascript: str) -> Any:
    raw = session.evaluate(javascript)
    assert isinstance(raw, str), "renderer IPC did not return JSON"
    if raw.startswith("ERR:"):
        raise AssertionError("renderer IPC rejected the journey action")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError("renderer IPC returned malformed JSON") from exc


def ipc_invoke(session: DriverSession, channel: str, payload: Mapping[str, Any]) -> Any:
    return _evaluate_json(
        session,
        "(async()=>{try{const value=await window.bridge.ipc.invoke("
        f"{json.dumps(channel)},{json.dumps(dict(payload))});"
        "return JSON.stringify(value);}catch(error){return 'ERR:'+error.message;}})()",
    )


def assert_main_production_posture(session: DriverSession) -> None:
    posture = ipc_invoke(session, "auth.get-posture", {})
    assert posture == {"productionPosture": True}, (
        "Electron main did not attest production supervisor posture"
    )


def transport_json(
    session: DriverSession,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
) -> Any:
    request: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        request["body"] = dict(body)
    return _evaluate_json(
        session,
        "(async()=>{try{const result=await window.bridge.ipc.invoke("
        f'"transport.request",{json.dumps(request)});'
        'if(result&&result.kind==="transport-result"){'
        'if(!result.ok)return "ERR:HTTP "+String(result.error?.status??"unknown");'
        "return JSON.stringify(result.value);}"
        "return JSON.stringify(result);"
        '}catch(error){return "ERR:"+error.message;}})()',
    )


def transport_error_status(
    session: DriverSession,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
) -> int:
    """Return an expected facade error status; fail if the request succeeds."""

    request: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        request["body"] = dict(body)
    result = _evaluate_json(
        session,
        "(async()=>{try{const result=await window.bridge.ipc.invoke("
        f'"transport.request",{json.dumps(request)});'
        'if(result?.kind!=="transport-result")return JSON.stringify({ok:true});'
        "return JSON.stringify({ok:result.ok,status:result.error?.status??null});"
        '}catch(error){return "ERR:"+error.message;}})()',
    )
    assert isinstance(result, dict) and result.get("ok") is False, (
        "facade unexpectedly accepted an operation that must fail closed"
    )
    status = result.get("status")
    assert isinstance(status, int), "facade error did not expose an HTTP status"
    return status


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


def desktop_process_id(session: DriverSession) -> int:
    process_id = session.rpc("status").get("pid")
    assert isinstance(process_id, int) and process_id > 0, (
        "desktop driver did not expose its Electron process id"
    )
    return process_id


def _native_automation(command: list[str], *, action: str) -> subprocess.Popen[bytes]:
    if sys.platform != "darwin":
        raise AssertionError(f"native {action} automation requires macOS")
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_native(
    process: subprocess.Popen[bytes], *, action: str, timeout_s: int = 30
) -> None:
    try:
        exit_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError(f"native {action} automation timed out") from exc
    assert exit_code == 0, (
        f"native {action} automation failed; no grant or approval was assumed"
    )


def grant_workspace(
    session: DriverSession,
    root: Path,
    *,
    label: str,
) -> str:
    process_id = desktop_process_id(session)
    picker = _native_automation(
        [
            "/usr/bin/osascript",
            "-e",
            _FOLDER_PICKER_APPLESCRIPT,
            "--",
            str(root),
            str(process_id),
        ],
        action="folder picker",
    )
    try:
        grant = ipc_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": "read_write_no_delete", "label": label},
        )
    finally:
        _wait_native(picker, action="folder picker")
    assert isinstance(grant, dict), "folder picker did not yield a grant"
    assert set(grant) == {"grantId", "mode", "label", "status"}, (
        "renderer grant leaked host authority fields or changed shape"
    )
    assert grant.get("mode") == "read_write_no_delete"
    assert grant.get("status") == "active"
    grant_id = grant.get("grantId")
    assert isinstance(grant_id, str) and grant_id, "folder grant omitted grantId"
    return grant_id


def approve_native_workspace_stage(session: DriverSession) -> None:
    process_id = desktop_process_id(session)
    confirmation = _native_automation(
        [
            "/usr/bin/osascript",
            "-e",
            _APPROVAL_APPLESCRIPT,
            "--",
            str(process_id),
        ],
        action="workspace approval",
    )
    try:
        session.click("[data-testid=tc-workspace-stage-approve]")
    finally:
        _wait_native(confirmation, action="workspace approval")


def wait_for_conversation_id(session: DriverSession, timeout_s: int = 60) -> str:
    deadline = time.time() + timeout_s
    last_route = ""
    while time.time() < deadline:
        last_route = str(session.evaluate("window.location.hash") or "")
        match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", last_route)
        if match is not None:
            return match.group(1)
        time.sleep(0.25)
    raise AssertionError(f"conversation route never bound; got {last_route!r}")


def create_conversation(
    session: DriverSession,
    *,
    title: str,
) -> str:
    created = transport_json(
        session, "POST", "/v1/agent/conversations", body={"title": title}
    )
    assert isinstance(created, dict), "conversation create returned a non-object"
    conversation_id = created.get("conversation_id")
    assert isinstance(conversation_id, str) and conversation_id, (
        "conversation create omitted conversation_id"
    )
    session.evaluate(f"window.location.hash={json.dumps(f'#/convo/{conversation_id}')}")
    assert session.wait_for("[data-testid=composer-textarea]"), (
        "created conversation did not render its composer"
    )
    return conversation_id


def runs_for_conversation(
    session: DriverSession, conversation_id: str
) -> list[dict[str, Any]]:
    listing = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/runs"
    )
    runs = listing.get("runs", [])
    assert isinstance(runs, list) and all(isinstance(run, dict) for run in runs), (
        "facade run listing is malformed"
    )
    return runs


def wait_for_new_run(
    session: DriverSession,
    conversation_id: str,
    before_count: int,
    *,
    timeout_s: int = 120,
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = runs_for_conversation(session, conversation_id)
        if len(runs) > before_count:
            run_id = runs[0].get("run_id")
            assert isinstance(run_id, str) and run_id, "new run omitted run_id"
            return run_id
        time.sleep(0.5)
    raise AssertionError("Desktop did not persist a new run")


def wait_for_terminal_run(
    session: DriverSession,
    run_id: str,
    *,
    expected: str = "completed",
    timeout_s: int = 180,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        result = session.transport("GET", f"/v1/agent/runs/{run_id}")
        assert isinstance(result, dict), "run inspection returned a non-object"
        last = result
        status = result.get("status")
        if status in TERMINAL_STATUSES:
            assert status == expected, (
                f"run ended {status!r}, expected {expected!r}: "
                f"{result.get('safe_error')!r}"
            )
            return result
        time.sleep(0.5)
    raise AssertionError(f"run did not terminate; last status={last.get('status')!r}")


def replay_events(session: DriverSession, run_id: str) -> list[dict[str, Any]]:
    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = replay.get("events", [])
    assert isinstance(events, list) and all(
        isinstance(event, dict) for event in events
    ), "event replay is malformed"
    previous = 0
    for event in events:
        sequence = event.get("sequence_no")
        assert isinstance(sequence, int) and sequence > previous, (
            "event replay is not strictly ordered"
        )
        previous = sequence
    return events


def run_prompt(
    session: DriverSession,
    conversation_id: str,
    prompt: str,
    *,
    expected_status: str = "completed",
) -> tuple[str, list[dict[str, Any]]]:
    before = len(runs_for_conversation(session, conversation_id))
    session.fill("[data-testid=composer-textarea]", prompt)
    session.click('button[aria-label="Send message"]')
    run_id = wait_for_new_run(session, conversation_id, before)
    wait_for_terminal_run(session, run_id, expected=expected_status)
    return run_id, replay_events(session, run_id)


def submit_prompt(
    session: DriverSession,
    conversation_id: str,
    prompt: str,
) -> str:
    """Submit through the real composer and return as soon as a run persists."""

    before = len(runs_for_conversation(session, conversation_id))
    session.fill("[data-testid=composer-textarea]", prompt)
    session.click('button[aria-label="Send message"]')
    return wait_for_new_run(session, conversation_id, before)


def event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def event_operations(events: list[dict[str, Any]]) -> list[str]:
    operations: list[str] = []
    for event in events:
        payload = event_payload(event)
        for key in EVENT_OPERATION_FIELDS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                operations.append(value)
    return operations


def assert_event_types(
    events: list[dict[str, Any]],
    *,
    required: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> None:
    event_types = [event.get("event_type") for event in events]
    for event_type in required:
        assert event_type in event_types, f"event replay omitted {event_type!r}"
    for event_type in forbidden:
        assert event_type not in event_types, (
            f"event replay unexpectedly contains {event_type!r}"
        )


def artifact_from_events(
    events: list[dict[str, Any]],
    *,
    kind: str,
) -> ArtifactReference:
    references: list[ArtifactReference] = []
    known_kinds: dict[str, str] = {}
    for event in events:
        if event.get("event_type") not in {"artifact.created", "artifact.revised"}:
            continue
        payload = event_payload(event)
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        payload_kind = payload.get("kind")
        if isinstance(payload_kind, str):
            known_kinds[artifact_id] = payload_kind
        if known_kinds.get(artifact_id) != kind:
            continue
        revision = payload.get("revision")
        content_ref = payload.get("content_ref")
        digest = payload.get("content_digest")
        assert isinstance(revision, int) and revision > 0
        assert isinstance(content_ref, str) and content_ref
        assert isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        references.append(
            ArtifactReference(
                artifact_id=artifact_id,
                revision=revision,
                kind=kind,
                content_ref=content_ref,
                content_digest=digest,
            )
        )
    assert references, f"run did not create/revise a {kind!r} artifact"
    return references[-1]


def artifact_detail(session: DriverSession, artifact_id: str) -> dict[str, Any]:
    detail = session.transport("GET", f"/v1/agent/artifacts/{artifact_id}")
    assert isinstance(detail, dict), "artifact detail is malformed"
    return detail


def read_artifact_bytes(
    session: DriverSession,
    artifact: ArtifactReference,
    *,
    limit: int = 2 * 1024 * 1024,
) -> bytes:
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
            if (bytes.length>{limit}) throw new Error("artifact exceeds journey bound");
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
        content = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise AssertionError("artifact stream returned invalid base64") from exc
    assert hashlib.sha256(content).hexdigest() == artifact.content_digest, (
        "artifact bytes do not match the facade ledger digest"
    )
    return content


def open_artifact_from_sources(session: DriverSession) -> None:
    if session.present("[data-testid=artifact-frame]"):
        return
    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Sources provenance rail did not render"
    )
    assert session.present("[data-testid=sources-v2-open-artifact]"), (
        "artifact provenance is not user-openable"
    )
    session.click("[data-testid=sources-v2-open-artifact]")
    assert session.wait_for("[data-testid=artifact-frame]"), (
        "opening artifact provenance did not render the artifact"
    )


def effect_stages(events: list[dict[str, Any]]) -> list[EffectStage]:
    stages: dict[str, EffectStage] = {}
    for event in events:
        payload = event_payload(event)
        event_type = event.get("event_type")
        if event_type == "effect.staged":
            stage_id = payload.get("stage_id")
            if not isinstance(stage_id, str) or not stage_id:
                continue
            stages[stage_id] = EffectStage(
                stage_id=stage_id,
                revision=1,
                proposal_digest=_required_digest(payload, "proposal_digest"),
                target_digest=_required_digest(payload, "target_digest"),
                target_ref=_required_text(payload, "target_ref"),
                executor=_required_text(payload, "executor"),
            )
        elif event_type == "effect.revised":
            stage_id = payload.get("stage_id")
            prior = stages.get(stage_id) if isinstance(stage_id, str) else None
            if prior is None:
                continue
            revision = payload.get("revision")
            assert isinstance(revision, int) and revision > prior.revision
            stages[stage_id] = EffectStage(
                stage_id=stage_id,
                revision=revision,
                proposal_digest=_required_digest(payload, "proposal_digest"),
                target_digest=_required_digest(payload, "target_digest"),
                target_ref=_required_text(payload, "target_ref"),
                executor=prior.executor,
            )
    return list(stages.values())


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    assert isinstance(value, str) and value, f"event payload omitted {key}"
    return value


def _required_digest(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    assert re.fullmatch(r"[0-9a-f]{64}", value), f"invalid {key} digest"
    return value


def assert_stage_decision(
    events: list[dict[str, Any]],
    stage: EffectStage,
    *,
    decision: str,
    applied: bool,
) -> None:
    decision_seen = False
    apply_seen = False
    for event in events:
        payload = event_payload(event)
        if payload.get("stage_id") != stage.stage_id:
            continue
        if event.get("event_type") == "effect.decision_recorded":
            if payload.get("decision") != decision:
                continue
            assert payload.get("revision") == stage.revision
            assert payload.get("proposal_digest") == stage.proposal_digest
            assert payload.get("target_digest") == stage.target_digest
            decision_seen = True
        elif event.get("event_type") == "effect.applied":
            assert decision_seen, "effect applied before its exact decision"
            assert payload.get("revision") == stage.revision
            apply_seen = True
    assert decision_seen, f"ledger omitted exact {decision!r} decision"
    assert apply_seen is applied, (
        "ledger application state disagrees with the expected terminal decision"
    )


def wait_for_stage_terminal(
    session: DriverSession,
    run_id: str,
    stage: EffectStage,
    *,
    decision: str,
    applied: bool,
    timeout_s: int = 120,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        events = replay_events(session, run_id)
        try:
            assert_stage_decision(events, stage, decision=decision, applied=applied)
        except AssertionError:
            time.sleep(0.5)
            continue
        return events
    raise AssertionError("effect stage did not reach its expected terminal decision")


def assert_no_execution_bypass(events: list[dict[str, Any]]) -> None:
    blocked = {
        "run_code_mode",
        "run_in_sandbox",
        "execute",
        "shell",
        "terminal",
        "python",
        "node",
        "npm",
    }
    leaked = sorted(
        marker
        for operation in event_operations(events)
        for marker in blocked
        if marker in operation.lower()
    )
    assert not leaked, f"journey used an inline execution bypass: {leaked}"


def assert_run_surfaces(
    session: DriverSession,
    run_id: str,
    *,
    required_kinds: set[str],
) -> list[dict[str, Any]]:
    response = session.transport("GET", f"/v1/agent/runs/{run_id}/surfaces")
    assert response.get("run_id") == run_id, "surface projection is run-misaligned"
    surfaces = response.get("surfaces", [])
    assert isinstance(surfaces, list) and all(
        isinstance(surface, dict) for surface in surfaces
    ), "surface projection is malformed"
    kinds = {
        surface.get("kind")
        for surface in surfaces
        if isinstance(surface.get("kind"), str)
    }
    assert required_kinds.issubset(kinds), (
        f"facade surfaces omitted required kinds {sorted(required_kinds - kinds)}"
    )
    return surfaces


def load_scenario() -> dict[str, Any]:
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "fixture scenario must be a JSON object"
    return raw


def _assert_scenario_identities_are_local(scenario: Mapping[str, Any]) -> None:
    serialized = json.dumps(scenario, sort_keys=True)
    for scheme in FIXTURE_REMOTE_SCHEMES:
        assert scheme not in serialized.lower(), (
            f"fixture scenario contains forbidden remote scheme {scheme}"
        )
    mail = scenario.get("mail")
    if isinstance(mail, dict):
        assert re.findall(r"[\w.+-]+@[\w.-]+", serialized)
        assert all(
            address.endswith("@fixture.invalid")
            for address in re.findall(r"[\w.+-]+@[\w.-]+", serialized)
        ), "fixture scenario contains a non-fixture email identity"


def register_local_fixture(session: DriverSession) -> FixtureRegistration:
    runtime_python = (
        staged_runtime_dir(target=INSTALLED_PAYLOAD_TARGET)
        / "python"
        / "bin"
        / "python3.13"
    )
    # This body drifted out of contract and now 422s. Four fields were wrong,
    # and every one of them is silent until something actually posts it:
    #
    #   url                     a stdio server is addressed by `stdio` ALONE
    #                           (`McpServerRecord._transport_matches_address`:
    #                           "a stdio server has no URL"), and the value sent
    #                           was `fixture://…`, which the URL validator
    #                           rejects outright — it admits only http/https.
    #   stdio.working_directory the field is `cwd` (`McpStdioRequest`).
    #   stdio.lifetime          not a field. `McpStdioRequest` is exactly
    #                           command / args / env / cwd.
    #   fixture_policy          not a field, and not anywhere in any service.
    #
    # That last one is worth being plain about rather than quietly deleting:
    # `fixture_policy` was never a server-enforced control. `network:
    # "disabled"` / `credentials: "rejected"` / `allowed_target_roots` read like
    # a sandbox the backend applied, and no backend ever saw them — the request
    # model forbids extras, so the whole call failed rather than the policy
    # being ignored. The fixture's safety comes from the fixture SERVER, which
    # is fixture-only by construction and reaches nothing external. Removing
    # these lines takes away a guarantee that was never in force.
    body = {
        "display_name": "Generative Workflows Local Fixture",
        "transport": "stdio",
        "auth_mode": "none",
        "stdio": {
            "command": str(runtime_python),
            "args": [str(FIXTURE_SERVER)],
            "cwd": str(HERE),
        },
    }
    created = transport_json(session, "POST", "/v1/mcp/servers", body=body)
    assert isinstance(created, dict), "fixture registration returned a non-object"
    assert created.get("transport") == "stdio"
    assert created.get("auth_state") in {"authenticated", "auth_skipped"}
    # A stdio server has no URL by contract, so the old
    # `startswith("fixture://")` assertion could only ever have passed against a
    # response shape the server no longer returns.
    assert created.get("url") is None, (
        f"a stdio server must have no url; got {created.get('url')!r}"
    )
    stdio = created.get("stdio")
    assert isinstance(stdio, dict) and stdio.get("command") == str(runtime_python), (
        f"facade did not persist the stdio launch config: {created}"
    )
    server_id = created.get("server_id")
    name = created.get("name")
    assert isinstance(server_id, str) and server_id
    assert isinstance(name, str) and name
    listing = session.transport("GET", "/v1/mcp/servers")
    servers = listing.get("servers", [])
    assert (
        isinstance(servers, list)
        and sum(
            isinstance(server, dict) and server.get("server_id") == server_id
            for server in servers
        )
        == 1
    ), "authenticated facade did not persist exactly one fixture server"
    return FixtureRegistration(server_id=server_id, name=name)


def scope_fixture_conversation(
    session: DriverSession,
    conversation_id: str,
    registration: FixtureRegistration,
) -> None:
    response = transport_json(
        session,
        "PATCH",
        f"/v1/agent/conversations/{conversation_id}/connectors",
        body={"scopes": {registration.name: ["read", "write"]}},
    )
    assert isinstance(response, dict)
    scopes = response.get("scopes")
    assert isinstance(scopes, dict)
    assert scopes.get(registration.name) == ["read", "write"], (
        "facade did not bind the fixture connector scope to the conversation"
    )


def assert_fixture_only_values(value: object) -> None:
    """Reject remote destinations in persisted events or fixture audit values."""

    if isinstance(value, str):
        lowered = value.lower()
        assert not lowered.startswith(FIXTURE_REMOTE_SCHEMES), (
            "journey observed a forbidden remote destination"
        )
        return
    if isinstance(value, list):
        for nested in value:
            assert_fixture_only_values(nested)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in {"target", "destination", "url", "endpoint", "receipt"}:
                if isinstance(nested, str):
                    assert nested.startswith("fixture://"), (
                        f"non-fixture {key} escaped the local connector"
                    )
            assert_fixture_only_values(nested)


def fixture_tool_payloads(
    events: list[dict[str, Any]], operation: str
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for event in events:
        payload = event_payload(event)
        values = {
            str(payload.get(key))
            for key in EVENT_OPERATION_FIELDS
            if payload.get(key) is not None
        }
        if operation in values:
            assert_fixture_only_values(payload)
            matches.append(payload)
    return matches


def assert_fixture_operations(
    events: list[dict[str, Any]],
    *,
    required: tuple[str, ...],
    forbidden: tuple[str, ...] = (),
) -> None:
    operations = event_operations(events)
    for operation in required:
        assert operation in operations, f"fixture run omitted {operation!r}"
    for operation in forbidden:
        assert operation not in operations, (
            f"fixture run unexpectedly invoked {operation!r}"
        )
    for event in events:
        assert_fixture_only_values(event_payload(event))


def extract_fixture_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = fixture_tool_payloads(events, "fixture_audit")
    assert candidates, "run did not persist a fixture_audit tool result"
    for payload in reversed(candidates):
        for key in ("output", "result", "result_payload", "structured_content"):
            value = payload.get(key)
            if isinstance(value, dict) and isinstance(value.get("entries"), list):
                assert value.get("valid") is True, "fixture audit hash chain is invalid"
                assert_fixture_only_values(value)
                return value
    raise AssertionError("fixture_audit result was not durably available in replay")


def audit_operations(audit: Mapping[str, Any]) -> list[str]:
    entries = audit.get("entries", [])
    assert isinstance(entries, list)
    operations: list[str] = []
    for entry in entries:
        assert isinstance(entry, dict)
        operation = entry.get("operation")
        assert isinstance(operation, str) and operation
        operations.append(operation)
    return operations


def edit_staged_draft(session: DriverSession, revised_body: str) -> int:
    assert session.wait_for("[data-testid=tc-staged-draft]"), (
        "staged draft surface did not render"
    )
    before = str(
        session.evaluate(
            'document.querySelector("[data-testid=tc-staged-draft-rev]").textContent'
        )
        or ""
    )
    match = re.search(r"rev\s+([1-9][0-9]*)", before)
    assert match is not None, "staged draft did not expose its revision"
    prior_revision = int(match.group(1))
    session.click("[data-testid=tc-staged-draft-edit]")
    assert session.wait_for("[data-testid=tc-staged-draft-editor]")
    session.fill("[data-testid=tc-staged-draft-editor]", revised_body)
    session.click("[data-testid=tc-staged-draft-save]")
    deadline = time.time() + 60
    while time.time() < deadline:
        text = str(
            session.evaluate(
                'document.querySelector("[data-testid=tc-staged-draft-rev]")?.textContent||""'
            )
        )
        next_match = re.search(r"rev\s+([1-9][0-9]*)", text)
        if next_match is not None and int(next_match.group(1)) > prior_revision:
            return int(next_match.group(1))
        time.sleep(0.5)
    raise AssertionError("staged draft edit did not persist a new revision")


def assert_receipt(session: DriverSession, *, expected_text: tuple[str, ...]) -> None:
    assert session.wait_for("[data-testid=receipt-v2-launch]"), (
        "terminal run did not expose a receipt launcher"
    )
    session.click("[data-testid=receipt-v2-open]")
    assert session.wait_for("[data-testid=receipt-v2-surface]"), (
        "receipt launcher did not open a receipt surface"
    )
    text = str(
        session.evaluate(
            'document.querySelector("[data-testid=receipt-v2-surface]").innerText'
        )
        or ""
    )
    for expected in expected_text:
        assert expected in text, f"receipt omitted {expected!r}"


def assert_no_plaintext_secret(
    secret: str | None,
    roots: tuple[Path | None, ...],
) -> None:
    if secret is None:
        return
    needle = secret.encode("utf-8")
    assert needle, "BYOK value unexpectedly empty"
    for root in roots:
        if root is None or not root.exists():
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
                                "logs, screenshots, user data, or fixture files"
                            )
                        keep = max(0, len(needle) - 1)
                        previous = (previous + chunk)[-keep:] if keep else b""
            except OSError:
                continue


def assert_deterministic_model_attested(session: DriverSession) -> None:
    logs = tuple(session.run_dir.rglob("*.log"))
    assert logs, "deterministic pass produced no supervised logs"
    assert any(
        b"DETERMINISTIC FAKE MODEL" in path.read_bytes()
        for path in logs
        if path.is_file()
    ), "supervised ai-backend did not attest deterministic fake-model activation"


__all__ = [
    "ArtifactReference",
    "EffectStage",
    "FIXTURE_NAMESPACE",
    "FIXTURE_WORKSPACE_ROOT",
    "FixtureRegistration",
    "JourneyBlocked",
    "JourneyMode",
    "PassConfig",
    "SCENARIO_PATH",
    "ThrowawayJourneyRoot",
    "artifact_detail",
    "artifact_from_events",
    "assert_deterministic_model_attested",
    "assert_event_types",
    "assert_fixture_operations",
    "assert_fixture_only_values",
    "assert_main_production_posture",
    "assert_no_execution_bypass",
    "assert_no_plaintext_secret",
    "assert_receipt",
    "assert_run_surfaces",
    "assert_stage_decision",
    "audit_operations",
    "approve_native_workspace_stage",
    "bootstrap_session",
    "create_conversation",
    "edit_staged_draft",
    "effect_stages",
    "event_operations",
    "event_payload",
    "extract_fixture_audit",
    "fixture_tool_payloads",
    "grant_workspace",
    "ipc_invoke",
    "journey_environment",
    "load_scenario",
    "new_session",
    "open_artifact_from_sources",
    "read_artifact_bytes",
    "register_local_fixture",
    "replay_events",
    "require_binary_docx_artifacts",
    "require_deterministic_docx_phases",
    "require_local_fixture_bridge",
    "run_matrix",
    "run_prompt",
    "scope_fixture_conversation",
    "submit_prompt",
    "transport_error_status",
    "transport_json",
    "wait_for_conversation_id",
    "wait_for_new_run",
    "wait_for_stage_terminal",
    "wait_for_terminal_run",
]
