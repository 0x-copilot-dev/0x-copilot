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
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
# _lib.py lives at <repo>/tools/desktop-journeys/_lib.py
REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tools" / "cli-testing" / "harness" / "driver.mjs"
DOTENV = REPO_ROOT / "services" / "ai-backend" / ".env"
RUNS_DIR = Path(__file__).resolve().parent / "runs"

CTL_PORT = int(os.environ.get("CTL_PORT", "8790"))
BOOT_TIMEOUT_S = int(
    os.environ.get("BOOT_TIMEOUT_S", "260")
)  # first boot = initdb + migrations


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
      COPILOT_HOME  – staged runtime dir (default: <repo>/apps/desktop/resources).
                      Frontend-only changes can reuse main's staged services.
      CTL_PORT      – control port (default 8790).
    """

    def __init__(
        self,
        name: str,
        *,
        fresh: bool = True,
        app_dir: str | None = None,
        copilot_home: str | None = None,
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
        env["APP_DIR"] = (
            app_dir or env.get("APP_DIR") or str(REPO_ROOT / "apps" / "desktop")
        )
        env["COPILOT_HOME"] = (
            copilot_home
            or env.get("COPILOT_HOME")
            or str(REPO_ROOT / "apps" / "desktop" / "resources")
        )
        # A throwaway userData subdir ⇒ a fresh first-run every time.
        suffix = str(int(time.time())) if fresh else "reuse"
        env["COPILOT_DESKTOP_USER_DATA_SUBDIR"] = f"journey-{name}-{suffix}"
        self._env = env
        self._proc: subprocess.Popen | None = None

    # -- lifecycle --
    def __enter__(self) -> "DriverSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._free_port()
        log = open(self.run_dir / "driver.log", "w")
        self._proc = subprocess.Popen(
            ["node", str(DRIVER)],
            env=self._env,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT),
        )
        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if self._probe():
                return
            time.sleep(2)
        raise SystemExit(
            f"[{self.name}] app did not come up within {BOOT_TIMEOUT_S}s "
            f"(see {self.run_dir}/driver.log)"
        )

    def stop(self) -> None:
        try:
            self.rpc("quit")
        except Exception:
            pass
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGKILL)
        self._free_port()

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

    def transport(self, method: str, path: str):
        """Make an authenticated facade call THROUGH the app (the app attaches the
        session bearer). e.g. transport("GET", "/v1/agent/models")."""
        js = (
            '(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",'
            f'{{method:"{method}",path:"{path}"}});return JSON.stringify(r.value||r);}}'
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
