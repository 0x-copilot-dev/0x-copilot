#!/usr/bin/env python3
"""FS-E — what MCP is actually configured in the app a user gets, and what is not.

The question this answers is deliberately narrow: is there a Linear MCP server
this session could really call, and if not, what IS there? It answers it from
the running app's own authenticated surfaces (`/v1/mcp/catalog`,
`/v1/mcp/servers`, `/v1/mcp/tools`) plus the Tools destination a user sees —
never from the source tree alone, because a catalog ENTRY is not a
configured SERVER.

It installs nothing and starts no OAuth. Connecting Linear means completing a
third-party authorization in the user's name; a journey that did that would be
granting consent nobody gave it, and the credentials do not exist here anyway.
So the honest outcome when nothing is connected is a REPORT, not a substituted
run that looks like an MCP call and isn't.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    lane,
    result,
    transport_json,
)

JOURNEY = "FS-E"
TOOLS_RAIL = '[aria-label="Tools"][data-destination]'


def _safe(session: DriverSession, path: str) -> Any:
    try:
        return transport_json(session, "GET", path)
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)[:300]}


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    evidence: dict[str, Any] = {}
    with lane(DEFAULT_LANE):
        session = DriverSession(name="fs-e-mcp-state")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                evidence["byok_provider"] = provider
                evidence["byok_key_len"] = len(key)  # length only, never the key

                catalog = _safe(session, "/v1/mcp/catalog")
                servers = _safe(session, "/v1/mcp/servers")
                tools = _safe(session, "/v1/mcp/tools")
                evidence["catalog_raw_keys"] = (
                    sorted(catalog)
                    if isinstance(catalog, dict)
                    else type(catalog).__name__
                )

                def entries(value: Any, *keys: str) -> list[dict[str, Any]]:
                    if isinstance(value, list):
                        return [v for v in value if isinstance(v, dict)]
                    if isinstance(value, dict):
                        for key_name in keys:
                            found = value.get(key_name)
                            if isinstance(found, list):
                                return [v for v in found if isinstance(v, dict)]
                    return []

                catalog_entries = entries(catalog, "entries", "catalog", "servers")
                server_entries = entries(servers, "servers", "items")
                tool_entries = entries(tools, "tools", "items")

                evidence["catalog_slugs"] = sorted(
                    str(e.get("slug") or e.get("id") or e.get("display_name"))
                    for e in catalog_entries
                )
                evidence["linear_catalog_entry"] = next(
                    (e for e in catalog_entries if str(e.get("slug")) == "linear"), None
                )
                evidence["installed_servers"] = [
                    {
                        k: v
                        for k, v in e.items()
                        if k
                        in {
                            "server_id",
                            "slug",
                            "display_name",
                            "status",
                            "auth_status",
                            "enabled",
                            "transport",
                        }
                    }
                    for e in server_entries
                ]
                evidence["installed_server_count"] = len(server_entries)
                evidence["available_tool_count"] = len(tool_entries)
                evidence["available_tools"] = [
                    str(e.get("name") or e.get("tool_name")) for e in tool_entries
                ][:50]
                evidence["linear_installed"] = any(
                    str(e.get("slug")) == "linear" for e in server_entries
                )

                # The surface a user would look at.
                session.click(TOOLS_RAIL)
                time.sleep(3)
                session.shot("e-01-tools-destination")
                evidence["tools_surface_text"] = (
                    session.evaluate(
                        "((document.querySelector('main') || document.body).innerText"
                        " || '').trim().slice(0, 2500)"
                    )
                    or ""
                )

                # Rider: WHEN the composer's folder bar goes away. FS-C measured
                # two points and found it still on screen moments after the send,
                # which is either a slow FTUE→cockpit handoff or a bar that
                # outlives the first message. Sampling settles which.
                session.click('[aria-label="Run"][data-destination]')
                time.sleep(2)
                session.send_first_run_message("Say READY and nothing else.")
                samples: list[dict[str, Any]] = []
                start = time.time()
                for _ in range(40):
                    samples.append(
                        {
                            "t": round(time.time() - start, 1),
                            "bar": bool(
                                session.evaluate(
                                    "!!document.querySelector('.aui-folder-bar')"
                                )
                            ),
                            "cockpit": bool(
                                session.evaluate(
                                    "!!document.querySelector('[data-testid=thread-canvas]')"
                                )
                            ),
                        }
                    )
                    if samples[-1]["cockpit"] and not samples[-1]["bar"]:
                        break
                    time.sleep(1)
                evidence["folder_bar_timeline"] = samples
                session.shot("e-02-after-first-message")
        finally:
            out = dump(session.run_dir, "fs-e-evidence.json", evidence)
            print(f"[fs-e] evidence -> {out}", flush=True)

    result(
        JOURNEY,
        "reported",
        linear_in_catalog=evidence.get("linear_catalog_entry") is not None,
        linear_configured=evidence.get("linear_installed"),
        installed_servers=evidence.get("installed_server_count"),
        mcp_tools_available=evidence.get("available_tool_count"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
