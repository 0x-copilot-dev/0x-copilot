#!/usr/bin/env python3
"""J1 — prose the model spoke BEFORE it acted must survive the run finishing.

Drives the real supervised desktop app through a turn shaped `text -> tool ->
text`, then asserts, AFTER the run settles, that both prose halves are on screen
and that the activity sits BETWEEN them.

Asserting after settle is the point. While a run streams, the transcript comes
from the live projection; the moment it goes terminal `useRunTranscript`
re-seeds from `/messages` and history wins. The reported bug was exactly that
seam — the turn looked right mid-stream and collapsed to its last sentence when
the run finished, because the persisted message carried only `content_text`.
One post-settle assertion therefore covers the client fold, the worker's fold at
seal time, and the re-seed.

It also reads the persisted message back through the app's authenticated
transport, so a green DOM cannot hide an empty `content` — that would mean the
transcript survives only until the next reload.

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

JOURNEY = "turn-interleaving/interleaved_turn"
PROVIDER = os.environ.get("INTERLEAVE_PROVIDER", "openai")

MARK_ONE = "STEP-ONE:"
MARK_TWO = "STEP-TWO:"

# Forces speak -> act -> speak. The markers are what the assertions key on, so
# they are demanded literally and up front.
PROMPT = (
    "Follow this exact shape and do not deviate.\n"
    f"1. FIRST, before calling any tool at all, write one short sentence that "
    f"begins with the literal text '{MARK_ONE}' saying what you are about to "
    "look up.\n"
    "2. THEN use the web_search tool exactly once to find the official Python "
    "documentation page for math.isqrt.\n"
    f"3. THEN write one short sentence that begins with the literal text "
    f"'{MARK_TWO}' giving the documentation URL.\n"
    "Do not merge steps 1 and 3 into a single sentence, and do not skip step 1."
)

# Classify every top-level transcript row as prose or activity, in DOM order.
# Part rows (`tc-chat-message-<id>-part-<n>`) and whole-message rows share the
# `tc-chat-message-` prefix, so both are read the same way.
JS_ROWS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    const activity=li.querySelector('[data-testid=tool-run-group]')
      || (id.startsWith('tc-chat-tool-')?li:null)
      || (id.startsWith('tc-chat-fleet-')?li:null)
      || li.querySelector('[data-testid^="tc-chat-tool-"],[data-testid^="tc-chat-fleet-"]');
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partType: li.getAttribute('data-part-type')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      activity: !!activity,
      text: (li.textContent||'').trim().slice(0,400),
    };
  }));
})()"""

