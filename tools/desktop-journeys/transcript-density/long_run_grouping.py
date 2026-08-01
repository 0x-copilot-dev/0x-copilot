#!/usr/bin/env python3
"""PRD-03 — a long, many-step run must collapse to one line, live.

Drives the real supervised desktop app through a deliberately LONG task that
mixes a web search, a filesystem listing and a delegated subagent in a single
response, then asserts the transcript-density contract on what actually
rendered:

  * the run's activity is wrapped in ONE `tool-run-group`, not N loose cards;
  * once every member settles the group is COLLAPSED (D-3.2);
  * the final answer sits OUTSIDE the group, so the conclusion is not buried
    inside the process (the whole point of PRD-03);
  * the collapsed group is materially shorter than its own members;
  * the disclosure still works — nothing is hidden, only folded.

It also RECORDS, rather than asserts, the one thing PRD-03 left open: whether
the streaming assistant message (stamped with the first delta's timestamp by
`chatProjection`) anchors between activity items and splits a turn's work into
more than one group. That is an empirical question about real runs, which is
exactly what a live journey is for.

The provider key is read from services/ai-backend/.env and only ever reaches the
password field — never printed, logged, or written to an artifact.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("DENSITY_PROVIDER", "openai")

# Deliberately unambiguous and deliberately LONG: three distinct kinds of work
# in one response, so the run produces enough activity for grouping to matter.
P_LONG = (
    "Do all of the following in this one response, in order, and do not skip a "
    "step:\n"
    "1. Use the web_search tool exactly once to find the official Python "
    "documentation page for math.isqrt, then summarise that page in one "
    "sentence.\n"
    "2. Use your filesystem tool to list the current working directory and say "
    "how many entries it has.\n"
    "3. Dispatch exactly ONE subagent to state the definition of a prime "
    "number, and do not compute it yourself.\n"
    "Finish with a single final answer that contains the documentation URL, the "
    "entry count, and the subagent's definition."
)

JS_GROUPS = """(()=>{
  const groups=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify(groups.map((g)=>({
    state:g.getAttribute('data-state'),
    open:!!g.open,
    pinned:g.getAttribute('data-pinned'),
    label:(g.querySelector('[data-testid=tool-run-group-label]')||{}).textContent||'',
    members:g.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status], [data-testid^="tc-chat-fleet-"]').length,
    collapsedH:Math.round(g.getBoundingClientRect().height),
    bodyH:Math.round(((g.querySelector('.cs-run-group__body')||{}).getBoundingClientRect?.()||{height:0}).height),
  })));
})()"""

# Loose = an activity card that is NOT inside a group. A lone call is allowed to
# be loose (D-3.4); a run of them is the bug PRD-03 exists to fix.
JS_LOOSE = """(()=>{
  const all=[...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status], [data-testid^="tc-chat-fleet-"]')];
  return all.filter((n)=>!n.closest('[data-testid=tool-run-group]')).length;
})()"""

JS_ORDER = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    if(li.querySelector('[data-testid=tool-run-group]')) return 'group';
    const m=li.getAttribute('data-testid')||'';
    if(m.startsWith('tc-chat-message-')) return 'msg:'+(li.getAttribute('data-role')||'?');
    if(m.startsWith('tc-chat-tool-')) return 'loose-tool';
    if(m.startsWith('tc-chat-fleet-')) return 'loose-fleet';
    return 'other';
  }));
})()"""

JS_ASSISTANT_COUNT = 'document.querySelectorAll("[data-testid^=tc-chat-message-][data-role=assistant]").length'


def log(line: str) -> None:
    print(line, flush=True)


def groups(s: DriverSession) -> list[dict]:
    raw = s.evaluate(JS_GROUPS)
    return json.loads(raw) if raw else []


