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
import traceback
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
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

    provider: "openai" | "anthropic" | "openrouter" | "google" | "virtuals"
    """
    var = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GOOGLE_API_KEY",
        # Named for the variable the Virtuals key ships under.
        "virtuals": "VIRTUALS_ACP_KEY",
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


def load_env_value(var: str, default: str) -> str:
    """Read a NON-SECRET setting from services/ai-backend/.env, else ``default``.

    Unlike :func:`load_env_key` this never raises — a missing .env (the normal
    case in a branch worktree) or an absent key yields ``default``. The process
    environment wins, so a journey can be pointed at a differently-configured
    stack without editing the file. Use this to derive an expected value from
    the deployment's own configuration instead of hardcoding a literal that
    goes stale on the next vendor release; never use it for key material.
    """
    override = os.environ.get(var, "").strip()
    if override:
        return override
    if DOTENV.exists():
        for line in DOTENV.read_text().splitlines():
            if line.startswith(f"{var}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return default


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
        # Drop the previous run's PNGs before this one writes any. Shots are
        # numbered from 1 each time, so a run that stops early leaves
        # higher-numbered files from an OLDER run sitting beside the current
        # ones — and `07-…png` from a build you have since changed looks
        # entirely convincing. Same trap the README documents for stale staged
        # runtimes, one directory down.
        for png in (self.run_dir / "screenshots").glob("*.png"):
            png.unlink()
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
        #: Set by ``JourneyPlan`` so a merged journey's screenshots say which
        #: phase produced them. One boot now carries a dozen phases, and
        #: ``07-transcript.png`` alone cannot tell you which claim it evidences.
        self.phase_prefix = ""

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
        """Call the driver, raising with the driver's OWN message on failure.

        The bare ``HTTPError`` this used to raise says only "HTTP Error 500:
        Internal Server Error", which is indistinguishable between "your
        selector matched nothing", "Playwright judged the element unactionable"
        and "the driver crashed". Diagnosing a journey failure then costs a
        whole re-run per hypothesis — measured, twice, on one `fillLast`.
        The driver already puts the real reason in the response body; this only
        stops throwing it away.
        """

        body = json.dumps({"cmd": cmd, **args}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/rpc",
            data=body,
            headers={"content-type": "application/json"},
        )
        try:
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace").strip()
            except Exception:  # noqa: BLE001 — the original error still wins
                pass
            raise RuntimeError(
                f"driver rpc {cmd} failed ({exc.code}): {detail or exc.reason}"
            ) from exc

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
        """Wait until the selector EXISTS in the DOM. See `wait_visible`.

        Built on `present`, i.e. `querySelector`, which finds nodes the user
        cannot see. That is the right check for "has this rendered yet" and the
        wrong one before typing or clicking — see `wait_visible`.
        """

        for _ in range(timeout_s * 2):
            if self.present(selector):
                return True
            time.sleep(0.5)
        return False

    def wait_visible(self, selector: str, timeout_s: int = 30) -> bool:
        """Wait until the selector is VISIBLE, the way Playwright judges it.

        `wait_for` cannot distinguish "the composer is ready" from "the composer
        is underneath the Settings surface": `querySelector` finds a hidden node
        just as happily, and a DOM `.click()` on one still fires its handler, so
        a journey can drive a screen the user is not looking at and record
        convincing evidence that it worked.

        Measured, in FS-H: Settings did not close, `present()` said the composer
        was there, the pill DID switch to Bypass on a hidden element, and the
        next `fill` spent 15s on "element is not visible" before failing. Use
        this before any interaction whose success depends on the element
        actually being on screen.
        """

        try:
            self.rpc(
                "waitFor",
                selector=selector,
                timeoutMs=timeout_s * 1000,
                state="visible",
            )
            return True
        except Exception:  # noqa: BLE001 — absence is an answer, not an error
            return False

    def shot(self, label: str) -> None:
        self._shot += 1
        prefix = f"{self.phase_prefix}-" if self.phase_prefix else ""
        self.rpc("screenshot", name=f"{self._shot:02d}-{prefix}{label}")

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
    def wait_for_app_ready(self, timeout_s: int | None = None) -> float:
        """Block until the supervised boot screen is gone. Returns seconds waited.

        The control-server probe in :meth:`start` returns as soon as the FIRST
        WINDOW exists, and the first window is the boot screen
        (``BootProgress.tsx``, ``data-testid=boot-gate``). The services behind
        it — initdb, migrations, three uvicorns — are still coming up, and the
        renderer shows nothing else until they are healthy. So "the app
        launched" and "the app is usable" are two different moments, and
        everything a journey wants to click belongs to the second one.

        Measured on a warm M-series laptop against a staged runtime: **110s**
        from launch to the sign-in gate, of which ~9s was the window. A hosted
        macOS runner is slower still. The 60s default on :meth:`wait_for` is
        therefore below the real cold-boot cost — which is why this waits on
        ``BOOT_TIMEOUT_S``, the budget the harness ALREADY uses for exactly this
        question and which the caller can already raise.

        Two behaviours make a red run readable instead of merely red:

        * a fatal boot error (``boot-fatal``) fails IMMEDIATELY with the app's
          own message, rather than burning the whole budget to then report a
          timeout;
        * a timeout names the boot stage the app was stuck on, so "still
          Starting the local database after 260s" cannot be misread as a
          product assertion about the screen that never rendered.
        """

        budget = BOOT_TIMEOUT_S if timeout_s is None else timeout_s
        start = time.time()
        deadline = start + budget
        stage = ""
        while time.time() < deadline:
            if self.present("[data-testid=boot-fatal]"):
                message = self.evaluate(
                    "(document.querySelector('[data-testid=boot-fatal-message]')"
                    "?.innerText||'').trim()"
                )
                raise AssertionError(
                    f"the app reported a FATAL boot error after "
                    f"{time.time() - start:.0f}s: {message!r}. The supervised "
                    "services did not come up; their logs are under the run's "
                    "userData `logs/` dir, not in the renderer."
                )
            if not self.present("[data-testid=boot-gate]"):
                return time.time() - start
            stage = (
                self.evaluate(
                    "(document.querySelector('[data-testid=boot-message]')"
                    "?.innerText||'').trim()"
                )
                or stage
            )
            time.sleep(0.5)
        raise AssertionError(
            f"the app was still booting after {budget}s — last stage: {stage!r}. "
            "This is the BOX, not the product: raise BOOT_TIMEOUT_S, or read the "
            "supervised services' logs to see which one never became healthy."
        )

    def sign_in_local(self) -> None:
        """Sign-in gate → "Use locally, no account" (the no-signup device account).

        Waits the boot screen out first. Without that this asserted on a DOM
        that was still showing `boot-gate`, and reported the honest-looking
        "sign-in gate never appeared" — a harness timeout wearing a product
        failure's clothes, and the same trap `driver.mjs` documents for
        `electron.launch`.
        """
        waited = self.wait_for_app_ready()
        print(f"[{self.name}] app ready after {waited:.0f}s of boot", flush=True)
        assert self.wait_for("[data-testid=sign-in-button]"), (
            "sign-in gate never appeared (the boot screen had already cleared, "
            "so this one IS about the sign-in surface)"
        )
        self.click("[data-testid=sign-in-button]")

    def ftue_add_key(self, provider: str, key: str) -> None:
        """FTUE gate → "Add a key" → paste → Connect. Never logs the key.

        There is no provider toggle any more: the form infers the provider from
        the key. We still take the expected `provider` so a journey states which
        one it MEANT, and we assert the form agreed — a silent mis-inference
        would otherwise store the key under the wrong slug and the journey would
        blame the model catalog. When nothing was inferred the fallback picker
        is open, so we choose there.
        """
        assert self.wait_for("[data-testid=first-run-add-key]"), (
            "FTUE key card never appeared"
        )
        # Virtuals is the default journey provider, so the catalog race in
        # `wait_for_virtuals_catalog` is now on the common path rather than in
        # one journey. Doing it here means every journey inherits the fix by
        # construction instead of each remembering to wait.
        if provider == "virtuals":
            waited = wait_for_virtuals_catalog(self)
            print(f"  virtuals catalog ready after ~{waited:.0f}s", flush=True)
        self.click("[data-testid=first-run-add-key]")
        assert self.wait_for("[data-testid=first-run-keyform]")
        self.fill("[data-testid=first-run-key-input]", key)  # value never printed
        # Blur takes the verdict without waiting out the idle debounce.
        self.press("[data-testid=first-run-key-input]", "Tab")
        assert self.wait_for("[data-testid=first-run-key-resolved]", 10), (
            "the key never resolved to a provider row"
        )
        if self.present("[data-testid=first-run-key-picker]"):
            self.click(f"[data-testid=first-run-key-pick-{provider}]")
        settled = self.evaluate(
            "(document.querySelector('[data-testid=first-run-key-resolved]')"
            "||{}).getAttribute?.('data-provider')"
        )
        assert settled == provider, (
            f"key resolved to {settled!r}, expected {provider!r}"
        )
        self.click("[data-testid=first-run-key-connect]")
        assert self.wait_for("[data-testid=first-run-composer]", 60), (
            "key connect did not reveal the composer"
        )
        # The composer reveals as soon as the key is saved, but the model
        # selection is NOT settled yet: the provider-keys port wraps its save
        # with `refreshCatalog(provider)` (FirstRunGate.tsx), and that refetch
        # of /v1/agent/models resolves a tick or two later. Reading the pill in
        # the caller's next statement therefore races it and sees the
        # pre-key value — the unselected placeholder, or whatever default was
        # resolved while every cloud row still said "needs key". That is a
        # harness race, not a product bug, and it produced a false failure that
        # cost real debugging time. Wait for the selection to settle.
        assert self.wait_model_pill_resolved(), (
            "model pill never resolved after the key was added"
        )
        # Opt-in cost control for a full-suite run: every journey that sends a
        # message otherwise runs on whatever the picker auto-selects, which is
        # the mid tier. Set COPILOT_JOURNEY_MODEL to pin the cheapest model the
        # provider publishes. Harness-only — no product code reads it, so a
        # normal run is unaffected.
        preferred = os.environ.get("COPILOT_JOURNEY_MODEL", "").strip()
        if preferred:
            assert self.select_model(preferred), (
                f"could not select the requested journey model {preferred!r}"
            )

    def select_model(self, name_fragment: str, timeout_s: int = 20) -> bool:
        """Open the composer's model picker and choose the matching row.

        Matches on the visible row name, case-insensitively, so a caller can
        pass "haiku" rather than the exact catalog label. Returns False when no
        enabled row matches — a keyless row is not selectable, and silently
        continuing on the wrong model would misreport what was exercised.

        POLLS rather than looking once. `wait_model_pill_resolved` returns as
        soon as the pill stops showing its placeholder, which can be BEFORE the
        just-added provider's rows land in the catalog. Opening the menu at that
        instant shows a shorter list, and a single look concluded "no such
        model" for a model that was about to appear — a race that passed
        standalone and failed under the load of a full-suite run.
        """

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.click(".atlas-model-pill")
            if self.wait_for(".atlas-model-pill__item", 5):
                clicked = self.evaluate(
                    """
                    (() => {
                      const want = %r.toLowerCase();
                      const rows = [...document.querySelectorAll('.atlas-model-pill__item')];
                      const row = rows.find((r) => {
                        const nm = r.querySelector('.atlas-model-pill__nm');
                        return nm && nm.innerText.toLowerCase().includes(want)
                          && !r.disabled;
                      });
                      if (!row) return null;
                      row.click();
                      return row.innerText;
                    })()
                    """
                    % name_fragment
                )
                if clicked:
                    return self.wait_model_pill_contains(name_fragment, timeout_s)
            # Close the popover before retrying: a stacked-open menu swallows
            # the next click, and the catalog may still be refreshing.
            self.evaluate("document.body.click()")
            time.sleep(1.0)
        return False

    def wait_model_pill_contains(self, fragment: str, timeout_s: int = 20) -> bool:
        """Wait until the pill's own label reflects the chosen model."""

        want = fragment.lower()
        for _ in range(timeout_s * 2):
            text = (
                self.evaluate(
                    '(document.querySelector(".atlas-model-pill")||{}).innerText||""'
                )
                or ""
            ).lower()
            if want in text:
                return True
            time.sleep(0.5)
        return False

    #: Pill text while nothing is selected — `ModelPill` renders the trigger
    #: with an aria-label of "Select a model" and this short visible label.
    _UNRESOLVED_MODEL_PILL = frozenset({"", "model", "select a model"})

    def wait_model_pill_resolved(self, timeout_s: int = 30) -> bool:
        """Wait until the composer's model pill names an actual model.

        Returns True when there is no pill to wait on — a surface that renders
        no model picker (a local-model-only flow, say) has nothing to settle,
        and blocking for the full timeout there would turn this guard into the
        flake it exists to remove.
        """

        for _ in range(timeout_s * 2):
            if not self.present(".atlas-model-pill"):
                return True
            text = (
                self.evaluate(
                    '(document.querySelector(".atlas-model-pill")||{}).innerText||""'
                )
                or ""
            ).strip()
            if text.lower() not in self._UNRESOLVED_MODEL_PILL:
                return True
            time.sleep(0.5)
        return False

    def send_first_run_message(self, text: str) -> None:
        """Type + send in the FTUE composer."""
        self.fill("[data-testid=composer-textarea]", text)
        time.sleep(0.3)
        self.click('button[aria-label="Send message"]')

    def send(self, text: str, *, timeout_s: int = 180) -> None:
        """Send from WHICHEVER composer is on screen, waiting for an idle one.

        Every journey used to own the first message in its boot, so
        ``send_first_run_message`` was always right. Grouped phases share a
        boot, and only the first of them meets the FTUE composer — the rest
        send into the run cockpit. Both are `composer-textarea`, so the only
        real difference is that the run composer is BUSY while a run streams:
        the send button is `aria-label="Stop response"` in flight, and filling
        the textarea mid-stream loses the text to a re-render.
        """

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.evaluate(
                "!!document.querySelector('button[aria-label=\"Send message\"]') && "
                "!document.querySelector('button[aria-label=\"Stop response\"]')"
            ):
                break
            time.sleep(1)
        else:
            raise AssertionError(f"no idle composer within {timeout_s}s")
        assert self.wait_visible("[data-testid=composer-textarea]", 30), (
            "the composer textarea never became visible"
        )
        self.fill("[data-testid=composer-textarea]", text)
        time.sleep(0.3)
        self.click('button[aria-label="Send message"]')

    def open_destination(self, aria_label: str) -> None:
        """Click a left nav-rail destination, e.g. "Chats" / "Run".

        Matches the BADGED form too. `AppRail` renders
        `aria-label={showBadge ? f"{label} ({count})" : label}`, so the moment a
        run is live the Run item becomes "Run (1)" and an exact-label selector
        stops matching — precisely when the app has something to report, which
        is when the later phases of a journey run. The two alternatives are
        mutually exclusive (an item is badged or it is not), so this still
        resolves to exactly one element and stays strict-mode safe.
        """
        self.click(
            f'[data-destination][aria-label="{aria_label}"], '
            f'[data-destination][aria-label^="{aria_label} ("]'
        )
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


