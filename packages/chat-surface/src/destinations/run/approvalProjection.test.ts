// PR-3.10 — approval projection unit tests (FR-3.3 / FR-3.22 / FR-3.12).
//
// The projector is a pure selector over the canonical run event stream; these
// pin the request→resolve reduction, the optimistic-decision overlay, and the
// rail-queue mapping the RunDestination integration test relies on.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  overlayApprovalDecisions,
  projectApprovals,
  toApprovalsQueue,
  type RunApprovalDecision,
} from "./approvalProjection";

let seq = 0;

function envelope(
  overrides: Partial<RuntimeEventEnvelope> & {
    event_type: RuntimeEventEnvelope["event_type"];
  },
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `e-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    activity_kind: "approval",
    payload: {},
    created_at: new Date(1716000000000 + seq * 1000).toISOString(),
    ...overrides,
  } as RuntimeEventEnvelope;
}

function requested(approvalId: string): RuntimeEventEnvelope {
  return envelope({
    event_type: "approval_requested",
    payload: {
      approval_id: approvalId,
      approval_kind: "mcp_tool",
      display_name: "Post to #launch-aurora",
      message: "Posts the launch note",
      server_name: "SLACK",
      read_only: false,
      arguments: { channel: "#launch-aurora", dry_run: false },
    },
  });
}

function resolved(
  approvalId: string,
  decision: "approved" | "rejected",
): RuntimeEventEnvelope {
  return envelope({
    event_type: "approval_resolved",
    payload: { approval_id: approvalId, decision, status: decision },
  });
}

describe("projectApprovals", () => {
  it("returns an empty projection for no events", () => {
    const projection = projectApprovals([]);
    expect(projection.approvals).toHaveLength(0);
    expect(projection.pending).toHaveLength(0);
    expect(projection.resolved).toHaveLength(0);
  });

  it("opens a pending approval on approval_requested with card fields", () => {
    seq = 0;
    const projection = projectApprovals([requested("a-1")]);
    expect(projection.pending).toHaveLength(1);
    const approval = projection.pending[0];
    expect(approval.approvalId).toBe("a-1");
    expect(approval.title).toBe("Post to #launch-aurora");
    expect(approval.approvalKind).toBe("mcp_tool");
    expect(approval.category).toEqual({ vendor: "SLACK", access: "ACTION" });
    expect(approval.target).toBe("#launch-aurora");
    // Primitive arguments become the inset key/value frame.
    expect(approval.params).toEqual([
      { label: "channel", value: "#launch-aurora" },
      { label: "dry_run", value: "false" },
    ]);
    expect(approval.resolved).toBe(false);
    expect(approval.decision).toBeNull();
  });

  it("settles an approval on approval_resolved with the decision", () => {
    seq = 0;
    const projection = projectApprovals([
      requested("a-1"),
      resolved("a-1", "approved"),
    ]);
    expect(projection.pending).toHaveLength(0);
    expect(projection.resolved).toHaveLength(1);
    expect(projection.resolved[0].decision).toBe("approved");
    expect(projection.resolved[0].resolvedAtMs).not.toBeNull();
  });

  it("is idempotent on replayed (duplicate event_id) frames", () => {
    seq = 0;
    const req = requested("a-1");
    const projection = projectApprovals([req, req]);
    expect(projection.approvals).toHaveLength(1);
  });

  it("preserves request order across multiple approvals", () => {
    seq = 0;
    const projection = projectApprovals([requested("a-1"), requested("a-2")]);
    expect(projection.approvals.map((a) => a.approvalId)).toEqual([
      "a-1",
      "a-2",
    ]);
  });
});

describe("overlayApprovalDecisions", () => {
  it("optimistically resolves a pending approval; server-resolved wins", () => {
    seq = 0;
    const base = projectApprovals([
      requested("a-1"),
      requested("a-2"),
      resolved("a-2", "rejected"),
    ]);
    const local = new Map<string, RunApprovalDecision>([["a-1", "approved"]]);
    const overlaid = overlayApprovalDecisions(base, local);

    expect(overlaid.pending).toHaveLength(0);
    const byId = new Map(overlaid.approvals.map((a) => [a.approvalId, a]));
    expect(byId.get("a-1")?.resolved).toBe(true);
    expect(byId.get("a-1")?.decision).toBe("approved");
    // The server rejection is untouched by the (absent) local decision.
    expect(byId.get("a-2")?.decision).toBe("rejected");
  });

  it("returns the same projection when there are no local decisions", () => {
    seq = 0;
    const base = projectApprovals([requested("a-1")]);
    expect(overlayApprovalDecisions(base, new Map())).toBe(base);
  });
});

describe("toApprovalsQueue", () => {
  it("splits the projection into pending + recent queue items", () => {
    seq = 0;
    const projection = projectApprovals([
      requested("a-1"),
      requested("a-2"),
      resolved("a-2", "approved"),
    ]);
    const queue = toApprovalsQueue(projection);
    expect(queue.pending.map((i) => i.approvalId)).toEqual(["a-1"]);
    expect(queue.recent.map((i) => i.approvalId)).toEqual(["a-2"]);
    expect(queue.recent[0].resolved).toBe(true);
    expect(queue.recent[0].resolvedAt).not.toBeNull();
  });

  it("maps an empty projection to an empty queue", () => {
    const queue = toApprovalsQueue(projectApprovals([]));
    expect(queue.pending).toHaveLength(0);
    expect(queue.recent).toHaveLength(0);
  });
});
