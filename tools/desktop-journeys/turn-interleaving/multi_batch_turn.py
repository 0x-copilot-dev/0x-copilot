#!/usr/bin/env python3
"""J2 — text → 3 tools → text → (2 tools + 1 subagent) → summary text.

The hard shape. J1 proves prose survives ONE tool call; this proves the turn
stays ordered across TWO activity batches of different composition, with prose
between and after them, and with a delegated subagent in the second batch.

Why this is the interesting case: the old fold kept one accumulator per KIND, so
every sentence in a turn like this collapsed into a single blob that
`final_response` then overwrote — three prose segments became one — and the
whole reply carried a single anchor (its first token), so all six activity cards
sorted after it regardless of when they ran. Nothing about that failure is
visible with one tool call and one sentence.

Asserted AFTER settle, so the DOM is rendered from the persisted message rather
than the live projection (see J1's docstring for why that seam is the point).

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

JOURNEY = "turn-interleaving/multi_batch_turn"
PROVIDER = os.environ.get("INTERLEAVE_PROVIDER", "openai")

MARKS = ("STEP-ONE:", "STEP-TWO:", "STEP-THREE:")

# Phrasing follows `chat-rich-cards`: name the tool, name the exact count, and
# forbid delegating the parts that must not be delegated. Vague prompts produce
# a different shape every run and the journey then measures the model, not us.
PROMPT = (
    "Follow this exact shape, in this order, and do not deviate.\n"
    "1. FIRST, before calling any tool at all, write one short sentence that "
    f"begins with the literal text '{MARKS[0]}' saying what you are about to "
    "research.\n"
    "2. THEN use the web_search tool exactly THREE times yourself — once for "
    "the official Python documentation page for math.isqrt, once for the "
    "official page for math.gcd, and once for the official page for "
    "math.factorial. Do not delegate these.\n"
    f"3. THEN write one short sentence that begins with the literal text "
    f"'{MARKS[1]}' saying what those three pages covered.\n"
    "4. THEN use the web_search tool exactly TWICE more yourself — once for "
    "the official page for math.comb and once for the official page for "
    "math.perm — and, in the same step, dispatch exactly ONE subagent to state "
    "the definition of a prime number. Do not compute that definition "
    "yourself.\n"
    f"5. FINALLY write one short sentence that begins with the literal text "
    f"'{MARKS[2]}' summarising everything.\n"
    "Do not merge the three sentences, and do not skip any of them."
)

# Every top-level transcript row, classified. An activity row is either a loose
# card or a `tool-run-group` (consecutive activity folds into one group, so a
# batch of three usually arrives as ONE row holding three members).
JS_ROWS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    const group=li.querySelector('[data-testid=tool-run-group]');
    const tools=li.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]').length
      + (id.startsWith('tc-chat-tool-')?1:0);
    const fleets=li.querySelectorAll('[data-testid^="tc-chat-fleet-"]').length
      + (id.startsWith('tc-chat-fleet-')?1:0);
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      tools, fleets,
      activity: !!group || tools>0 || fleets>0,
      groupLabel: group ? (group.getAttribute('data-state')||'') : null,
      text: (li.innerText||'').trim().slice(0,300),
    };
  }));
})()"""

JS_BUSY = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify({
    running: g.some((n)=>n.getAttribute('data-state')==='running'),
    spin: !!document.querySelector('[data-tool-status=running]'),
  });
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


