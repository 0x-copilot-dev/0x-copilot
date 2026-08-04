#!/usr/bin/env python3
"""SF-1 — the inference floor, end to end on the real packaged app.

**The claim under test.** A connector nobody wrote a spec for still renders a
legible, shaped surface — with no model call and no provider credential involved
in the shaping — and the ledger's provenance agrees with what is drawn.

**Why this is a journey and not a unit test.** The generative-UI audit that
motivated this work found four independent breaks between a matched spec and the
screen, and ~13,000 unit tests were green over every one of them. Each break sat
at an injected seam — a scheduler, a store, a completion port, a tuple half — and
tests inject past seams. Two of them are only observable here:

* the spec is rebuilt field-by-field by ``RuntimeEventPresentationProjector``
  before it is persisted, so an emitter test and a fold test can both pass while
  the allow-list silently strips it;
* the PRESENT stage only runs for a tool the policy layer classified READ, which
  depends on a real MCP descriptor arriving over a real transport.

**What is real and what is a double.** Everything inside the app is real: the
real MCP client, the real per-tool middleware stack, the real ``SurfaceProjector``
climbing the real ladder, the real ``WorkLedgerEmitter``, the real hydration
endpoint, the real renderers. The single substitution is the vendor on the far
end — a loopback MCP server (``tools/desktop-journeys/surface-floor/fixture_mcp.py``) — because every
connector in the desktop profile requires an OAuth authorization that an
automated journey must not complete in the user's name. That substitution is at
the network boundary, which is the same place the Hermes harness draws it.

**Prerequisites**

1. ``node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64``
   (re-stage after ANY ``services/*`` change — the stage is a snapshot)
2. ``npm run build --workspace @0x-copilot/desktop``
3. the fixture server running: ``python tools/desktop-journeys/surface-floor/fixture_mcp.py``
4. a provider key in ``services/ai-backend/.env``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import DriverSession, load_env_key  # noqa: E402

FIXTURE_URL = "http://127.0.0.1:8931/mcp"
ASK = (
    "Use the incidents connector to list the open incidents, then stop and "
    "show me what came back. Do not summarise them in prose."
)


class FloorJourney:
    """Drives the app to a real MCP read and judges the surface it produces."""

    def __init__(self, session: DriverSession) -> None:
        self.session = session
        self.findings: list[str] = []

    # -- helpers ---------------------------------------------------------

    def post(self, path: str, body: dict) -> object:
        """Authenticated POST through the app. ``_lib.transport`` is GET-shaped."""

        payload = json.dumps({"method": "POST", "path": path, "body": body})
        js = (
            "(async()=>{try{const r=await window.bridge.ipc.invoke("
            f'"transport.request",{payload});'
            'if(r&&r.kind==="transport-result"){'
            'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"?")+" "'
            '+String(r.error?.message??"");'
            "return JSON.stringify(r.value);}return JSON.stringify(r);}"
            'catch(e){return "ERR:"+e.message}})()'
        )
        raw = self.session.evaluate(js)
        if isinstance(raw, str) and raw.startswith("ERR:"):
            raise RuntimeError(f"POST {path} -> {raw}")
        return json.loads(raw)

    def note(self, ok: bool, claim: str) -> bool:
        self.findings.append(f"{'PASS' if ok else 'FAIL'}  {claim}")
        return ok

    # -- steps -----------------------------------------------------------

    def register_fixture(self) -> str:
        """Register the loopback connector. No OAuth: ``auth_mode: none``."""

        created = self.post(
            "/v1/mcp/servers",
            {
                "url": FIXTURE_URL,
                "display_name": "Incidents",
                "transport": "http",
                "auth_mode": "none",
            },
        )
        assert isinstance(created, dict), created
        server_id = str(created.get("id") or created.get("server_id") or "")
        assert server_id, f"no server id in {created}"
        return server_id

    def wait_for_surface(self, timeout_s: int = 180) -> bool:
        """A rendered archetype, not merely a finished run.

        Keyed on the renderer's own testid rather than on run status: a run can
        complete having rendered nothing, which is exactly the failure this
        journey exists to catch.
        """

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.session.present("[data-testid=table-renderer]") or (
                self.session.present("[data-testid=record-renderer]")
            ):
                return True
            time.sleep(2)
        return False

    def diagnose(self) -> None:
        """Split the last hop: does the SERVER serve state, and what URI does the
        CLIENT look it up under?

        The event on disk provably carries ``state{spec,source,data}``, and the
        renderer provably shows none. Exactly two links remain between them, and
        this prints both rather than reasoning about either.
        """

        run_id = self.session.evaluate(
            "(()=>{const el=document.querySelector('[data-run-id]');"
            "return el?el.getAttribute('data-run-id'):'';})()"
        )
        print(f"[diag] run_id from DOM: {run_id!r}")
        if run_id:
            try:
                served = self.session.transport(
                    "GET", f"/v1/agent/runs/{run_id}/surfaces"
                )
                for row in (served or {}).get("surfaces", []):
                    state = row.get("state")
                    print(
                        f"[diag] SERVER surface_id={row.get('surface_id')!r} "
                        f"state={'None' if state is None else sorted(state)}"
                    )
            except Exception as exc:  # noqa: BLE001 — diagnostic only
                print(f"[diag] surfaces endpoint failed: {exc}")

        diag = self.session.evaluate(
            "JSON.stringify((globalThis.__DIAG||[]).slice(-12))"
        )
        print("[diag] CLIENT trace:")
        try:
            for row in json.loads(diag or "[]"):
                print("   ", json.dumps(row))
        except Exception:  # noqa: BLE001 — diagnostic only
            print("   raw:", diag)

        tabs = self.session.evaluate(
            "(()=>{const t=[...document.querySelectorAll('[data-testid^=tc-tab]')]"
            ".map(e=>e.getAttribute('data-uri')||e.getAttribute('data-tab-uri')||e.textContent.trim());"
            "const slot=document.querySelector('[data-canvas-slot-testid=tc-surface-slot]');"
            "return JSON.stringify({tabs, activeUri: slot?slot.getAttribute('data-active-uri'):null});})()"
        )
        print(f"[diag] CLIENT tabs/activeUri: {tabs}")

    def read_surface(self) -> dict:
        """Read what is actually on screen, out of the live DOM."""

        js = """(()=>{
          const el = document.querySelector('[data-testid=table-renderer]')
                  || document.querySelector('[data-testid=record-renderer]');
          if(!el) return JSON.stringify({present:false});
          const q = (s)=>[...el.querySelectorAll(s)].map(n=>n.textContent.trim());
          return JSON.stringify({
            present:true,
            kind: el.getAttribute('data-testid'),
            spec: el.getAttribute('data-spec'),
            title: (el.querySelector('[data-testid=surface-title]')||{}).textContent,
            headers: q('th'),
            firstRow: q('[data-testid^=table-cell-0-]'),
            fieldLabels: q('[data-testid^=field-][data-testid$=-label]'),
            badges: q('[data-surface-format=badge]'),
            body: el.textContent.slice(0, 400),
          });
        })()"""
        return json.loads(self.session.evaluate(js))

    def run(self) -> int:
        s = self.session
        s.sign_in_local()
        s.ftue_add_key("openai", load_env_key("openai"))
        s.shot("01-signed-in")

        server_id = self.register_fixture()
        print(f"[floor] registered loopback connector: {server_id}")

        servers = s.transport("GET", "/v1/mcp/servers")
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        self.note(bool(rows), f"the connector is registered ({len(rows)} server(s))")
        s.shot("02-connector-registered")

        s.send_first_run_message(ASK)
        rendered = self.wait_for_surface()
        s.shot("03-after-run")

        if not self.note(rendered, "a surface renderer mounted on screen"):
            print("\n".join(self.findings))
            return 1

        self.diagnose()
        surface = self.read_surface()
        print("[floor] on-screen surface:", json.dumps(surface, indent=2)[:900])

        self.note(surface.get("spec") == "present", "the renderer received a SPEC")
        self.note(
            "No spec matched" not in (surface.get("body") or ""),
            "the retired apology does not appear",
        )
        title = (surface.get("title") or "").strip()
        self.note(title not in ("", "Untitled"), f"the header is titled ({title!r})")
        slots = surface.get("headers") or surface.get("fieldLabels") or []
        self.note(len(slots) >= 3, f"at least 3 bound slots ({slots})")
        self.note(bool(surface.get("badges")), "a low-cardinality value drew as a chip")

        s.shot("04-surface")
        print("\n".join(self.findings))
        return 0 if all(f.startswith("PASS") for f in self.findings) else 1


def main() -> int:
    with DriverSession("surface-floor") as session:
        return FloorJourney(session).run()


if __name__ == "__main__":
    raise SystemExit(main())
