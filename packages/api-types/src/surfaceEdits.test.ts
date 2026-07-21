// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  type ApprovalDecision,
  type ApprovalDecisionRequest,
  type ApprovalResolvedPayload,
  isSurfaceEdits,
  type SurfaceEdits,
} from "./index";

// PRD-09a (Wave 3) — pins the edit/commit CONTRACT slice the backend
// commit executor (09b) and FE edit overlay (09c) code against:
//   - `approve_with_edits` is an additive `ApprovalDecision` member;
//   - `SurfaceEdits` is `{ fields?, body?, accepted_hunk_ids? }`;
//   - both `ApprovalDecisionRequest.edits` and the audit-visible
//     `ApprovalResolvedPayload.edits` carry it.
// Types are compile-time; the `isSurfaceEdits` guard is the runtime SSOT.

describe("ApprovalDecision — approve_with_edits is additive", () => {
  it("accepts approve_with_edits without dropping approve/reject", () => {
    const decisions: ApprovalDecision[] = [
      "approved",
      "rejected",
      "forwarded",
      "suggest_edit",
      "approve_with_edits",
    ];
    // A type-level assertion the union still admits the legacy members
    // alongside the new one — a narrowing regression would fail `tsc`.
    expect(decisions).toContain("approved");
    expect(decisions).toContain("rejected");
    expect(decisions).toContain("approve_with_edits");
  });
});

describe("ApprovalDecisionRequest.edits — carries SurfaceEdits", () => {
  it("types an approve_with_edits request with fields, body and hunks", () => {
    const request: ApprovalDecisionRequest = {
      decision: "approve_with_edits",
      decided_by_user_id:
        "user_1" as ApprovalDecisionRequest["decided_by_user_id"],
      edits: {
        fields: { subject: "Revised subject" },
        body: "Edited body copy.",
        accepted_hunk_ids: ["h1", "h3"],
      },
    };
    expect(request.edits?.body).toBe("Edited body copy.");
    expect(request.edits?.accepted_hunk_ids).toEqual(["h1", "h3"]);
  });

  it("permits plain approve/reject with no edits (byte-identical legacy shape)", () => {
    const approve: ApprovalDecisionRequest = {
      decision: "approved",
      decided_by_user_id:
        "user_1" as ApprovalDecisionRequest["decided_by_user_id"],
    };
    expect(approve.edits).toBeUndefined();
  });
});

describe("ApprovalResolvedPayload.edits — audit-visible mirror", () => {
  it("mirrors the applied edits on the resolved event", () => {
    const resolved: ApprovalResolvedPayload = {
      approval_id: "appr_1" as ApprovalResolvedPayload["approval_id"],
      status: "approved",
      decision: "approve_with_edits",
      edits: { body: "Committed body." },
    };
    expect(resolved.edits?.body).toBe("Committed body.");
    // An edited approval still resolves to the "approved" terminal status.
    expect(resolved.status).toBe("approved");
  });
});

describe("isSurfaceEdits — structural runtime guard", () => {
  it("accepts an empty object (every field optional)", () => {
    expect(isSurfaceEdits({})).toBe(true);
  });

  it("accepts a fully-populated SurfaceEdits", () => {
    const edits: SurfaceEdits = {
      fields: { to: "a@b.com", subject: "Hi" },
      body: "Body.",
      accepted_hunk_ids: ["h1"],
    };
    expect(isSurfaceEdits(edits)).toBe(true);
  });

  it("rejects non-string field values", () => {
    expect(isSurfaceEdits({ fields: { subject: 42 } })).toBe(false);
  });

  it("rejects a non-string body", () => {
    expect(isSurfaceEdits({ body: 7 })).toBe(false);
  });

  it("rejects non-string hunk ids and non-array hunk lists", () => {
    expect(isSurfaceEdits({ accepted_hunk_ids: [1, 2] })).toBe(false);
    expect(isSurfaceEdits({ accepted_hunk_ids: "h1" })).toBe(false);
  });

  it("rejects non-object inputs", () => {
    expect(isSurfaceEdits(null)).toBe(false);
    expect(isSurfaceEdits("edits")).toBe(false);
    expect(isSurfaceEdits(["h1"])).toBe(false);
  });
});
