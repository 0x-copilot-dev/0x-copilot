import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectToolCalls } from "./eventProjector";

// The two runs this file exists for. Both are `write_file`, one path apart, and
// on screen they were the same stopped card:
//
//   A  write_file("/random.csv")        → refused. Remedy: attach a folder.
//   B  write_file("/drafts/random.csv") → parked.  Remedy: one click.
//
// The wire tells them apart and the projection has always had the evidence,
// because approvals ride the SAME event array (FR-3.3). What follows pins the
// distinction to the frames each lane actually emits — deny returns a failed
// `tool_result` (deepagents builds it itself:
// `ToolMessage(content="Error: permission denied for write on …", status="error")`
// in `deepagents/middleware/filesystem.py`), interrupt returns NOTHING and drops
// an `approval_requested` beside a call that stays open forever.

const BASE = {
  run_id: "run-1",
  conversation_id: "conv-1",
  activity_kind: "event",
  created_at: "2026-08-26T12:00:00.000Z",
};

function started(
  seq: number,
  callId: string,
  toolName: string,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return {
    ...BASE,
    event_id: `s${seq}`,
    sequence_no: seq,
    event_type: "tool_call_started",
    payload: { tool_name: toolName, call_id: callId, args: {}, ...payload },
  } as unknown as RuntimeEventEnvelope;
}

function failed(
  seq: number,
  callId: string,
  toolName: string,
  content: string,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return {
    ...BASE,
    event_id: `r${seq}`,
    sequence_no: seq,
    event_type: "tool_result",
    summary: "0xCopilot couldn't complete this step.",
    payload: {
      tool_name: toolName,
      call_id: callId,
      status: "failed",
      output: { content },
      ...payload,
    },
  } as unknown as RuntimeEventEnvelope;
}

/**
 * The filesystem lane's ask, field for field.
 *
 * Transcribed from `_FilesystemApproval.payload` and then narrowed to what
 * `_approval_requested_payload` actually lets through, because that projection
 * is a strict allow-list and a fixture richer than the wire is how a green
 * suite ships over a field the client never receives. Note what is NOT here:
 * no call id of any kind. `tool_name` is the only handle this lane offers.
 */
function filesystemAsk(
  seq: number,
  toolName: string,
  path: string,
  approvalId: string,
): RuntimeEventEnvelope {
  return {
    ...BASE,
    event_id: `a${seq}`,
    sequence_no: seq,
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: approvalId,
      approval_kind: "filesystem_access",
      batch_id: approvalId.split(":")[0],
      batch_index: 0,
      tool_name: toolName,
      display_name: path.replace(/\/$/, "").split("/").pop(),
      path,
      operation: "write",
      message: `Allow writing to ${path}?`,
      read_only: false,
      risk_level: "high",
      status: "pending",
      grant_options: ["allow_once"],
      arguments: { file_path: path, content: "a,b\n" },
    },
  } as unknown as RuntimeEventEnvelope;
}

function resolved(seq: number, approvalId: string): RuntimeEventEnvelope {
  return {
    ...BASE,
    event_id: `x${seq}`,
    sequence_no: seq,
    event_type: "approval_resolved",
    activity_kind: "approval",
    payload: { approval_id: approvalId, status: "approved" },
  } as unknown as RuntimeEventEnvelope;
}

const DENIED = "Error: permission denied for write on /random.csv";

describe("a refused tool call and a parked one are different states", () => {
  it("run A — the refusal ENDS the call, and names the lane that refused", () => {
    const [call] = projectToolCalls([
      started(1, "c1", "write_file"),
      failed(2, "c1", "write_file", DENIED),
    ]);

    expect(call.status).toBe("error");
    expect(call.blockedBy).toEqual({ kind: "permission", lane: "filesystem" });
    // The reason survives verbatim — the error-copy work this must not regress.
    expect(call.errorMessage).toBe(
      "permission denied for write on /random.csv",
    );
  });

  it("run B — the gate leaves the call OPEN and names the decision", () => {
    // No `tool_result` at all: `HumanInTheLoopMiddleware` parks BEFORE the tool
    // body runs, so the frame that would close this card is never produced.
    const [call] = projectToolCalls([
      started(1, "c1", "write_file"),
      filesystemAsk(2, "write_file", "/drafts/random.csv", "int-7:0"),
    ]);

    expect(call.status).toBe("running");
    expect(call.blockedBy).toEqual({
      kind: "decision",
      approvalId: "int-7:0",
      ask: "Allow writing to /drafts/random.csv?",
    });
  });

  it("does not confuse the two when both happen in one run", () => {
    // The whole bug in one array: same tool, same shape of card, opposite
    // remedies. If either arm leaked into the other, this is where it shows.
    const calls = projectToolCalls([
      started(1, "c1", "write_file"),
      failed(2, "c1", "write_file", DENIED),
      started(3, "c2", "write_file"),
      filesystemAsk(4, "write_file", "/drafts/random.csv", "int-7:0"),
    ]);

    expect(calls.map((c) => c.blockedBy?.kind)).toEqual([
      "permission",
      "decision",
    ]);
  });
});

