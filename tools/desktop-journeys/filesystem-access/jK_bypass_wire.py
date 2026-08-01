#!/usr/bin/env python3
"""FS-K — does a bypass SELECTION survive the wire? Backend half, alone.

One question, and it splits a bug that three UI runs could not:

    when a run-create carries `filesystem_bypass`, does the sealed decision
    on the run come back as BYPASS?

    YES => the backend is correct end to end. Whatever is wrong lives in the
           renderer: the pill's selection is not reaching the request body.
    NO  => the field is dropped or ignored server-side, and the sealed
           decision has been lying about what it received.

WHY THIS EXISTS RATHER THAN MORE UI. FS-H drives the real composer, and every
one of its bypass failures so far has been a defect in the DRIVING rather than
in the product: a pill clicked while invisible, a menu toggled shut, a Send
button waited on before typing. Each cost a ~12-minute run to disprove. The
sealed decision says `source: "master"`, which means "no selection arrived" —
equally consistent with the client never sending one and the server discarding
it, and the UI cannot tell those apart.

So this journey removes the UI from the question. It signs in and POSTs runs
straight through `transport_json`, i.e. the app's OWN authenticated transport
(`window.bridge.ipc.invoke("transport.request", …)`) — the same channel, session
and facade the composer uses, minus every widget between the user and the body.

AND NOT BY PATCHING THE BRIDGE. The obvious alternative — intercept
`window.bridge.ipc.invoke` and read the body the composer posts — CANNOT WORK:
the bridge is published with `contextBridge.exposeInMainWorld`, whose objects
are immutable in the main world, so the assignment silently no-ops. An
interceptor written that way reports `installed: true`, captures nothing, and
reads exactly like "the client sent nothing". Measured. See jH's header note.

Three probes, because absence has to be distinguishable from refusal:

  1. no selection            -> expect manual / source=master   (the baseline)
  2. message-scope bypass    -> expect bypass / source=message
  3. run-scope bypass        -> expect bypass / source=run

Probe 1 is what makes 2 and 3 meaningful: if it already reported bypass, the
field would be proving nothing.
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

JOURNEY = "FS-K"

ATTACH = ".aui-folder-bar__attach"

#: Cheap prompts — this journey is about the run's SEALED CONTEXT, not its work.
GOAL = "Say READY and nothing else."


def _create_run(
    session: DriverSession,
    conversation_id: str,
    selection: dict[str, str] | None,
) -> dict[str, Any]:
    """POST one run, with or without a bypass selection, and read it back."""

    body: dict[str, Any] = {"conversation_id": conversation_id, "goal": GOAL}
    if selection is not None:
        body["filesystem_bypass"] = selection
    created = transport_json(session, "POST", "/v1/agent/runs", body=body)
    run_id = (created or {}).get("run_id")
    if not isinstance(run_id, str) or run_id == "":
        return {"sent": selection, "error": "no run_id", "created": created}

    # The sealed decision is on the run record. Read it back through the same
    # transport rather than off disk, so this journey asserts what the PRODUCT
    # reports about itself.
    sealed: Any = None
    for _ in range(20):
        run = transport_json(session, "GET", f"/v1/agent/runs/{run_id}")
        context = (run or {}).get("runtime_context") or {}
        sealed = context.get("filesystem_bypass")
        if sealed is not None:
            break
        time.sleep(1.0)
    return {"sent": selection, "run_id": run_id, "sealed": sealed}


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    evidence: dict[str, Any] = {}

    with lane(DEFAULT_LANE):
        session = DriverSession(name="fs-k-bypass-wire")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                assert session.wait_for(
                    "[data-testid=sign-in-gate], " + ATTACH, timeout_s=120
                ), "the app never reached a known screen"
                if session.present("[data-testid=sign-in-button]"):
                    session.sign_in_local()
                session.wait_for(
                    "[data-testid=first-run-add-key], " + ATTACH, timeout_s=120
                )
                if not session.present(ATTACH):
                    session.ftue_add_key(provider, key)
                assert session.wait_for(ATTACH, timeout_s=60), "no composer"

                # What the server believes tier 1 is, read the same way the
                # renderer reads it. Recorded beside the sealed decisions so a
                # disagreement between them is visible in ONE artifact.
                evidence["workspace_defaults"] = (
                    transport_json(session, "GET", "/v1/agent/workspace/defaults") or {}
                ).get("behavior_overrides")

                conversation = transport_json(
                    session, "POST", "/v1/agent/conversations", body={}
                )
                conversation_id = (conversation or {}).get("conversation_id")
                evidence["conversation_id"] = conversation_id
                assert isinstance(conversation_id, str) and conversation_id, (
                    f"no conversation_id: {conversation!r}"
                )

                evidence["baseline"] = _create_run(session, conversation_id, None)
                evidence["message_scope"] = _create_run(
                    session, conversation_id, {"message": "bypass"}
                )
                evidence["run_scope"] = _create_run(
                    session, conversation_id, {"run": "bypass"}
                )
        finally:
            out = dump(session.run_dir, "fs-k-evidence.json", evidence)

    def _mode(key: str) -> str:
        return str(((evidence.get(key) or {}).get("sealed") or {}).get("mode") or "")

    def _source(key: str) -> str:
        return str(((evidence.get(key) or {}).get("sealed") or {}).get("source") or "")

    failures: list[str] = []
    if _mode("baseline") != "manual":
        failures.append(
            f"BASELINE: a run with no selection sealed {_mode('baseline')!r}, "
            "so the other two probes prove nothing"
        )
    if _mode("message_scope") != "bypass":
        failures.append(
            "MESSAGE SCOPE: a run-create carrying "
            "`filesystem_bypass={'message':'bypass'}` sealed "
            f"{_mode('message_scope')!r}/{_source('message_scope')!r} — the "
            "server dropped or refused the selection"
        )
    if _mode("run_scope") != "bypass":
        failures.append(
            "RUN SCOPE: a run-create carrying "
            "`filesystem_bypass={'run':'bypass'}` sealed "
            f"{_mode('run_scope')!r}/{_source('run_scope')!r}"
        )

    result(
        JOURNEY,
        "FAILED" if failures else "passed",
        failures=failures,
        baseline=_mode("baseline"),
        message_scope=f"{_mode('message_scope')}/{_source('message_scope')}",
        run_scope=f"{_mode('run_scope')}/{_source('run_scope')}",
        # The verdict this journey exists to deliver, stated rather than implied.
        verdict=(
            "backend OK — the renderer is not sending the selection"
            if not failures
            else "backend drops or refuses the selection"
        ),
        evidence=str(out),
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