# ── shared run/preflight helpers ─────────────────────────────────────────────
# These began as private functions inside `g2_csv_lifecycle`, and were then
# imported ACROSS set boundaries (`focus-inline-artifacts` reached into
# `generative-workflows` to get them). Every merged journey that drives a real
# run needs them, so they belong here.

#: Statuses a run can end on. Anything else means it is still moving.
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
)

#: Plaintext provider keys the LAUNCHING SHELL may have exported. The desktop
#: supervisor forwards these, so a journey that wants to reproduce a true
#: keyless first-run has to clear them.
SECRET_ENVIRONMENT_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
)

#: The lane the artifact/workspace journeys need switched on.
ARTIFACT_JOURNEY_ENVIRONMENT = {
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM": "1",
    "SURFACES_V2": "true",
    "ARTIFACT_EFFECTS_V2": "true",
    "ARTIFACT_DRAFTS_V2": "true",
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}


def preflight_staged_runtime(*, target: str = SOURCE_TARGET) -> None:
    """Require the real supervised services, or raise ``PhaseSkipped``.

    ``target`` selects WHICH stage to require, and must match the target the
    caller's ``DriverSession`` will launch. Gating on the other one would skip
    on a perfectly good stage — or, worse, green-light a stage that is not
    under test.
    """

    runtime = staged_runtime_dir(target=target)
    manifest_path = runtime / "staging-manifest.json"
    if not manifest_path.is_file():
        raise PhaseSkipped(
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
        raise PhaseSkipped(
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
        raise PhaseSkipped(
            "staged runtime is incomplete (missing " + ", ".join(missing) + ")"
        )


# Virtuals leads the chain: it is the default provider for journey runs.
#
# Not an arbitrary preference. It is the gateway the product is actually sold
# through, and the only provider whose path has broken *silently* — `ChatOpenAI`
# documents that it neither extracts nor preserves the non-standard
# `reasoning_content` Virtuals streams, so the runtime billed reasoning tokens it
# could never display and nothing raised (see `virtuals_thinking.py`). Direct
# OpenAI/Anthropic keys exercise a first-party path that cannot catch that class
# of bug. They stay in the chain as fallbacks so a machine with no Virtuals key
# still runs the suite instead of skipping it.
BYOK_PROVIDER_CHAIN: tuple[str, ...] = ("virtuals", "openai", "anthropic")


def byok_provider(*, env_var: str = "JOURNEY_PROVIDER") -> tuple[str, str]:
    """Pick a provider that actually has a local key. Never returns the key alone."""

    requested = os.environ.get(env_var, "auto").strip().lower()
    if requested not in {"auto", *BYOK_PROVIDER_CHAIN}:
        raise AssertionError(
            f"{env_var} must be auto or one of {', '.join(BYOK_PROVIDER_CHAIN)}"
        )
    providers = (requested,) if requested != "auto" else BYOK_PROVIDER_CHAIN
    for provider in providers:
        try:
            return provider, load_env_key(provider)
        except SystemExit:
            # load_env_key emits only a variable/path; never a provider key.
            continue
    label = (
        requested
        if requested != "auto"
        else f"{', '.join(BYOK_PROVIDER_CHAIN[:-1])} or {BYOK_PROVIDER_CHAIN[-1]}"
    )
    raise PhaseSkipped(
        f"no local {label} BYOK key is available through services/ai-backend/.env"
    )


def wait_for_virtuals_catalog(session: DriverSession, timeout_s: int = 60) -> float:
    """Block until the model catalog carries Virtuals rows. Returns seconds waited.

    This is not padding, and it must run BEFORE the key is saved.
    ``VirtualsModelSource`` never fetches on the request path, so a fresh
    profile's first catalog read returns nothing and only *schedules* the fetch.
    The FTUE refetches the catalog exactly once, immediately after the key is
    saved — so a driver that pastes a key two seconds after boot beats the
    snapshot, caches a Virtuals-free catalog, and never asks again. Measured:
    rows appear ~2s in, the driver saved at ~2s. A human is slower than a driver,
    which is why this only ever bit automation.

    Returning rather than asserting is deliberate: a journey that merely needs a
    working key should not fail because the catalog was slow, and one that asserts
    on Virtuals rows (``virtuals_connected``) does its own checking afterwards.
    """
    waited = 0.0
    while waited < timeout_s:
        models = session.transport("GET", "/v1/agent/models").get("models", [])
        if any(m.get("provider") == "virtuals" for m in models):
            break
        time.sleep(1)
        waited += 1
    return waited


def runs_for_conversation(session: DriverSession, conversation_id: str) -> list[dict]:
    listing = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/runs"
    )
    runs = listing.get("runs", [])
    assert isinstance(runs, list), "facade run list omitted its run array"
    assert all(isinstance(run, dict) for run in runs), "facade run list is malformed"
    return runs


def wait_for_conversation_id(
    session: DriverSession, timeout_s: int = 60, excluding: str | None = None
) -> str:
    """Wait until the composer submission binds the new conversation.

    A fresh profile intentionally has no conversation route before the user
    sends their first message; the UI creates and selects that conversation as
    one atomic submission flow.

    ``excluding`` is REQUIRED for any send that follows a "New chat", and the
    reason is a real defect this helper walked into. `openNewRun`
    (`apps/desktop/renderer/bootstrap.tsx`) clears the bound conversation in
    React state but does NOT navigate — only `onConversationCreated` does — so
    the hash still reads `#/convo/<the conversation you just left>` until the
    send lazily creates the next one. Polling the raw hash therefore returns
    the PREVIOUS conversation whenever the read wins that race, and the caller
    then asserts against a run that finished minutes ago. Naming the outgoing
    id turns "a conversation is bound" into "a NEW conversation is bound",
    which is what every caller after a `new_chat` actually means.
    """

    deadline = time.time() + timeout_s
    last_route = ""
    while time.time() < deadline:
        last_route = str(session.evaluate("window.location.hash") or "")
        match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", last_route)
        if match is not None and match.group(1) != excluding:
            return match.group(1)
        time.sleep(0.25)
    raise AssertionError(
        f"the prompt did not bind a conversation route; got {last_route!r}"
        + (
            f" (still the conversation {excluding!r} it was told to leave)"
            if excluding
            else ""
        )
    )


def wait_for_new_run(
    session: DriverSession, conversation_id: str, before_count: int = 0
) -> str:
    deadline = time.time() + 120
    while time.time() < deadline:
        runs = runs_for_conversation(session, conversation_id)
        if len(runs) > before_count:
            run_id = runs[0].get("run_id")
            assert isinstance(run_id, str) and run_id, "facade run omitted run_id"
            return run_id
        time.sleep(0.5)
    raise AssertionError("the app did not create a run for the prompt")


def wait_for_terminal_run(session: DriverSession, run_id: str) -> dict:
    deadline = time.time() + 180
    last: dict = {}
    while time.time() < deadline:
        result = session.transport("GET", f"/v1/agent/runs/{run_id}")
        assert isinstance(result, dict), "run inspection returned a non-object response"
        last = result
        status = result.get("status")
        if status in TERMINAL_STATUSES:
            assert status == "completed", (
                f"agent run ended {status!r}: {result.get('safe_error')!r}"
            )
            return result
        time.sleep(0.5)
    raise AssertionError(
        f"run did not become terminal; last status={last.get('status')!r}"
    )


def assert_no_plaintext_secret(secret: str, roots: tuple[Path, ...]) -> None:
    """Search journey-owned files WITHOUT ever returning a matching secret value."""

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


# ── phase runner ─────────────────────────────────────────────────────────────
# One supervised boot costs initdb + migrations + three service starts, so the
# suite used to spend most of its wall clock booting: 64 scripts, 64 boots. The
# journeys are now grouped by what they need from the machine (target, profile,
# lane) and share one boot per group.
#
# Sharing a boot must not cost what a script per claim bought. Those were 64
# independent verdicts; a naive concatenation has ONE, aborts at the first
# assertion, and lets one absent prerequisite hide every later claim. So a boot
# runs PHASES: each is isolated, records its own outcome, and the next one runs
# regardless. The file's exit code is the aggregate, and the per-phase table is
# the thing you read.


class Outcome(StrEnum):
    """What became of one phase. Only ``PASSED`` is a pass."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PhaseBlocked(Exception):
    """A declared PRODUCT/harness capability is absent — exit `2` territory.

    Raise this when the thing under test cannot exist yet (no local stdio
    fixture bridge, no binary DOCX contract). It is a statement about the
    product, and it must never be dressed up as a pass.
    """


class PhaseSkipped(Exception):
    """A LOCAL prerequisite is absent — exit `3` territory.

    Raise this when this machine cannot run the phase (no staged runtime, no
    BYOK key in `.env`, no connected MCP server, no macOS native automation).
    It is a statement about the box, not about the product.
    """


def require(condition: object, reason: str) -> None:
    """Skip this phase unless a local prerequisite holds."""
    if not condition:
        raise PhaseSkipped(reason)


def blocked_unless(condition: object, reason: str) -> None:
    """Block this phase unless a declared product capability is present."""
    if not condition:
        raise PhaseBlocked(reason)


@dataclass
class PhaseResult:
    boot: str
    phase_id: str
    title: str
    outcome: Outcome
    seconds: float
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASSED


#: A phase is ``(id, one-line claim, callable)``. The callable takes the live
#: ``DriverSession`` and asserts; returning normally is the pass.
Phase = tuple[str, str, Callable[[DriverSession], None]]

#: Env var pinning WHICH phases this process may run (comma/space separated).
PHASE_SELECTOR_ENV = "JOURNEY_PHASES"


def selected_phase_ids() -> frozenset[str] | None:
    """The phase ids ``JOURNEY_PHASES`` pins, or ``None`` for "run them all".

    Since one boot now carries every phase in a file, running the file is the
    only way to get a claim — and those phases share that boot's state ON
    PURPOSE, so a later one inherits whatever route, rail, env and run history
    its predecessors left behind. A caller that wants exactly ONE claim (a
    per-PR CI job, a bisect, a bug reproduction) therefore cannot get it by
    running the file: it inherits state it never asked for and fails in a LATER
    phase with a symptom-shaped message that reads exactly like a product bug.

    ``JOURNEY_PHASES=FR-0`` runs FR-0 and DROPS the rest. Dropped, not recorded
    as skipped: a skipped phase is deliberately non-zero (see
    :attr:`JourneyPlan.exit_code`), so recording six phases the caller
    explicitly did not ask for would make every pinned run red. A boot with no
    selected phase is not booted at all — the whole point is to not pay for
    initdb + migrations + three uvicorns to run nothing.

    Matching is case-insensitive and whitespace-tolerant. An id that matches
    nothing is a hard failure rather than a quiet empty run: see
    :attr:`JourneyPlan.exit_code`. That is the difference between "CI runs one
    phase" and "CI runs zero phases and reports success", which is precisely the
    failure mode a per-PR e2e gate exists to not have.
    """

    raw = os.environ.get(PHASE_SELECTOR_ENV, "").strip()
    if not raw:
        return None
    ids = frozenset(
        token.strip().lower()
        for token in raw.replace(",", " ").split()
        if token.strip()
    )
    return ids or None


@contextmanager
def scoped_env(
    overrides: Mapping[str, str] | None = None,
    *,
    clear: Sequence[str] = (),
) -> Iterator[None]:
    """Apply env for the duration of a boot, then put the shell back.

    Unset means unset: a supervisor default is frequently the thing under test,
    so ``clear`` removes names outright rather than leaving whatever the calling
    shell happened to export.
    """

    names = {*(overrides or {}), *clear}
    previous = {name: os.environ.get(name) for name in names}
    for name in clear:
        os.environ.pop(name, None)
    os.environ.update(overrides or {})
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class JourneyPlan:
    """Runs grouped phases across one or more boots and reports one verdict.

    Typical shape of a merged journey::

        plan = JourneyPlan("workspace-consent")
        plan.boot(
            "default lane",
            lambda: DriverSession(name="workspace-consent"),
            setup=sign_in_with_key,          # a failure here skips the phases
            phases=[
                ("FS1", "an ungranted path never empty-succeeds", fs1),
                ("FS2", "the folder affordance is the composer bar", fs2),
            ],
        )
        raise SystemExit(plan.finish())
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.results: list[PhaseResult] = []
        self._started = time.time()
        #: Phase ids this run is pinned to, or ``None`` for all of them.
        self.selected = selected_phase_ids()
        #: Which pinned ids an actual declared phase answered to. Anything left
        #: over is a caller pointing at a phase that no longer exists.
        self._matched: set[str] = set()

    # -- recording --
    def _record(
        self,
        boot: str,
        phase_id: str,
        title: str,
        outcome: Outcome,
        seconds: float,
        detail: str = "",
    ) -> PhaseResult:
        result = PhaseResult(boot, phase_id, title, outcome, seconds, detail)
        self.results.append(result)
        mark = {
            Outcome.PASSED: "PASS",
            Outcome.FAILED: "FAIL",
            Outcome.BLOCKED: "BLOCKED",
            Outcome.SKIPPED: "SKIP",
        }[outcome]
        line = f"  [{mark:<7}] {phase_id:<10} {title} ({seconds:.1f}s)"
        print(line, flush=True)
        if detail:
            print(f"            → {detail}", flush=True)
        return result

    # -- execution --
    def run_phase(self, boot: str, phase: Phase, session: DriverSession) -> PhaseResult:
        """Run one phase in isolation. Never raises — the outcome is the return."""

        phase_id, title, fn = phase
        session.phase_prefix = phase_id.lower().replace(" ", "-")
        started = time.time()
        try:
            fn(session)
        except PhaseSkipped as exc:
            return self._record(
                boot, phase_id, title, Outcome.SKIPPED, time.time() - started, str(exc)
            )
        except PhaseBlocked as exc:
            return self._record(
                boot, phase_id, title, Outcome.BLOCKED, time.time() - started, str(exc)
            )
        except BaseException as exc:  # noqa: BLE001 — one phase must not end the run
            traceback.print_exc()
            detail = f"{type(exc).__name__}: {exc}".strip()
            return self._record(
                boot, phase_id, title, Outcome.FAILED, time.time() - started, detail
            )
        finally:
            session.phase_prefix = ""
        return self._record(
            boot, phase_id, title, Outcome.PASSED, time.time() - started
        )

    def boot(
        self,
        label: str,
        factory: Callable[[], DriverSession],
        *,
        phases: Sequence[Phase],
        setup: Callable[[DriverSession], None] | None = None,
        env: Mapping[str, str] | None = None,
        clear_env: Sequence[str] = (),
    ) -> None:
        """Launch one app, run every phase against it, tear it down.

        A boot failure, or a ``setup`` failure, marks the whole group rather
        than exploding: the phases record as skipped/failed with the reason and
        any LATER ``boot()`` call in the same file still runs. That is the point
        of grouping by prerequisite — one absent capability must cost its own
        group and nothing else.
        """

        # Pin BEFORE the factory runs. A boot with no selected phase must not
        # cost initdb + migrations + three uvicorns to then run nothing.
        phases = self._pin(phases)
        if not phases:
            print(
                f"\n── boot: {label} — skipped, "
                f"{PHASE_SELECTOR_ENV} pins no phase in this group",
                flush=True,
            )
            return

        print(f"\n── boot: {label} ({len(phases)} phases)", flush=True)
        with scoped_env(env, clear=clear_env):
            try:
                session = factory()
            except BaseException as exc:  # noqa: BLE001
                self._skip_group(label, phases, Outcome.FAILED, f"boot rejected: {exc}")
                return
            try:
                with session:
                    if setup is not None:
                        try:
                            setup(session)
                        except PhaseSkipped as exc:
                            self._skip_group(
                                label, phases, Outcome.SKIPPED, f"setup: {exc}"
                            )
                            return
                        except BaseException as exc:  # noqa: BLE001
                            traceback.print_exc()
                            self._skip_group(
                                label,
                                phases,
                                Outcome.FAILED,
                                f"setup failed: {type(exc).__name__}: {exc}",
                            )
                            return
                    for phase in phases:
                        self.run_phase(label, phase, session)
            except SystemExit as exc:
                # DriverSession.start raises SystemExit when the app never came
                # up. That is this group's verdict, not the file's.
                self._skip_group(label, phases, Outcome.FAILED, str(exc))
            except BaseException as exc:  # noqa: BLE001
                traceback.print_exc()
                self._skip_group(
                    label, phases, Outcome.FAILED, f"{type(exc).__name__}: {exc}"
                )

    def _pin(self, phases: Sequence[Phase]) -> Sequence[Phase]:
        """Narrow ``phases`` to what ``JOURNEY_PHASES`` selected. See
        :func:`selected_phase_ids` for why dropping beats skipping."""

        if self.selected is None:
            return phases
        kept = [phase for phase in phases if phase[0].strip().lower() in self.selected]
        self._matched.update(phase[0].strip().lower() for phase in kept)
        return kept

    def _skip_group(
        self,
        label: str,
        phases: Sequence[Phase],
        outcome: Outcome,
        reason: str,
    ) -> None:
        recorded = {(r.boot, r.phase_id) for r in self.results}
        for phase_id, title, _ in phases:
            if (label, phase_id) not in recorded:
                self._record(label, phase_id, title, outcome, 0.0, reason)

    # -- reporting --
    def counts(self) -> dict[str, int]:
        return {
            outcome.value: sum(1 for r in self.results if r.outcome is outcome)
            for outcome in Outcome
        }

    @property
    def unmatched_selection(self) -> tuple[str, ...]:
        """Pinned phase ids no declared phase answered to."""

        if self.selected is None:
            return ()
        return tuple(sorted(self.selected - self._matched))

    @property
    def exit_code(self) -> int:
        """`0` only when every phase ran and passed.

        Severity order is deliberate: a real defect outranks a missing product
        capability, which outranks a missing local prerequisite. A run with even
        one skipped phase is never `0`, because the file did not prove what its
        name claims.

        A ``JOURNEY_PHASES`` id that matched no declared phase outranks all of
        them and reports `1`. A pinned run is a caller asserting that a specific
        claim is checked; if renaming a phase silently turned that into an empty
        run, the caller — a per-PR CI gate, say — would go on reporting success
        while proving nothing. That is the exact pathology this harness exists
        to catch, so it must not be one.
        """

        if self.unmatched_selection:
            return 1
        outcomes = {r.outcome for r in self.results}
        if not self.results:
            return EXIT_SKIPPED
        if Outcome.FAILED in outcomes:
            return 1
        if Outcome.BLOCKED in outcomes:
            return EXIT_BLOCKED
        if Outcome.SKIPPED in outcomes:
            return EXIT_SKIPPED
        return 0

    def finish(self) -> int:
        """Print the phase table + machine-readable summary; return the exit code."""

        counts = self.counts()
        code = self.exit_code
        elapsed = time.time() - self._started
        unmatched = self.unmatched_selection
        if unmatched:
            print(
                f"\n!! {PHASE_SELECTOR_ENV} named "
                + ", ".join(repr(phase_id) for phase_id in unmatched)
                + f", which {self.name} does not declare — nothing was proven. "
                "Fix the caller (a CI job, a script) or restore the phase id."
            )
        print(f"\n══ {self.name} — {len(self.results)} phases in {elapsed:.0f}s")
        for result in self.results:
            if not result.ok:
                print(
                    f"   {result.outcome.value.upper():<8} {result.phase_id:<10} "
                    f"{result.title}" + (f" — {result.detail}" if result.detail else "")
                )
        print(
            "   "
            + "  ".join(f"{name}={value}" for name, value in counts.items())
            + f"  → exit {code}"
        )
        print(
            json.dumps(
                {
                    "journey": self.name,
                    "outcome": (
                        "passed"
                        if code == 0
                        else {1: "failed", 2: "blocked", 3: "skipped"}[code]
                    ),
                    "counts": counts,
                    "seconds": round(elapsed, 1),
                    **(
                        {"selected_phases": sorted(self.selected)}
                        if self.selected is not None
                        else {}
                    ),
                    **({"unmatched_phases": list(unmatched)} if unmatched else {}),
                    "phases": [
                        {
                            "boot": r.boot,
                            "id": r.phase_id,
                            "title": r.title,
                            "outcome": r.outcome.value,
                            "seconds": round(r.seconds, 1),
                            **({"detail": r.detail} if r.detail else {}),
                        }
                        for r in self.results
                    ],
                }
            ),
            flush=True,
        )
        return code