describe("binding an ask to the call it is holding up", () => {
  it("joins the MCP write gate on the call id inside its own approval id", () => {
    // `mcp_write:<run_id>:<tool_call_id>` (policy_tool.py `_approval_id`). The
    // trailing segment IS the key this projection cards on, so the join is
    // identity — and it stays exact where the tool-name heuristic gives up.
    const calls = projectToolCalls([
      started(1, "call-a", "call_mcp_tool"),
      started(2, "call-b", "call_mcp_tool"),
      {
        ...BASE,
        event_id: "a3",
        sequence_no: 3,
        event_type: "approval_requested",
        payload: {
          approval_id: "mcp_write:run-1:call-b",
          approval_kind: "mcp_tool",
          tool_name: "call_mcp_tool",
          message: "Allow Linear create issue?",
          status: "pending",
        },
      } as unknown as RuntimeEventEnvelope,
    ]);

    expect(calls.map((c) => c.blockedBy?.kind)).toEqual([
      undefined,
      "decision",
    ]);
  });

  it("splits that id on the FIRST colon, so a call id containing one survives", () => {
    // `lastIndexOf` would truncate the key it is meant to reproduce. Nothing
    // promises LangChain's call id is colon-free; the run id is the half we know.
    const [call] = projectToolCalls([
      started(1, "toolu:01:xyz", "call_mcp_tool"),
      {
        ...BASE,
        event_id: "a2",
        sequence_no: 2,
        event_type: "approval_requested",
        payload: {
          approval_id: "mcp_write:run-1:toolu:01:xyz",
          message: "Allow it?",
          status: "pending",
        },
      } as unknown as RuntimeEventEnvelope,
    ]);

    expect(call.blockedBy).toEqual({
      kind: "decision",
      approvalId: "mcp_write:run-1:toolu:01:xyz",
      ask: "Allow it?",
    });
  });

  it("refuses to guess when two calls to the same tool are open", () => {
    // The filesystem ask carries no call id, so with two candidates the honest
    // answer is none. Naming the wrong one is worse than naming none: the
    // reader would approve believing it settles a different write.
    const calls = projectToolCalls([
      started(1, "c1", "write_file"),
      started(2, "c2", "write_file"),
      filesystemAsk(3, "write_file", "/drafts/random.csv", "int-7:0"),
    ]);

    expect(calls.map((c) => c.blockedBy)).toEqual([undefined, undefined]);
  });

  it("still binds when the other open call is a DIFFERENT tool", () => {
    // Ambiguity is per tool name, not "more than one call is open" — otherwise
    // any concurrent read would suppress the gate on the write.
    const calls = projectToolCalls([
      started(1, "c1", "read_file"),
      started(2, "c2", "write_file"),
      filesystemAsk(3, "write_file", "/drafts/random.csv", "int-7:0"),
    ]);

    expect(calls.map((c) => c.blockedBy?.kind)).toEqual([
      undefined,
      "decision",
    ]);
  });

  it("clears the block once the approval is resolved", () => {
    const calls = projectToolCalls([
      started(1, "c1", "write_file"),
      filesystemAsk(2, "write_file", "/drafts/random.csv", "int-7:0"),
      resolved(3, "int-7:0"),
    ]);

    expect(calls[0].blockedBy).toBeUndefined();
  });

  it("never parks a call the run has already moved past", () => {
    // The reason binding runs AFTER the reduce and not inside it: the result
    // frame arrives LATER in the same array than the ask that preceded it, so a
    // mid-loop bind would park a card that has since finished.
    const calls = projectToolCalls([
      started(1, "c1", "write_file"),
      filesystemAsk(2, "write_file", "/drafts/random.csv", "int-7:0"),
      {
        ...BASE,
        event_id: "r3",
        sequence_no: 3,
        event_type: "tool_result",
        payload: {
          tool_name: "write_file",
          call_id: "c1",
          status: "completed",
          output: { content: "Updated file /drafts/random.csv" },
        },
      } as unknown as RuntimeEventEnvelope,
    ]);

    expect(calls[0].status).toBe("complete");
    expect(calls[0].blockedBy).toBeUndefined();
  });

  it("carries the ask as null when the payload had no question of its own", () => {
    // Replay can strip it; a gate with no sentence is still a gate.
    const [call] = projectToolCalls([
      started(1, "c1", "write_file"),
      {
        ...BASE,
        event_id: "a2",
        sequence_no: 2,
        event_type: "approval_requested",
        payload: {
          approval_id: "int-7:0",
          tool_name: "write_file",
          status: "pending",
        },
      } as unknown as RuntimeEventEnvelope,
    ]);

    expect(call.blockedBy).toEqual({
      kind: "decision",
      approvalId: "int-7:0",
      ask: null,
    });
  });

  it("reads the write gate's `question`, since a parked write rides ask_a_question", () => {
    // `_ask_a_question_requested_payload` is a SEPARATE allow-list and spells
    // the sentence `question`; the standard one spells it `message`.
    const [call] = projectToolCalls([
      started(1, "c1", "call_mcp_tool"),
      {
        ...BASE,
        event_id: "a2",
        sequence_no: 2,
        event_type: "approval_requested",
        payload: {
          approval_id: "mcp_write:run-1:c1",
          approval_kind: "ask_a_question",
          question: "Allow Linear create issue?",
          status: "pending",
        },
      } as unknown as RuntimeEventEnvelope,
    ]);

    expect(call.blockedBy).toMatchObject({ ask: "Allow Linear create issue?" });
  });
});

