// The COMMAND lane's projection contract (PRD-shell-execution §14.1, §18 Phase 0).
//
// A `run_command` ask is drawn by the same card as a parked MCP write, and that
// is a reuse of a WIRE SHAPE — `approval_kind: "ask_a_question"`, so the resume
// carries `decision_scope` and the server's allow-list projects `op_class` —
// and NOT a shared id. The two lanes have different producers:
//
//   * an MCP write is minted by `PolicyToolMiddleware._approval_id` as
//     `mcp_write:<run_id>:<tool_call_id>`;
//   * a command is minted by `runtime_worker/stream_events.py::_CommandApproval`
//     as `<interrupt_id>:<index>`, because that branch serves LangGraph's own
//     native `action_requests` interrupt and its siblings mint the same way.
//
// ⚠️ THIS FILE ONCE ASSERTED OTHERWISE, and that is the reason to read the note
// rather than skim it. It built its fixture on an `mcp_write:` id and pinned the
// resulting behaviour green, while the producer — pinned green by its own tests
// — minted the other shape. Nothing in the tree mints an `mcp_write:` id AND
// stamps `command`, so the premise was never true of any payload that could
// exist, and the client's card selection, keyed on that prefix, dropped a
// command ask into the QUESTION branch: a yes/no about a shell command drawn as
// a free-text answer box. The fix keys card selection on the payload
// (`TcChat.isCommandApproval` reads `RunApproval.command`), which is a fact the
// producer really stamps. What this file pins now:
//
//  1. **No new `approval_kind`.** The kind IS the resume shape
//     (`ApprovalResumeBuilder` forwards `decision_scope` on that branch alone)
//     and it IS the server's allow-list (`_approval_requested_payload`
//     early-returns into `_ask_a_question_requested_payload`). A bespoke kind
//     would land on the sibling list, arrive here unlisted, and fall through
//     `mapApprovalKind` to `"unknown"`.
//  2. **The producer's id, not `mcp_write:`.** Which means `allowsRunScopedGrant`
//     is FALSE on every command card — and agrees with the server, which sends
//     `grant_options: ["allow_once"]` for a command anyway. See the last
//     describe block: both halves have to move together, or §8.3 ships a dead
//     control.
//  3. **`irreversible` and the always-grant are two facts, not one.** A command
//     is never one-click approvable (`risk_level: "high"`), independently of
//     whatever scope the server offers (`op_class: "execute"`). Read from one
//     field, one of those two answers is wrong.
//
// NOTHING IS LIVE YET — but less dark than it was. `_CommandApproval` stamps
// `command` and `_ask_a_question_requested_payload` now projects it, so these
// payload shapes are the producer's, not the PRD's. What is missing is the TOOL:
// nothing raises the `run_command` interrupt, so no run produces one of these.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  projectApprovals,
  WRITE_GATE_APPROVAL_PREFIX,
} from "./approvalProjection";

let seq = 0;

