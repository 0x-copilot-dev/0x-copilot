#!/usr/bin/env python3
"""Live proof of the Studio spec-generation progress signal.

A Studio surface costs a SECOND model call that used to be awaited in silence:
the runtime emitted nothing until it finished, so the reader watched dead air
with no way to tell a slow generation from a dead one. `surface_spec_requested`
is the signal that closes that gap.

WHAT THIS HAS TO PROVE, and why it needs the live stack:

1. The runtime really emits it, from a re-staged snapshot of THIS branch. The
   staged runtime is a COPY of `services/*`; a stale stage runs old backend code
   and reports its verdict with total confidence.
2. The payload matches what the client projector reads (`surface_id`,
   `model_id`). The two halves were written in parallel against a fixed contract
   and never saw each other's code, so drift here typechecks fine and silently
   does nothing.
3. It PRECEDES its terminal `surface_spec_generated`. A progress signal that
   lands after the thing it announces is not progress.

Generation needs no env var on desktop: `ShapingModelResolver` falls back to the
cheapest model of the run's BYOK provider when `SURFACES_V2` is on, and the
desktop supervisor defaults that to true (`service-env.ts`). `SURFACE_SPEC_MODEL`
is deliberately NOT in the supervisor's passthrough allowlist, so the env-var
lane cannot be forced from the launching shell anyway.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from _lib import DriverSession, byok_provider, preflight_staged_runtime
from _workspace_lib import (
    dump,
    events,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "studio-progress-signal"

REQUESTED = "surface_spec_requested"
GENERATED = "surface_spec_generated"

# WHAT ACTUALLY TRIGGERS THE SIGNAL, learned the hard way. Generation is
# scheduled from `SurfaceProjector.project`, and the ONLY caller is
# `capabilities/operations/presentation.py` — the connected-capability read
# lane. It fires when the ladder returns `wants_refinement`, which rung 0 (the
# deterministic inferrer) deliberately sets so an inferred spec invites
# improvement.
#
# `publish_artifact` therefore CANNOT trigger it: it is a builtin product tool,
# it mints its surface through the Work Ledger emitter directly, and it never
# reaches the projector. The first version of this journey used it, saw
# `surface.created` in the stream, and reported a clean FAIL for a code path it
# had never executed.
#
# So this journey needs a REAL connected MCP server, which per the harness
# README must be connected out of band — the driver suppresses the browser
# handoff an OAuth connect needs. Without one it reports BLOCKED, not FAILED:
# an untested claim and a broken one are different results.
PROMPT = (
    "Use publish_artifact to publish a dataset artifact titled 'Animals' with "
    "media_type text/csv and this exact content:\n"
    "id,animal,score\n1,otter,42\n2,falcon,87\n3,badger,15\n"
    "Then reply with just: published"
)


def poll_for_skeleton(session: DriverSession, stop: threading.Event) -> list[str]:
    """Best-effort capture of the skeleton WHILE the run streams.

    Racy by nature — generation is a background task that can finish between
    polls — so this is reported as observed / not-observed and never gates the
    verdict. The deterministic proof is the event assertion.
    """

    seen: list[str] = []
    while not stop.is_set():
        try:
            text = session.evaluate(
                "(() => { const el = document.querySelector("
                "'[data-testid=tc-surface-skeleton]');"
                " return el ? (el.textContent || '').trim().slice(0, 120) : null; })()"
            )
        except Exception:  # noqa: BLE001 - a poll that races a reload is not a failure
            text = None
        if isinstance(text, str) and text and text not in seen:
            seen.append(text)
        time.sleep(0.25)
    return seen


def main() -> int:
    provider, key = byok_provider()
    if not key:
        print(f"no {provider} key in services/ai-backend/.env — cannot run")
        return 2

    preflight_staged_runtime()

    evidence: dict[str, Any] = {"provider": provider}
    skeletons: list[str] = []

    with DriverSession(JOURNEY) as s:
        s.sign_in_local()
        s.ftue_add_key(provider, key)

        stop = threading.Event()
        captured: list[list[str]] = []
        watcher = threading.Thread(
            target=lambda: captured.append(poll_for_skeleton(s, stop)), daemon=True
        )
        watcher.start()

        s.send(PROMPT)
        conversation_id = wait_for_conversation_id(s)
        run_id = wait_for_new_run(s, conversation_id, 0)
        final = settle_run(s, run_id)

        # Generation is fire-and-forget (`asyncio.create_task`), so its events
        # can land AFTER the run settles. Give the task a moment before reading.
        time.sleep(6)
        stop.set()
        watcher.join(timeout=5)
        skeletons = captured[0] if captured else []

        evidence["run_status"] = final.get("status")
        stream = events(s, run_id)
        evidence["tools"] = tool_calls(stream)
        s.shot("01-studio-after-run")

        named = [
            {
                "seq": e.get("sequence_no"),
                "type": e.get("event_type"),
                "payload": e.get("payload") or {},
            }
            for e in stream
            if e.get("event_type") in (REQUESTED, GENERATED)
        ]
        evidence["spec_events"] = named
        evidence["skeletons_observed"] = skeletons
        evidence["surface_created"] = sum(
            1 for e in stream if e.get("event_type") == "surface.created"
        )
        # `read.executed` is the operation lane's own marker — the one that
        # implies the projector ran. `surface.created` does NOT imply it:
        # publish_artifact mints one without touching the projector.
        evidence["operation_reads"] = sum(
            1 for e in stream if e.get("event_type") == "read.executed"
        )
        dump(s.run_dir, "evidence.json", evidence)

    requested = [e for e in named if e["type"] == REQUESTED]
    generated = [e for e in named if e["type"] == GENERATED]

    # The contract the client projector reads. Both keys are always present and
    # null-rather-than-absent when the runtime named neither.
    payload_ok = all(
        set(e["payload"]).issuperset({"surface_id", "model_id"}) for e in requested
    )
    precedes = bool(requested) and (
        not generated
        or min(e["seq"] for e in requested) < max(e["seq"] for e in generated)
    )

    # Did anything reach the projector at all? Only an operation-lane read does,
    # so with no connected capability there is nothing for the signal to be
    # emitted BY, and a FAIL here would be a lie about coverage.
    operation_reads = evidence["operation_reads"]
    if operation_reads == 0:
        print("\n" + "=" * 64)
        print(f"{JOURNEY} — BLOCKED (not a failure)")
        print("=" * 64)
        print("  No connected-capability read occurred, so `SurfaceProjector`")
        print("  never ran and no generation could be scheduled. The signal is")
        print("  untested here, NOT broken.")
        print(f"\n  surface.created events : {evidence['surface_created']}")
        print(f"  tools called           : {evidence.get('tools')}")
        print("\n  To unblock: connect an MCP server out of band (the driver")
        print("  suppresses the OAuth browser handoff), then re-run with")
        print("  COPILOT_DESKTOP_USER_DATA_SUBDIR set to that reused profile.")
        return 3

    checks = {
        "the runtime emitted surface_spec_requested": bool(requested),
        "its payload carries surface_id + model_id": bool(requested) and payload_ok,
        "it precedes its terminal surface_spec_generated": precedes,
    }

    print("\n" + "=" * 64)
    print(f"{JOURNEY} — provider={provider} run={evidence.get('run_status')}")
    print("=" * 64)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  surface.created events : {evidence['surface_created']}")
    print(f"  tools called           : {evidence.get('tools')}")
    if named:
        print("\n  spec-generation events, in wire order:")
        for e in sorted(named, key=lambda x: x["seq"] or 0):
            keys = ",".join(sorted(e["payload"]))
            model = e["payload"].get("model_id")
            print(f"    seq {e['seq']:>4}  {e['type']:<24} keys=[{keys}] model={model}")
    else:
        print("\n  NO spec-generation events in the stream.")
    # Reported, never gating: catching the skeleton is a race against a
    # background task, and losing that race says nothing about the signal.
    print(
        f"\n  skeleton observed mid-run: {skeletons or 'not caught (racy, not a failure)'}"
    )
    print(f"  evidence: tools/desktop-journeys/runs/{JOURNEY}/")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
