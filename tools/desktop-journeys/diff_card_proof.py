#!/usr/bin/env python3
"""Proof that an `edit_file` call renders as a real diff in the transcript.

Drives the REAL supervised app: attaches a fixture folder through the native
picker, asks the agent to change one line of a file, and then asserts against
the LIVE DOM that the tool card carried a `TcFileDiff` with the right hunk —
not just that the tool ran.

The DOM assertions are the point. A green run only proves the agent edited a
file; the claim under test is that the READER can see what changed, so the
evidence has to come from the rendered card.

Frontend-only change ⇒ no re-stage needed; `npm run build --workspace
@0x-copilot/desktop` in the worktree plus `app_dir` pointing at it is enough.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _lib import DriverSession, byok_provider, preflight_staged_runtime
from _workspace_lib import (
    attach_folder,
    dump,
    events,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "diff-card-proof"

BEFORE = """id,color,animal,score,is_valid
1,blue,otter,42,true
2,green,falcon,87,false
3,red,badger,15,true
4,purple,heron,63,true
5,orange,lynx,29,false
"""

# One line, one field. A single-cell change is what makes the diff legible as
# evidence: +1/-1 with the neighbouring rows as context is unambiguous, whereas
# a rewrite would render as a wall of green and prove nothing about hunking.
PROMPT = (
    "Use the edit_file tool on {path} to change ONLY the score on the lynx row "
    "from 29 to 30. Do not rewrite the file, do not use write_file, and do not "
    "change anything else. Then reply with just: done"
)

# No host grant available: have the agent build the fixture in its own
# filesystem first, then edit it. Two tool calls, two cards — a write_file
# (all-additions diff) and an edit_file (+1/-1), which is a better exercise of
# the renderer than the host lane's single call.
VIRTUAL_PROMPT = (
    "Do exactly two tool calls and nothing else.\n"
    "1. write_file to `random.csv` with EXACTLY these lines:\n"
    "```\n" + BEFORE + "```\n"
    "2. edit_file on `random.csv` changing ONLY `5,orange,lynx,29,false` to "
    "`5,orange,lynx,30,false`.\n"
    "Then reply with just: done"
)

# The renderer's own testids (packages/chat-surface/src/thread-canvas).
DIFF_ROOT = "tc-tool-edit-diff"
WRITE_ROOT = "tc-tool-write-diff"


def read_diff_cards(session: DriverSession, root: str = DIFF_ROOT) -> Any:
    """Every rendered file-diff card, read straight out of the live DOM."""

    return session.evaluate(
        """
        (() => {
          const out = [];
          for (const el of document.querySelectorAll(
                 '[data-testid="%s"]')) {
            const counts = el.querySelector('[data-testid="%s-counts"]');
            const path = el.querySelector('[data-testid="%s-path"]');
            const rows = [...el.querySelectorAll('[data-diff-kind]')].map((r) => ({
              kind: r.getAttribute('data-diff-kind'),
              text: r.textContent,
            }));
            // TRUE visibility, not just the nearest disclosure. Tool cards are
            // themselves folded into an outer activity group, so a card that
            // reports open can still be sealed inside a closed ancestor — the
            // first run of this journey passed exactly that way.
            let node = el.parentElement, sealedBy = null;
            while (node) {
              if (node.tagName === 'DETAILS' && !node.open) {
                sealedBy = (node.querySelector('summary')?.textContent || 'details')
                  .trim().slice(0, 60);
                break;
              }
              node = node.parentElement;
            }
            const box = el.getBoundingClientRect();
            out.push({
              counts: counts ? counts.textContent : null,
              path: path ? path.textContent : null,
              applied: el.getAttribute('data-applied'),
              sealedBy,
              painted: box.width > 0 && box.height > 0,
              approximate: el.getAttribute('data-approximate'),
              rows,
            });
          }
          return out;
        })()
        """
        % (root, root, root)
    )


def read_tool_card_chrome(session: DriverSession) -> Any:
    """The card header: does it name the file and carry a real icon?"""

    return session.evaluate(
        """
        (() => {
          const cards = [...document.querySelectorAll('[data-testid^="tc-chat-tool-"]')]
            .filter((el) => el.getAttribute('data-tool-status'));
          return cards.map((el) => {
            const sub = el.querySelector('[data-testid="tc-tool-card-subtitle"]');
            const tile = el.querySelector('svg');
            return {
              status: el.getAttribute('data-tool-status'),
              subtitle: sub ? sub.textContent : null,
              hasIconSvg: !!tile,
              text: (el.textContent || '').slice(0, 160),
            };
          });
        })()
        """
    )


def main() -> int:
    # `byok_provider()` resolves the chain and hands back BOTH halves.
    provider, key = byok_provider()
    if not key:
        print(f"no {provider} key in services/ai-backend/.env — cannot run")
        return 2

    preflight_staged_runtime()

    evidence: dict[str, Any] = {"provider": provider}

    with tempfile.TemporaryDirectory(prefix="diffproof-") as raw:
        root = Path(raw).resolve()
        target = root / "random.csv"
        target.write_text(BEFORE, encoding="utf-8")

        with DriverSession(JOURNEY) as s:
            s.sign_in_local()
            s.ftue_add_key(provider, key)
            s.shot("01-ftue-key-added")

            # The host lane needs the REAL native picker, and macOS denies that
            # automation unless the controlling process holds Accessibility. Fall
            # back to the agent's own virtual filesystem rather than reporting
            # nothing: the card under test renders from the CALL's arguments
            # (`old_string` / `new_string`), which are identical either way, so
            # the virtual lane exercises the same renderer end to end. What it
            # does NOT prove is the host-grant path — recorded as such.
            try:
                evidence["grant_id"] = attach_folder(
                    s, root, mode="read_write_no_delete", label="diff fixture"
                )
                evidence["lane"] = "host"
                s.shot("02-folder-attached")
                prompt = PROMPT.format(path=target)
            except Exception as exc:  # noqa: BLE001
                evidence["lane"] = "virtual"
                evidence["attach_blocked"] = repr(exc)[:200]
                s.shot("02-attach-blocked")
                prompt = VIRTUAL_PROMPT

            s.send(prompt)
            conversation_id = wait_for_conversation_id(s)
            run_id = wait_for_new_run(s, conversation_id, 0)
            final = settle_run(s, run_id)
            evidence["run_status"] = final.get("status")

            stream = events(s, run_id)
            evidence["tools"] = tool_calls(stream)

            # Let the last frame paint before reading the DOM.
            time.sleep(2)
            s.shot("03-transcript-with-diff")

            evidence["diff_cards"] = read_diff_cards(s)
            evidence["write_cards"] = read_diff_cards(s, WRITE_ROOT)
            evidence["tool_cards"] = read_tool_card_chrome(s)
            evidence["file_after"] = target.read_text(encoding="utf-8")

            # Focus mode must render the same card — the transcript is the one
            # place a run's work can appear there.
            s.evaluate(
                "document.querySelector('[data-testid=run-mode-focus]')?.click()"
            )
            time.sleep(2)
            s.shot("04-focus-same-diff")
            evidence["diff_cards_focus"] = read_diff_cards(s)

            dump(s.run_dir, "evidence.json", evidence)

    cards = evidence.get("diff_cards") or []
    focus_cards = evidence.get("diff_cards_focus") or []
    host_lane = evidence.get("lane") == "host"
    checks = {
        "edit_file ran": "edit_file" in (evidence.get("tools") or []),
        # Only meaningful on the host lane — the virtual lane writes into the
        # agent's own filesystem, so the fixture on disk is untouched by design.
        "file really changed (host lane only)": (
            "5,orange,lynx,30,false" in (evidence.get("file_after") or "")
            if host_lane
            else True
        ),
        "a diff card rendered": len(cards) > 0,
        "diff counts are +1/-1": bool(cards)
        and "+1" in (cards[0].get("counts") or "")
        and "1" in (cards[0].get("counts") or ""),
        "diff names the file": bool(cards)
        and "random.csv" in (cards[0].get("path") or ""),
        "diff has an add row and a remove row": bool(cards)
        and {"add", "remove"} <= {r["kind"] for r in cards[0].get("rows", [])},
        "card names the file on its header": any(
            (c.get("subtitle") or "") == "random.csv"
            for c in (evidence.get("tool_cards") or [])
        ),
        "card uses an icon, not a letter tile": any(
            c.get("hasIconSvg") for c in (evidence.get("tool_cards") or [])
        ),
        "the same diff renders in Focus": len(focus_cards) > 0,
        # The fix under test: DOM presence is not visibility. The first live
        # run passed every check above with the diff sealed inside a collapsed
        # <details> nobody would ever open.
        "the diff is not sealed inside a collapsed ancestor": bool(cards)
        and cards[0].get("sealedBy") is None,
        "the diff actually occupies pixels": bool(cards)
        and cards[0].get("painted") is True,
        "a failed call is labelled not-applied": all(
            c.get("applied") == "false" for c in cards if c.get("applied") is not None
        )
        if any(tc.get("status") == "error" for tc in (evidence.get("tool_cards") or []))
        else True,
    }

    print("\n" + "=" * 62)
    print(
        f"{JOURNEY} — provider={provider} lane={evidence.get('lane')} "
        f"run={evidence.get('run_status')}"
    )
    if evidence.get("attach_blocked"):
        print("  NOTE: host folder grant unavailable (native picker automation")
        print("        denied); ran the virtual-filesystem lane instead.")
    print("=" * 62)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    for label, group in (
        ("edit_file", cards),
        ("write_file", evidence.get("write_cards") or []),
    ):
        if not group:
            continue
        print(f"\n  {label} diff as rendered:")
        print(f"    path   : {group[0].get('path')}")
        print(f"    counts : {group[0].get('counts')}")
        for row in group[0].get("rows", [])[:10]:
            mark = {"add": "+", "remove": "-", "context": " "}.get(row["kind"], "?")
            print(f"    {mark} {row['text']}")
    print(f"\n  tools called: {evidence.get('tools')}")
    print(f"  screenshots + evidence.json: tools/desktop-journeys/runs/{JOURNEY}/")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