def wait_quiet(s: DriverSession, timeout_s: int = 180) -> list[dict]:
    """Wait until no group is running and the DOM stops changing."""
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        gs = groups(s)
        running = any(g["state"] == "running" for g in gs)
        snap = json.dumps(gs, sort_keys=True)
        if not running and gs and snap == previous:
            stable += 1
            if stable >= 8:
                return gs
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)
    raise AssertionError(f"run never settled within {timeout_s}s; last={previous}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0

    try:
        key = load_env_key(PROVIDER)
    # `load_env_key` raises SystemExit, which is a BaseException — an
    # `except Exception` would let it through and exit 1, i.e. report a missing
    # local key as a FAILURE. The harness contract says a missing prerequisite
    # is exit 3 (skipped), so catch it explicitly.
    except SystemExit as exc:
        print(
            json.dumps(
                {
                    "journey": "transcript-density/long_run_grouping",
                    "outcome": "skipped",
                    "reason": str(exc),
                }
            ),
            flush=True,
        )
        return 3

    with DriverSession(name="transcript-density") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        s.shot("00-byok-ready")
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
        s.send_first_run_message(P_LONG)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        log("── long run dispatched (web search + ls + 1 subagent) ──────")

        # 1 — the group must appear WHILE the run is live, and be open.
        appeared = False
        deadline = time.time() + 120
        while time.time() < deadline:
            gs = groups(s)
            if gs:
                appeared = True
                if any(g["state"] == "running" for g in gs):
                    running = [g for g in gs if g["state"] == "running"][0]
                    assert running["open"], (
                        "D-3.2: a running group must be EXPANDED so the user "
                        f"can watch the work; got {running!r}"
                    )
                    log(f"PASS  live group is expanded — {running['label']!r}")
                    s.shot("01-running-expanded")
                    break
            time.sleep(0.5)
        assert appeared, (
            "no tool-run-group ever rendered — the run produced no grouped "
            "activity, so this journey cannot test PRD-03"
        )

        # 2 — settle, then assert the collapse contract.
        gs = wait_quiet(s)
        s.shot("02-settled-collapsed")
        order = json.loads(s.evaluate(JS_ORDER) or "[]")
        loose = int(s.evaluate(JS_LOOSE) or 0)
        log(f"transcript order: {order}")
        log(f"groups: {json.dumps(gs)}")

        assert gs, "the settled transcript has no group"
        total_members = sum(g["members"] for g in gs)
        assert total_members >= 2, (
            f"the run produced only {total_members} activity item(s); a long "
            "task was requested, so grouping was never exercised"
        )

        for g in gs:
            assert g["state"] in {"settled", "failed"}, g
            if g["state"] == "settled":
                assert not g["open"], f"D-3.2: a settled group must collapse; got {g!r}"
                assert g["label"].startswith("Worked for"), g
            else:
                # D-3.5 — a failed run keeps its detail on screen.
                assert g["open"], f"D-3.5: a failed group must stay open; got {g!r}"

        # 3 — the ANSWER must be outside the group. This is the finding.
        assert int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before, (
            "no assistant answer arrived"
        )
        assert order and order[-1].startswith("msg:"), (
            f"the final transcript item must be the answer, not process; got {order!r}"
        )
        answer_in_group = s.evaluate(
            "!!document.querySelector('[data-testid=tool-run-group] "
            "[data-testid^=tc-chat-message-][data-role=assistant]')"
        )
        assert not answer_in_group, "the answer must never be inside the group"
        log("PASS  the answer sits outside the collapsed group")

        # 4 — density actually improved: the collapsed group is far shorter
        #     than the stack of cards it replaced.
        settled = [g for g in gs if g["state"] == "settled"]
        for g in settled:
            assert g["collapsedH"] <= 64, (
                f"a collapsed group should be about one card tall; got {g['collapsedH']}px"
            )
        log(
            "PASS  collapsed group height "
            f"{[g['collapsedH'] for g in settled]}px for "
            f"{[g['members'] for g in settled]} members"
        )

        # 5 — nothing is hidden: the disclosure still opens.
        summary = "[data-testid=tool-run-group-summary]"
        assert s.present(summary), "the group has no disclosure control"
        s.click(summary)
        opened = groups(s)
        assert any(g["open"] for g in opened), "clicking the summary did not expand"
        s.shot("03-expanded-by-user")
        log("PASS  the group discloses on click — folded, not hidden")

        # 6 — RECORD (not assert) the open question from PRD-03 D-3.1: does the
        #     streaming answer anchor mid-run and split one turn's work?
        group_count = len(gs)
        verdict = (
            "single group — the assistant message did NOT split the run"
            if group_count == 1
            else f"{group_count} groups — the assistant message SPLIT the run"
        )
        log(f"FINDING  {verdict}")
        log(f"FINDING  loose (ungrouped) activity cards: {loose}")

        # 7 — narrow window: the contract must hold at 640px too.
        s.resize(640, 900)
        time.sleep(1.0)
        narrow = groups(s)
        s.shot("04-compact-640")
        assert narrow, "the group vanished at 640px"
        scroll = s.document_scroll()
        # `document_scroll` returns raw metrics; the frame invariant is that the
        # DOCUMENT itself never overflows (every scroller lives inside it).
        overflow_x = scroll["scrollWidth"] - scroll["clientWidth"]
        overflow_y = scroll["scrollHeight"] - scroll["clientHeight"]
        assert overflow_x <= 1 and overflow_y <= 1, (
            f"the document must never scroll; overflow x={overflow_x} "
            f"y={overflow_y}; metrics={scroll!r}"
        )
        log("PASS  group survives 640px and the document does not scroll")

    print(
        json.dumps(
            {
                "journey": "transcript-density/long_run_grouping",
                "outcome": "passed",
                "groups": group_count,
                "members": total_members,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