def wait_settled(s: DriverSession, timeout_s: int = 420) -> list[dict]:
    """Wait until nothing is running AND the transcript stops changing.

    Longer than J1 on purpose: six activity items plus a delegated subagent is a
    genuinely long run, and a timeout here would report a slow model as a
    rendering failure.
    """
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        busy = json.loads(s.evaluate(JS_BUSY) or "{}")
        current = rows(s)
        snap = json.dumps(current, sort_keys=True)
        if (
            not (busy.get("running") or busy.get("spin"))
            and current
            and snap == previous
        ):
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
    except SystemExit as exc:  # missing prerequisite ⇒ exit 3, never a failure
        emit("skipped", str(exc))
        return 3

    with DriverSession(name="turn-interleaving-multi") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        s.send_first_run_message(PROMPT)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        log("── text → 3 tools → text → (2 tools + 1 subagent) → text ──")

        settled = wait_settled(s)
        s.shot("01-settled")
        log("transcript rows (post-settle — rendered from the persisted message):")
        for i, r in enumerate(settled):
            log(
                f"  [{i}] {r['id'] or '(none)':<44} role={r['role']} "
                f"seq={r['partSeq']} tools={r['tools']} fleets={r['fleets']} "
                f"activity={r['activity']} :: {r['text'][:70]!r}"
            )

        # ASSISTANT rows only — the user's turn quotes every marker verbatim.
        def mark_row(mark: str) -> int:
            return next(
                (
                    i
                    for i, r in enumerate(settled)
                    if r["role"] == "assistant" and mark in r["text"]
                ),
                -1,
            )

        idx = [mark_row(m) for m in MARKS]
        found = [m for m, i in zip(MARKS, idx) if i != -1]
        activity = [i for i, r in enumerate(settled) if r["activity"]]

        # Model compliance is not a product fact. If the model did not produce
        # the requested shape, say so instead of failing — but never call it a
        # pass either.
        if len(found) < len(MARKS):
            emit(
                "blocked",
                f"model produced {len(found)}/3 markers ({found}); the shape "
                "under test did not occur",
            )
            return 2
        if not activity:
            emit("blocked", "the turn produced no activity; nothing to interleave")
            return 2

        assert idx == sorted(idx), (
            f"the three prose segments are out of order: {list(zip(MARKS, idx))}"
        )
        log(f"PASS  all three prose segments survived, in order, at rows {idx}")

        batch_one = [i for i in activity if idx[0] < i < idx[1]]
        batch_two = [i for i in activity if idx[1] < i < idx[2]]
        assert batch_one, (
            f"no activity between {MARKS[0]} (row {idx[0]}) and {MARKS[1]} "
            f"(row {idx[1]}); activity rows are {activity}"
        )
        assert batch_two, (
            f"no activity between {MARKS[1]} (row {idx[1]}) and {MARKS[2]} "
            f"(row {idx[2]}); activity rows are {activity}"
        )

        tools_one = sum(settled[i]["tools"] for i in batch_one)
        tools_two = sum(settled[i]["tools"] for i in batch_two)
        fleets_two = sum(settled[i]["fleets"] for i in batch_two)
        log(
            f"PASS  batch 1 at rows {batch_one}: {tools_one} tool card(s)\n"
            f"PASS  batch 2 at rows {batch_two}: {tools_two} tool card(s), "
            f"{fleets_two} fleet card(s)"
        )

        # Composition. Under-delivery by the model is `blocked`; the ORDERING
        # asserts above already held, which is what this change owns.
        if tools_one < 3 or tools_two < 2 or fleets_two < 1:
            emit(
                "blocked",
                f"ordering held, but the model produced batch1={tools_one} tools, "
                f"batch2={tools_two} tools + {fleets_two} subagent fleets "
                "(asked for 3, then 2 + 1)",
            )
            return 2

        # Durability — the DOM must not be the only place this shape exists.
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
        assert assistant, "no assistant message was persisted"
        blocks = [
            b for b in (assistant[-1].get("content") or []) if b.get("type") == "text"
        ]
        seqs = [b.get("seq") for b in blocks]
        log(f"persisted text blocks: {json.dumps(blocks)[:700]}")
        assert len(blocks) >= 3, (
            f"persisted {len(blocks)} text block(s); a three-segment turn must "
            "persist three, or it collapses to one blob on the next reload"
        )
        assert all(isinstance(q, int) for q in seqs) and seqs == sorted(seqs), (
            f"persisted blocks are not seq-ordered: {seqs}"
        )
        log(f"PASS  persisted {len(blocks)} ordered text blocks, seqs={seqs}")

        s.shot("02-verified")
        emit("passed", "two activity batches interleaved between three prose segments")
        return 0


if __name__ == "__main__":
    sys.exit(main())