describe("naming the lane a refusal came from", () => {
  it("routes an MCP call by its declared provenance, not by its prose", () => {
    const [call] = projectToolCalls([
      started(1, "c1", "call_mcp_tool", {
        provenance: { source: "mcp", server_name: "Linear" },
      }),
      failed(2, "c1", "call_mcp_tool", "Error: permission denied", {
        provenance: { source: "mcp", server_name: "Linear" },
      }),
    ]);

    expect(call.blockedBy).toEqual({ kind: "permission", lane: "connector" });
  });

  it("says nothing when the denial cannot be placed on a lane", () => {
    // A tool that is neither an MCP call nor a deepagents filesystem tool was
    // refused by something we cannot name. The card keeps the reason alone —
    // "attach that folder" would be a wrong answer delivered confidently.
    const [call] = projectToolCalls([
      started(1, "c1", "some_custom_tool"),
      failed(2, "c1", "some_custom_tool", "Error: permission denied"),
    ]);

    expect(call.blockedBy).toBeUndefined();
    expect(call.errorMessage).toBe("permission denied");
  });

  it("leaves an ordinary failure unblocked", () => {
    const [call] = projectToolCalls([
      started(1, "c1", "write_file"),
      failed(2, "c1", "write_file", "Error: disk full"),
    ]);

    expect(call.status).toBe("error");
    expect(call.blockedBy).toBeUndefined();
  });

  it("matches the two words case-insensitively and without a sticky regex", () => {
    // A `g` flag would carry `lastIndex` between calls, so the same message
    // would match on one card and miss on the next. Two identical denials in
    // one projection is the cheapest way to catch that.
    const calls = projectToolCalls([
      started(1, "c1", "write_file"),
      failed(2, "c1", "write_file", "Error: Permission Denied for write on /a"),
      started(3, "c2", "write_file"),
      failed(4, "c2", "write_file", "Error: Permission Denied for write on /b"),
    ]);

    expect(calls.map((c) => c.blockedBy)).toEqual([
      { kind: "permission", lane: "filesystem" },
      { kind: "permission", lane: "filesystem" },
    ]);
  });
});
