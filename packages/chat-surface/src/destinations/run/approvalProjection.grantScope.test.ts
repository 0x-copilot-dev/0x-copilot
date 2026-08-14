// `grant_options` — the once/always scope, and the lane split that decides
// whether an `always` on a card means anything at all.
//
// The backend has emitted this key on every filesystem approval and every write
// gate since before this program; the only app-code reference in the repo was a
// strip list. Binding it is not "render the array": the SAME wire word carries
// two different acts, and only one of them survives the `/decision` POST.
//
//   * write gate (`mcp_write:` ids) — `always` writes a RUN-SCOPED allow rule
//     over the subjects the call already carried. Its `ask_a_question` resume
//     shape is the only one `ApprovalResumeBuilder` forwards `decision_scope`
//     on, so this is the one lane where the control does something.
//   * filesystem (`filesystem_access`) — `always` ATTACHES A FOLDER: durable,
//     wider than the path the card named, settled by `WorkspaceGrantPort` and an
//     OS dialog. `decision_scope` is dropped on that lane
//     (`runtime_worker/stream_events.py:227-234` records the reasoning, and
//     names the composer's bypass pill as the control for repeated writes).
//
// So these tests are mostly about what the projection REFUSES to advertise.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  projectApprovals,
  WRITE_GATE_APPROVAL_PREFIX,
} from "./approvalProjection";

let seq = 0;

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
      display_name: "Create an issue in Parth-test",
      server_name: "linear",
      read_only: false,
      ...payload,
    },
  } as RuntimeEventEnvelope;
}

const GATE_ID = `${WRITE_GATE_APPROVAL_PREFIX}run-1:call-1`;

function project(
  approvalId: string,
  payload: Record<string, unknown> = {},
): ReturnType<typeof projectApprovals>["approvals"][number] {
  return projectApprovals([requested(approvalId, payload)]).approvals[0]!;
}

describe("projectApprovals — grant_options", () => {
  it("carries the server's list verbatim, rather than a boolean it invented", () => {
    const approval = project(GATE_ID, {
      grant_options: ["allow_once", "allow_always"],
    });
    expect(approval.grantOptions).toEqual(["allow_once", "allow_always"]);
  });

  it("drops non-string entries exactly as the server's own projection does", () => {
    const approval = project(GATE_ID, {
      grant_options: ["allow_once", 7, null, { x: 1 }, "allow_always"],
    });
    expect(approval.grantOptions).toEqual(["allow_once", "allow_always"]);
  });

  it("is empty — never undefined — when the payload named no scopes", () => {
    expect(project(GATE_ID).grantOptions).toEqual([]);
  });

  it("refuses a `grant_options` that is not a list instead of coercing it", () => {
    expect(
      project(GATE_ID, { grant_options: "allow_always" }).grantOptions,
    ).toEqual([]);
  });

  it("keeps the list a REDELIVERED frame omitted, in the safe direction", () => {
    // Sticky toward what the first frame advertised: losing the option costs a
    // re-ask, while a frame that could retract it mid-read would move a button
    // out from under the cursor.
    const approvals = projectApprovals([
      requested(GATE_ID, { grant_options: ["allow_once", "allow_always"] }),
      requested(GATE_ID),
    ]).approvals;
    expect(approvals).toHaveLength(1);
    expect(approvals[0].grantOptions).toEqual(["allow_once", "allow_always"]);
  });
});

describe("projectApprovals — which `always` the /decision POST actually carries", () => {
  it("offers the run-scoped grant on the write-gate lane", () => {
    expect(
      project(GATE_ID, { grant_options: ["allow_once", "allow_always"] })
        .allowsRunScopedGrant,
    ).toBe(true);
  });

  it("withholds it when the server offered only `allow_once`", () => {
    // `ToolAccessGate._grant_options` returns exactly this for a destructive op,
    // on the reasoning that puts the destructive rung above BYPASS: an advance
    // yes to a class of deletes is the thing that rung exists to prevent.
    expect(
      project(GATE_ID, { grant_options: ["allow_once"] }).allowsRunScopedGrant,
    ).toBe(false);
  });

  it("withholds it on the FILESYSTEM lane, where `always` attaches a folder", () => {
    // The payload the filesystem lane really emits — `allow_always` PLUS the
    // `grant_scope` folder it would attach. Offering this card a run-scoped
    // "always" would post a `decision_scope` that lane's resume builder drops,
    // and would describe an act (a durable folder grant) the button is not
    // performing. That decision belongs to `WorkspaceGrantCard`.
    const approval = project("appr-fs-1", {
      approval_kind: "filesystem_access",
      grant_options: ["allow_once", "allow_always"],
      grant_scope: {
        path: "/Users/p/reports",
        folder_name: "reports",
        platform: "posix",
        mode: "read_only",
      },
      read_only: true,
    });
    expect(approval.grantOptions).toEqual(["allow_once", "allow_always"]);
    expect(approval.allowsRunScopedGrant).toBe(false);
  });

  it("withholds it on an ordinary mcp_tool ask, whose resume shape drops the scope", () => {
    expect(
      project("appr-mcp-1", {
        approval_kind: "mcp_tool",
        grant_options: ["allow_once", "allow_always"],
      }).allowsRunScopedGrant,
    ).toBe(false);
  });

  it("withholds it on an `mcp_auth` connect gate, which is a different question", () => {
    expect(
      project("mcp_auth:run-1:linear", {
        approval_kind: "mcp_auth",
        grant_options: ["allow_once", "allow_always"],
      }).allowsRunScopedGrant,
    ).toBe(false);
  });
});
