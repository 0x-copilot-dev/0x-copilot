#!/usr/bin/env python3
"""Shared harness for the desktop user-journey scripts.

These journeys drive the REAL packaged 0xCopilot desktop app (the supervised
Electron + embedded-Postgres + three-Python-services stack) as a user would,
through the Playwright control server in ``tools/cli-testing/harness/driver.mjs``.
Each journey spawns its own driver, walks a user flow by clicking real testIds,
asserts the outcome, screenshots each step, and tears the app down.

Nothing here talks to the services directly — every action is a DOM interaction
or an authenticated call made THROUGH the running app (see ``transport``), so a
green journey proves the real end-to-end wiring, not a mock.

Usage (see the root README.md for full setup):

    from _lib import DriverSession, load_env_key

    with DriverSession(name="my-journey") as s:
        s.sign_in_local()                     # "Use locally, no account"
        s.ftue_add_key("anthropic", load_env_key("anthropic"))
        s.send_first_run_message("write a haiku")
        assert s.on_run(), "expected to land on the run"

SECURITY: provider keys are read from services/ai-backend/.env via
``load_env_key`` and passed straight into the app's password field. They are
NEVER printed, logged, or committed. Only lengths / status codes are ever shown.
"""

from __future__ import annotations

import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
# _lib.py lives at <repo>/tools/desktop-journeys/_lib.py
REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tools" / "cli-testing" / "harness" / "driver.mjs"
# A branch worktree normally does not contain the ignored local ``.env`` file.
# Let a journey explicitly point at the checkout that owns that developer-local
# file while preserving the repo-local path as the ordinary, safe default.  The
# override is intentionally a path only: ``load_env_key`` still returns the
# value directly to a password field and never logs it.
DOTENV = Path(
    os.environ.get(
        "COPILOT_JOURNEY_DOTENV", REPO_ROOT / "services" / "ai-backend" / ".env"
    )
)
RUNS_DIR = Path(__file__).resolve().parent / "runs"

CTL_PORT = int(os.environ.get("CTL_PORT", "8790"))
BOOT_TIMEOUT_S = int(
    os.environ.get("BOOT_TIMEOUT_S", "260")
)  # first boot = initdb + migrations

# ── journey targets + exit codes ─────────────────────────────────────────────
SOURCE_TARGET = "source"
INSTALLED_PAYLOAD_TARGET = "installed-payload"

# A preflight skip is NOT a pass: the journey never ran, so it must not be
# indistinguishable from success to a shell, a CI step, or an agent. `2` is
# already taken by the G3–G10 "blocked" contract (a declared capability is
# absent), so an absent local prerequisite reports `3`. Assertion failures stay
# `1` via the normal traceback.
EXIT_SKIPPED = 3
EXIT_BLOCKED = 2


# ── staged runtime location ──────────────────────────────────────────────────
def host_runtime_key() -> str:
    """`<platform>-<arch>` — the staged runtime subdir for THIS host."""
    machine = platform.machine().lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(machine, machine)
    return f"{sys.platform}-{arch}"


def resolve_copilot_home(
    *, target: str = SOURCE_TARGET, override: str | None = None
) -> Path:
    """Resolve COPILOT_HOME (the staged-runtime root) for a journey target.

    The default is target-dependent, and deliberately so — the two targets stage
    to different places and are not interchangeable:

    * ``source`` drives the checkout's ``apps/desktop`` build, whose supervisor
      reads the repo-local stage at ``apps/desktop/resources``.
    * ``installed-payload`` drives the globally installed ``@0x-copilot/cli``,
      which stages to ``~/.0xcopilot``. Falling back to the checkout there would
      make an "installed" journey prove the source tree instead of the shipped
      artifact, so that fallback must never exist.

    An explicit ``override`` (or ``COPILOT_HOME``) still wins for both — that is
    how an isolated worktree reuses a stage owned by the primary checkout.

    This is the single source of truth: ``DriverSession`` resolves the env it
    launches with through here, so a journey's preflight and its session always
    agree about which runtime is under test.
    """
    if override:
        return Path(override)
    from_env = os.environ.get("COPILOT_HOME")
    if from_env:
        return Path(from_env)
    if target == INSTALLED_PAYLOAD_TARGET:
        return Path.home() / ".0xcopilot"
    return REPO_ROOT / "apps" / "desktop" / "resources"