JS_RUN_ACTIVE = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  const running=g.some((n)=>n.getAttribute('data-state')==='running');
  const spin=!!document.querySelector('[data-tool-status=running]');
  return JSON.stringify({running, spin, groups:g.length});
})()"""


def log(line: str) -> None:
    print(line, flush=True)


def emit(outcome: str, reason: str) -> None:
    print(
        json.dumps({"journey": JOURNEY, "outcome": outcome, "reason": reason}),
        flush=True,
    )


def rows(s: DriverSession) -> list[dict]:
    raw = s.evaluate(JS_ROWS)
    return json.loads(raw) if raw else []


def wait_settled(s: DriverSession, timeout_s: int = 240) -> list[dict]:
    """Wait until nothing is running AND the transcript stops changing.

    The stability window matters more than usual here: the terminal re-seed
    swaps the live overlay for persisted history, so a snapshot taken the
    instant the spinner stops can still be the pre-seed DOM.
    """
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        state = json.loads(s.evaluate(JS_RUN_ACTIVE) or "{}")
        current = rows(s)
        snap = json.dumps(current, sort_keys=True)
        busy = state.get("running") or state.get("spin")
        if not busy and current and snap == previous:
            stable += 1
            if stable >= 10:
                return current
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)
    raise AssertionError(f"run never settled within {timeout_s}s; last rows={previous}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0

    try:
        key = load_env_key(PROVIDER)
    # `load_env_key` raises SystemExit (a BaseException): an `except Exception`
    # would let it through as exit 1, reporting a missing local key as a
    # FAILURE. The harness contract makes a missing prerequisite exit 3.
    except SystemExit as exc:
        emit("skipped", str(exc))
        return 3

    with DriverSession(name="turn-interleaving") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        s.send_first_run_message(PROMPT)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        log("── speak → act → speak dispatched ─────────────────────────")

        settled = wait_settled(s)
        s.shot("01-settled")
        log("transcript rows (post-settle, i.e. rendered from persisted history):")
        for row in settled:
            log(
                f"  {row['id'] or '(no testid)':<46} "
                f"role={row['role']} part={row['partType']}/{row['partSeq']} "
                f"activity={row['activity']} :: {row['text'][:90]!r}"
            )

        prose = [r for r in settled if not r["activity"] and r["text"]]
        first_idx = next(
            (i for i, r in enumerate(settled) if MARK_ONE in r["text"]), -1
        )
        second_idx = next(
            (i for i, r in enumerate(settled) if MARK_TWO in r["text"]), -1
        )
        activity_idx = [i for i, r in enumerate(settled) if r["activity"]]

        # Model compliance, not a product fact: if the model never spoke before
        # acting, the shape under test did not occur. Report that as blocked so
        # it can never be mistaken for "the shape occurred and was fine".
        if first_idx == -1 and second_idx == -1:
            emit("blocked", "the model emitted neither marker; prompt not honoured")
            return 2
        if not activity_idx:
            emit("blocked", "the turn produced no tool activity; nothing to interleave")
            return 2
        if first_idx == -1:
            # This is the failure mode the fix targets, but it is indistinguishable
            # from a model that simply called the tool first. Say so plainly.
            raise AssertionError(
                f"{MARK_ONE!r} is absent from the settled transcript. Either the "
                "model skipped step 1, or the pre-tool prose was destroyed by "
                "final_response — check 01-settled.png and the rows above."
            )

        assert second_idx != -1, (
            f"{MARK_TWO!r} is absent; the turn never produced its closing prose"
        )
        log(
            f"PASS  both prose halves survived the run finishing ({len(prose)} prose rows)"
        )

        # The ordering claim: activity sits BETWEEN the two prose halves. Before
        # the fix both halves were one <li> with one anchor, so every card
        # sorted after the whole message and no index could satisfy this.
        between = [i for i in activity_idx if first_idx < i < second_idx]
        assert between, (
            f"no activity rendered between the prose halves: {MARK_ONE} at row "
            f"{first_idx}, {MARK_TWO} at row {second_idx}, activity at "
            f"{activity_idx}. The turn's cards did not interleave."
        )
        log(f"PASS  activity interleaved between the halves at row(s) {between}")

        # Durability: a green DOM must not hide an empty persisted `content`.
        conversation_id = s.evaluate(
            "(location.hash.match(/conversation[=/]([^&/?]+)/)||[])[1]||null"
        ) or s.evaluate(
            "(document.querySelector('[data-conversation-id]')||{})"
            ".getAttribute?.('data-conversation-id')||null"
        )
        assert conversation_id, "could not resolve the conversation id from the app"
        payload = s.transport(
            "GET", f"/v1/agent/conversations/{conversation_id}/messages"
        )
        assistant = [
            m for m in (payload.get("messages") or []) if m.get("role") == "assistant"
        ]
        assert assistant, "no assistant message was persisted for this conversation"
        blocks = assistant[-1].get("content") or []
        text_blocks = [b for b in blocks if b.get("type") == "text"]
        log(f"persisted content blocks: {json.dumps(blocks)[:600]}")
        assert len(text_blocks) >= 2, (
            "the persisted assistant message carries "
            f"{len(text_blocks)} text block(s). The worker did not fold the turn, "
            "so this transcript survives only until the next reload — "
            f"content_text={assistant[-1].get('content_text', '')[:120]!r}"
        )
        assert all(isinstance(b.get("seq"), int) for b in text_blocks), (
            f"a persisted text block has no integer seq: {text_blocks!r}"
        )
        seqs = [b["seq"] for b in text_blocks]
        assert seqs == sorted(seqs), f"persisted blocks are not seq-ordered: {seqs}"
        log(f"PASS  persisted {len(text_blocks)} ordered text blocks, seqs={seqs}")

        s.shot("02-verified")
        emit("passed", "interleaved turn survived settle, ordering and persistence")
        return 0


if __name__ == "__main__":
    sys.exit(main())
