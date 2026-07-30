#!/usr/bin/env python3
"""DIAGNOSTIC (not an assertion journey): why is web search missing from Sources?

Runs one web-search prompt against the real packaged app, then reports, for that
run, every link in the chain that could break:

  backend   →  does the run stream carry `source_ingested` / `sources_ingested`?
  persist   →  does GET /v1/agent/conversations/{id}/sources return rows?
  client    →  what does the Sources tab actually render?

Prints findings; does not assert. Delete once the bug is understood and covered
by a real journey.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("JOURNEY_PROVIDER", "anthropic")
P_CITE = "Search the web for what LangGraph is and summarise it in two sentences with sources."

JS_HASH = "window.location.hash"
JS_SOURCES_TAB = "(document.querySelector('[data-testid=run-rail-panel-sources]')||{}).innerText||null"
JS_RAIL_TAB = (
    "(document.querySelector('[data-testid=run-workspace-rail]')||{})"
    ".getAttribute && document.querySelector("
    "'[data-testid=run-workspace-rail]').getAttribute('data-active-tab')"
)


def log(line: str) -> None:
    print(line, flush=True)


def await_model_pill(s: DriverSession, timeout_s: int = 60) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        last = (s.model_pill() or "").strip()
        if last and last.lower() != "model":
            return last
        time.sleep(0.5)
    raise AssertionError("model pill never resolved")


def main() -> int:
    with DriverSession(name="sources-probe") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, load_env_key(PROVIDER))
        log(f"  model = {await_model_pill(s)!r}")

        s.send_first_run_message(P_CITE)
        assert s.wait_for("[data-testid=thread-canvas]", 120), "no cockpit"

        # Wait for chips (proxy for "the answer streamed and cited").
        for _ in range(60):
            time.sleep(1)
            n = s.evaluate(
                "document.querySelectorAll('[data-testid=tc-chat] .citation-chip').length"
            )
            if n and int(n) > 0:
                break
        time.sleep(4)

        conv_hash = s.evaluate(JS_HASH) or ""
        log(f"\n  route hash = {conv_hash!r}")
        conv_id = conv_hash.rsplit("/", 1)[-1] if "/" in conv_hash else ""

        # ── backend: what event types did this run emit? ──────────────────────
        runs = s.transport("GET", f"/v1/agent/conversations/{conv_id}")
        run_id = runs.get("latest_run_id") or runs.get("latest_run_id_any_status") or ""
        log(f"  conversation = {conv_id!r}  run = {run_id!r}")

        events = s.transport("GET", f"/v1/agent/runs/{run_id}/events")
        rows = events.get("events", events if isinstance(events, list) else [])
        kinds = Counter(e.get("event_type") for e in rows)
        log(f"\n  === run emitted {len(rows)} events ===")
        for kind, count in kinds.most_common():
            marker = "  <<<" if "source" in str(kind) or "citation" in str(kind) else ""
            log(f"    {count:>4}  {kind}{marker}")

        for e in rows:
            if e.get("event_type") in {"source_ingested", "sources_ingested"}:
                log(f"\n  payload of {e['event_type']}:")
                log(f"    {json.dumps(e.get('payload'), indent=2)[:900]}")

        # ── persistence: does the sources endpoint have rows? ─────────────────
        try:
            persisted = s.transport("GET", f"/v1/agent/conversations/{conv_id}/sources")
            log(f"\n  === GET /sources ===\n    {json.dumps(persisted)[:900]}")
        except Exception as exc:  # noqa: BLE001 - diagnostic
            log(f"\n  === GET /sources RAISED === {exc}")

        # ── client: what does the tab render? ────────────────────────────────
        s.click("[data-testid=tc-chat] .citation-chip")
        time.sleep(1.5)
        log(f"\n  rail tab after chip click = {s.evaluate(JS_RAIL_TAB)!r}")
        log(f"  Sources panel text = {s.evaluate(JS_SOURCES_TAB)!r}")
        s.shot("sources-panel")
        log(f"\nscreenshots → {s.run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