def staged_runtime_dir(
    *, target: str = SOURCE_TARGET, override: str | None = None
) -> Path:
    """The host-specific staged runtime a journey's preflight must inspect."""
    return (
        resolve_copilot_home(target=target, override=override)
        / "runtime"
        / host_runtime_key()
    )


# ── secure key loading ───────────────────────────────────────────────────────
def load_env_key(provider: str) -> str:
    """Read a provider key from services/ai-backend/.env. Never prints it.

    provider: "openai" | "anthropic" | "openrouter" | "google"
    """
    var = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GOOGLE_API_KEY",
    }[provider]
    if not DOTENV.exists():
        raise SystemExit(f"{DOTENV} not found — cannot load {var}")
    for line in DOTENV.read_text().splitlines():
        if line.startswith(f"{var}="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if not val:
                raise SystemExit(f"{var} is empty in {DOTENV}")
            return val
    raise SystemExit(f"{var} not present in {DOTENV}")


# ── driver session ───────────────────────────────────────────────────────────
class DriverSession:
    """Spawns driver.mjs, waits for the app, exposes the /rpc control API.

    env overrides (all optional):
      APP_DIR       – electron app dir (default: <repo>/apps/desktop). Point at a
                      worktree's apps/desktop to verify a branch build.
      COPILOT_HOME  – staged runtime dir. The default depends on the target:
                      <repo>/apps/desktop/resources for `source`, ~/.0xcopilot
                      for `installed-payload` (see `resolve_copilot_home`).
                      Frontend-only changes can reuse main's staged services.
      COPILOT_DESKTOP_TEST_TARGET=installed-payload – launch the globally installed
                      @0x-copilot/cli payload and its own Electron binary. This
                      intentionally rejects APP_DIR so the journey cannot silently
                      exercise the checkout instead of the npm artifact.
      CTL_PORT      – control port (default 8790).
    """

    def __init__(
        self,
        name: str,
        *,
        fresh: bool = True,
        app_dir: str | None = None,
        copilot_home: str | None = None,
        installed_payload: bool = False,
    ):
        self.name = name
        self.port = CTL_PORT
        self.run_dir = RUNS_DIR / name
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._shot = 0
        env = dict(os.environ)
        env["CTL_PORT"] = str(self.port)
        env["POSTURE"] = "prod"
        env["RUN_DIR"] = str(self.run_dir)
        target = env.get("COPILOT_DESKTOP_TEST_TARGET", SOURCE_TARGET)
        if installed_payload:
            target = INSTALLED_PAYLOAD_TARGET
        env["COPILOT_DESKTOP_TEST_TARGET"] = target
        if target == INSTALLED_PAYLOAD_TARGET:
            if app_dir is not None:
                raise ValueError(
                    "app_dir cannot be used with installed_payload=True; "
                    "that journey must launch the installed npm payload"
                )
            # An installed CLI stages to ~/.0xcopilot. `resolve_copilot_home`
            # honours an explicit COPILOT_HOME for isolated installs but never
            # substitutes the source checkout's resources — that would
            # invalidate the artifact test.
            env.pop("APP_DIR", None)
        else:
            env["APP_DIR"] = (
                app_dir or env.get("APP_DIR") or str(REPO_ROOT / "apps" / "desktop")
            )
        env["COPILOT_HOME"] = str(
            resolve_copilot_home(target=target, override=copilot_home)
        )
        # A throwaway userData subdir ⇒ a fresh first-run every time.
        suffix = str(time.time_ns()) if fresh else "reuse"
        self.user_data_subdir = f"journey-{name}-{suffix}"
        env["COPILOT_DESKTOP_USER_DATA_SUBDIR"] = self.user_data_subdir
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._log = None

    @property
    def _user_data_dir(self) -> Path:
        """The app-private data directory for this exact test invocation."""
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "0xCopilot"
        elif sys.platform == "win32":
            root = (
                Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
                / "0xCopilot"
            )
        else:
            root = (
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                / "0xCopilot"
            )
        return root / self.user_data_subdir

    # -- lifecycle --
    def __enter__(self) -> "DriverSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._free_port()
        self._log = open(self.run_dir / "driver.log", "w")
        self._proc = subprocess.Popen(
            ["node", str(DRIVER)],
            env=self._env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT),
            start_new_session=True,
        )
        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if self._probe():
                return
            if self._proc.poll() is not None:
                self._cleanup_failed_launch()
                raise SystemExit(
                    f"[{self.name}] driver exited before the app came up "
                    f"(see {self.run_dir}/driver.log)"
                )
            time.sleep(2)
        self._cleanup_failed_launch()
        raise SystemExit(
            f"[{self.name}] app did not come up within {BOOT_TIMEOUT_S}s "
            f"(see {self.run_dir}/driver.log)"
        )

    def stop(self) -> None:
        quit_cleanly = False
        try:
            self.rpc("quit")
            quit_cleanly = True
        except Exception:
            pass
        cleanup_needed = not quit_cleanly
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # The driver did not complete its normal `app.close()` shutdown.
                # These are only ever pids rooted in this session's fresh data dir.
                cleanup_needed = True
        if cleanup_needed:
            self._cleanup_failed_launch()
        self._free_port()
        self._close_log()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None

    def _owned_pids(self) -> set[int]:
        """Read pids only from this invocation's driver and supervised logs."""
        pids: set[int] = set()
        driver_log = self.run_dir / "driver.log"
        try:
            pids.update(
                int(value)
                for value in re.findall(
                    r"<launched> pid=(\\d+)", driver_log.read_text()
                )
            )
        except OSError:
            pass
        for path in (self._user_data_dir / "logs").glob("*.log"):
            try:
                pids.update(
                    int(value)
                    for value in re.findall(
                        r"Started server process \[(\\d+)\]", path.read_text()
                    )
                )
            except OSError:
                continue
        return pids

    @staticmethod
    def _stop_pid(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _stop_owned_postgres(self) -> None:
        pgdata = self._user_data_dir / "pgdata"
        if not (pgdata / "postmaster.pid").exists():
            return
        executable = "pg_ctl.exe" if sys.platform == "win32" else "pg_ctl"
        runtime = Path(self._env["COPILOT_HOME"]) / "runtime"
        candidates = list(runtime.glob(f"*/postgres/bin/{executable}"))
        if len(candidates) != 1:
            return
        subprocess.run(
            [
                str(candidates[0]),
                "-D",
                str(pgdata),
                "-m",
                "fast",
                "-w",
                "-t",
                "20",
                "stop",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _cleanup_failed_launch(self) -> None:
        """Tear down a launch that failed before the driver exposed `/rpc`.

        Playwright can time out after Electron has already spawned the embedded
        runtime.  There is no RPC handle in that state, so normal app shutdown
        is impossible.  Scope cleanup to this invocation's random userData
        subdirectory; a real user's app and database are never considered.
        """
        for pid in self._owned_pids():
            self._stop_pid(pid)
        self._stop_owned_postgres()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._close_log()

    def _free_port(self) -> None:
        # Best-effort: kill any lingering driver/electron holding the port.
        subprocess.run(
            [
                "bash",
                "-c",
                f"lsof -nP -iTCP:{self.port} -sTCP:LISTEN -t 2>/dev/null | xargs -r kill -9 2>/dev/null",
            ],
            check=False,
        )

    # -- rpc --
    def rpc(self, cmd: str, **args) -> dict:
        body = json.dumps({"cmd": cmd, **args}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/rpc",
            data=body,
            headers={"content-type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=40).read())

    def _probe(self) -> bool:
        try:
            return (
                self.rpc("pageEval", js="typeof window.bridge").get("value") == "object"
            )
        except Exception:
            return False

    def evaluate(self, js: str):
        r = self.rpc("pageEval", js=js)
        return r.get("value") if r.get("ok") else None

    def click(self, selector: str) -> None:
        self.rpc("click", selector=selector)

    def fill(self, selector: str, value: str) -> None:
        self.rpc("fill", selector=selector, value=value)

    def press(self, selector: str, key: str) -> None:
        """Focus ``selector`` and send a real keyboard key through Playwright.

        Rich-card journeys use this alongside pointer clicks so the desktop
        accessibility contract is exercised, not merely the visual state.
        """
        self.rpc("press", selector=selector, key=key)

    def present(self, selector: str) -> bool:
        return bool(self.evaluate(f"!!document.querySelector({json.dumps(selector)})"))

    def wait_for(self, selector: str, timeout_s: int = 60) -> bool:
        for _ in range(timeout_s * 2):
            if self.present(selector):
                return True
            time.sleep(0.5)
        return False

    def shot(self, label: str) -> None:
        self._shot += 1
        self.rpc("screenshot", name=f"{self._shot:02d}-{label}")

    def resize(self, width: int, height: int) -> dict:
        """Resize the REAL desktop window's content area, as a user dragging it would.

        Returns the driver's ``{requested, applied, viewport}``. Use this to
        exercise layout that only breaks on a short/narrow window (internal
        scroll regions, `vh`-sized surfaces). The window manager may refuse a
        size, so assert on the reported ``viewport`` rather than the request.
        """
        return self.rpc("resizeWindow", width=width, height=height)

    def document_scroll(self) -> dict:
        """Measure whether the DOCUMENT can scroll.

        The desktop window is a fixed application frame — every scroll region
        lives inside `.desktop-window-frame`, so the document itself must never
        be scrollable (apps/desktop/renderer/desktop.css, invariant 3). A
        non-zero overflow here means some element escaped the frame's clip and
        the whole shell can be scrolled out of the window.
        """
        return self.evaluate(
            "(()=>{const d=document.documentElement,b=document.body;return{"
            "scrollHeight:d.scrollHeight,clientHeight:d.clientHeight,"
            "scrollWidth:d.scrollWidth,clientWidth:d.clientWidth,"
            "scrollTop:d.scrollTop,scrollLeft:d.scrollLeft,"
            "bodyScrollHeight:b.scrollHeight,bodyClientHeight:b.clientHeight,"
            "innerHeight:window.innerHeight,innerWidth:window.innerWidth};})()"
        )

    def transport(self, method: str, path: str):
        """Make an authenticated facade call THROUGH the app (the app attaches the
        session bearer). e.g. transport("GET", "/v1/agent/models")."""
        js = (
            '(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",'
            f'{{method:"{method}",path:"{path}"}});'
            'if(r&&r.kind==="transport-result"){'
            'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"unknown")+'
            '" "+String(r.error?.message??"request failed");'
            "return JSON.stringify(r.value);}"
            "return JSON.stringify(r);}"
            'catch(e){return "ERR:"+e.message}})()'
        )
        raw = self.evaluate(js)
        if isinstance(raw, str) and raw.startswith("ERR:"):
            raise RuntimeError(raw)
        return json.loads(raw)

    # -- common user actions (real testIds; keep in sync with the app) --
    def sign_in_local(self) -> None:
        """Sign-in gate → "Use locally, no account" (the no-signup device account)."""
        assert self.wait_for("[data-testid=sign-in-button]"), (
            "sign-in gate never appeared"
        )
        self.click("[data-testid=sign-in-button]")

    def ftue_add_key(self, provider: str, key: str) -> None:
        """FTUE gate → "Add a key" → pick provider → paste → Connect. Never logs the key."""
        label = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
        }[provider]
        assert self.wait_for("[data-testid=first-run-add-key]"), (
            "FTUE key card never appeared"
        )
        self.click("[data-testid=first-run-add-key]")
        assert self.wait_for("[data-testid=first-run-keyform]")
        self.click(f'[role=radio]:has-text("{label}")')
        time.sleep(0.3)
        self.fill("[data-testid=first-run-key-input]", key)  # value never printed
        self.click("[data-testid=first-run-key-connect]")
        assert self.wait_for("[data-testid=first-run-composer]", 60), (
            "key connect did not reveal the composer"
        )

    def send_first_run_message(self, text: str) -> None:
        """Type + send in the FTUE composer."""
        self.fill("[data-testid=composer-textarea]", text)
        time.sleep(0.3)
        self.click('button[aria-label="Send message"]')

    def open_destination(self, aria_label: str) -> None:
        """Click a left nav-rail destination, e.g. "Chats" / "Run"."""
        self.click(f'[aria-label="{aria_label}"][data-destination]')
        time.sleep(2)

    def on_run(self) -> bool:
        return bool(
            self.evaluate(
                '!!document.querySelector("[data-testid=tc-chat]") && '
                'document.querySelectorAll("[data-testid^=tc-chat-message-]").length>0'
            )
        )

    def run_mode(self) -> str | None:
        return self.evaluate(
            '(document.querySelector("[data-testid=thread-canvas]")||{}).getAttribute&&'
            'document.querySelector("[data-testid=thread-canvas]").getAttribute("data-mode")'
        )

    def model_pill(self) -> str | None:
        return self.evaluate(
            '(document.querySelector(".atlas-model-pill")||{}).innerText||null'
        )