/** `_CommandApproval.payload` for a simple command, minus what a case varies. */
function requested(
  approvalId: string,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `e-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    activity_kind: "approval",
    event_type: "approval_requested",
    created_at: new Date(1716000000000 + seq * 1000).toISOString(),
    payload: {
      approval_id: approvalId,
      approval_kind: "ask_a_question",
      display_name: "Run `pytest -q` in my-project",
      // The producer writes ONE sentence into both keys — two spellings of one
      // ask is how a card contradicts itself — so `parseQuestion` succeeds on
      // every command ask. Which is exactly why neither the KIND nor the
      // QUESTION can tell a command apart from a genuine agent question, and
      // `command` has to be what saves this card.
      message: "Allow this command to run?",
      question: "Allow this command to run?",
      command: "pytest -q",
      op_class: "execute",
      risk_level: "high",
      // `_CommandApproval.GRANT_OPTIONS`, verbatim: `("allow_once",)`. The
      // producer has no tokeniser, so it cannot tell `pytest -q` from
      // `pytest -q && curl … | sh` — whose `argv[0]` is also `pytest` — and it
      // gives the withholding answer rather than a rule nobody was shown.
      grant_options: ["allow_once"],
      status: "pending",
      ...payload,
    },
  } as RuntimeEventEnvelope;
}

/**
 * The interrupt LangGraph raised, by its own `id` — opaque, and prefix-free.
 * `_native_interrupt_id` reads exactly that field, falling back to
 * `interrupt:<run_id>:<n>` only when the interrupt carries none. Neither shape
 * begins with `mcp_write:`, which is the whole point.
 */
const INTERRUPT_ID = "7d1b0c9a4e6f28315ac0b7e93d5f1602";

/**
 * The approval id the producer really mints: `f"{interrupt_id}:{index}"`
 * (`stream_events.py::_CommandApproval.payload`), where the index is the
 * action's position in the interrupt's `action_requests` list.
 *
 * NOT `mcp_write:<run>:<call>` — that shape belongs to
 * `PolicyToolMiddleware._approval_id` and to the MCP lane alone. The difference
 * is not cosmetic: the segment after the colon here is an ACTION INDEX, so an
 * id wearing the write-gate prefix would hand `eventProjector.writeGateCallId`
 * an index where it expects a `tool_call_id`.
 */
const COMMAND_ID = `${INTERRUPT_ID}:0`;

function project(
  approvalId: string = COMMAND_ID,
  payload: Record<string, unknown> = {},
): ReturnType<typeof projectApprovals>["approvals"][number] {
  return projectApprovals([requested(approvalId, payload)]).approvals[0]!;
}

describe("projectApprovals — the command itself", () => {
  it("carries the command verbatim, as the thing the card is approved over", () => {
    expect(project().command).toBe("pytest -q");
  });

  it("keeps newlines and interior spacing — the bytes ARE the command", () => {
    // `/bin/sh -c "<command>"` runs what the model sent. A projection that
    // collapsed whitespace would put a different command on the card from the
    // one that executes, and the difference between `rm -rf ./build` and
    // `rm -rf . /build` is one space.
    const multiline = "cd packages/chat-surface\nnpx  vitest run --root .";
    expect(project(COMMAND_ID, { command: multiline }).command).toBe(multiline);
  });

  it("is null when the payload named no command, never an empty string", () => {
    // Null is what makes an ask NOT a command ask. An empty string would be a
    // command ask with nothing to show — a card that unlocks Approve over a
    // blank frame is the blind approval the whole lane exists to prevent.
    expect(project(COMMAND_ID, { command: undefined }).command).toBeNull();
    expect(project(COMMAND_ID, { command: "" }).command).toBeNull();
    expect(project(COMMAND_ID, { command: "   " }).command).toBeNull();
  });

  it("refuses a non-string command rather than coercing it", () => {
    expect(
      project(COMMAND_ID, { command: ["pytest", "-q"] }).command,
    ).toBeNull();
  });

  it("keeps a command a REDELIVERED frame omitted", () => {
    // Same replay rule as `presentation` / `workspaceGrant`, and the
    // safety-critical instance of it: the command is what unlocks Approve, so a
    // frame that could retract it would pull the evidence out from under a
    // decision already being read.
    const approvals = projectApprovals([
      requested(COMMAND_ID),
      requested(COMMAND_ID, { command: undefined }),
    ]).approvals;
    expect(approvals).toHaveLength(1);
    expect(approvals[0].command).toBe("pytest -q");
  });

  it("is keyed on the payload block, never on the kind", () => {
    // Same rule as `workspaceGrant`: whichever interrupt a backend already
    // emits becomes a command ask by stamping one field. It has to be, because
    // the kind cannot discriminate here — the command lane rides
    // `ask_a_question` verbatim, so an MCP write and a command are the same
    // kind and only this field tells them apart.
    expect(project("appr-mcp-1", { approval_kind: "mcp_tool" }).command).toBe(
      "pytest -q",
    );
    expect(
      project("appr-mcp-1", { approval_kind: "mcp_tool", command: undefined })
        .command,
    ).toBeNull();
  });
});

describe("projectApprovals — the command lane's approval_kind", () => {
  it("is `ask_a_question`, the write gate's shape, and NOT a fourth kind", () => {
    // The decision recorded in `mapApprovalKind`. It buys three things at once:
    // `decision_scope` on the resume, `op_class` through the server's
    // allow-list, and a card the client already knows how to draw.
    expect(project().approvalKind).toBe("ask_a_question");
  });

  it("parses a question spec too — which is why the PAYLOAD is what saves the card", () => {
    // Both halves of the bug, in one assertion pair.
    //
    // The producer mirrors its title into `question` (two spellings of one ask
    // is how a card contradicts itself), so `parseQuestion` succeeds on every
    // command ask — this payload IS a valid question spec. And the id does not
    // carry the write-gate prefix. So `renderApprovalItem`, which checked
    // `isWriteGateApproval` first and fell through to the question branch,
    // drew a free-text answer box over a shell command.
    //
    // `TcChat.isCommandApproval` now keys on `command` instead, which is why
    // the projection carrying that field verbatim is load-bearing rather than
    // decorative. Pinned from here so the coupling is visible on this side too.
    expect(project().question).not.toBeNull();
    expect(project().approvalId.startsWith(WRITE_GATE_APPROVAL_PREFIX)).toBe(
      false,
    );
    expect(project().command).not.toBeNull();
  });

  it("falls through to `unknown` for a bespoke kind — the tripwire, not a feature", () => {
    // If this value ever appears on a real command card, someone minted a new
    // `approval_kind`: the payload then rides `_approval_requested_payload`,
    // which drops `op_class`, and its resume builder drops `decision_scope`.
    // The card still draws, which is why the failure is silent.
    expect(
      project(COMMAND_ID, { approval_kind: "run_command" }).approvalKind,
    ).toBe("unknown");
  });
});

describe("projectApprovals — a command is never one-click approvable", () => {
  it("draws irreversible from `risk_level`, which is the field §14.1 chose", () => {
    expect(project().irreversible).toBe(true);
  });

  it("stays irreversible on `risk_level` ALONE, with no op_class at all", () => {
    // The two knobs are deliberately decoupled, and this is the half that has
    // to survive the sibling allow-list — which projects `risk_level` and drops
    // `op_class`. If irreversibility depended on `op_class`, a lane change
    // upstream would silently hand back the one-click Approve.
    expect(project(COMMAND_ID, { op_class: undefined }).irreversible).toBe(
      true,
    );
  });

  it("does NOT read `op_class: execute` as destructive", () => {
    // The other half of the decoupling. `execute` is a fourth op class, not a
    // synonym for `destructive`: reading it as one would cost the always-grant
    // server-side (`_grant_options` withholds `allow_always` for exactly one
    // class) with no client change able to recover it.
    expect(project(COMMAND_ID, { risk_level: undefined }).irreversible).toBe(
      false,
    );
  });
});

// §8.3's run-scoped grant is NOT reachable on a command card today, and this
// block pins that as a two-sided fact so nobody "fixes" one side alone.
//
// `allowsRunScopedGrant` is an AND: the server must have offered `allow_always`,
// AND the id must carry the write-gate prefix. A command ask fails BOTH, and the
// two failures agree — which is what makes the current state coherent rather
// than broken. Shipping §8.3 means moving both halves in one change:
//
//   * server — `_CommandApproval.GRANT_OPTIONS` widens, which needs the
//     tokeniser verdict `ToolAccessGate._grant_options(simple_command=)` that
//     the projection deliberately does not have;
//   * client — this predicate stops keying on the prefix.
//
// Move only the server and the client withholds an option the user was offered.
// Move only the client and it draws an "always" the server drops on the
// `/decision` POST — the dead-control shape.
describe("projectApprovals — §8.3's run-scoped grant on a command card", () => {
  it("is withheld, because the SERVER did not offer it", () => {
    // The first half of the AND, and the one that is true of the real wire:
    // `_CommandApproval.GRANT_OPTIONS == ("allow_once",)`. Withholding costs a
    // click; offering it wrongly hands over a rule nobody was shown.
    const approval = project();
    expect(approval.grantOptions).toEqual(["allow_once"]);
    expect(approval.allowsRunScopedGrant).toBe(false);
    // …and still irreversible, which is the decoupling: the two knobs answer
    // "may this be approved in one click?" (never) and "may this decision cover
    // more than this call?" (not yet). One field cannot say both.
    expect(approval.irreversible).toBe(true);
  });

  it("stays withheld even if a future server DID offer it — the id is the other half", () => {
    // The second half of the AND, exercised on its own. This is the assertion
    // that flips when §8.3 lands, and it must be flipped DELIBERATELY, next to
    // the server change — not discovered by a card drawing a control that does
    // nothing.
    expect(
      project(COMMAND_ID, { grant_options: ["allow_once", "allow_always"] })
        .allowsRunScopedGrant,
    ).toBe(false);
  });

  it("is still granted on the MCP write lane this predicate was written for", () => {
    // The contrast that keeps the two above from passing vacuously: the
    // predicate is not simply always-false, and nothing here changed the lane
    // it already served. `mcp_write:` is minted by `PolicyToolMiddleware`
    // alone — a real id from a real producer, unlike the fixture this file
    // used to build the command cases on.
    expect(
      project(`${WRITE_GATE_APPROVAL_PREFIX}run-1:call-1`, {
        command: undefined,
        grant_options: ["allow_once", "allow_always"],
      }).allowsRunScopedGrant,
    ).toBe(true);
  });
});
