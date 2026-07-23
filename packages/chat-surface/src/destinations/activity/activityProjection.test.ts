// @vitest-environment node
import type { AuditEvent, Conversation } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { projectActivityRows } from "./activityProjection";

// Minimal Conversation fixture — fills the non-load-bearing required fields so
// the tests state only the fields the projection reads (PRD-04 Seam C).
function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: "conv_1",
    org_id: "org_1",
    user_id: "user_1",
    assistant_id: "asst_1",
    title: "Weekly treasury reconciliation",
    status: "active",
    created_at: "2026-07-18T08:00:00Z",
    updated_at: "2026-07-18T09:15:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
    latest_run_id: "run_1",
    latest_run_status: "running",
    ...over,
  };
}

function audit(over: Partial<AuditEvent> = {}): AuditEvent {
  return {
    stream: "mcp_audit_events",
    seq: 1,
    audit_id: "aud_1",
    org_id: "org_1",
    actor_user_id: "user_1",
    actor_kind: "user",
    subject_user_id: null,
    action: "tool.invoke",
    resource_type: "run",
    resource_id: "run_1",
    outcome: "success",
    metadata: { connector_id: "Sheets" },
    chain: {} as AuditEvent["chain"],
    created_at: "2026-07-18T09:10:00Z",
    ...over,
  };
}

describe("projectActivityRows — conversation + audit → ActivityRunRow[]", () => {
  // DoD 6 — conversation_id and run_id are DISTINCT fields, both stamped.
  it("stamps conversation_id from conversation.conversation_id and run_id from latest_run_id", () => {
    const rows = projectActivityRows(
      [
        conv({
          conversation_id: "conv_abc",
          latest_run_id: "run_xyz",
        }),
      ],
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]!.conversation_id).toBe("conv_abc");
    expect(rows[0]!.run_id).toBe("run_xyz");
    // The two are different fields, not aliases.
    expect(rows[0]!.conversation_id).not.toBe(rows[0]!.run_id);
  });

  // DoD 6 — the blank-title fallback.
  it("falls back to 'Untitled run' when the conversation title is blank/whitespace", () => {
    const rows = projectActivityRows([conv({ title: "   " })], []);
    expect(rows[0]!.title).toBe("Untitled run");
  });

  it("uses the trimmed conversation title when present", () => {
    const rows = projectActivityRows(
      [conv({ title: "  Draft investor update  " })],
      [],
    );
    expect(rows[0]!.title).toBe("Draft investor update");
  });

  it("skips never-ran conversations (no latest_run_id / status)", () => {
    const rows = projectActivityRows(
      [
        conv({ conversation_id: "c_ran", latest_run_id: "run_1" }),
        conv({
          conversation_id: "c_never",
          latest_run_id: null,
          latest_run_status: null,
        }),
        conv({
          conversation_id: "c_empty",
          latest_run_id: "",
          latest_run_status: "running",
        }),
      ],
      [],
    );
    expect(rows.map((r) => r.conversation_id)).toEqual(["c_ran"]);
  });

  it("maps the runtime run status onto the Activity taxonomy", () => {
    const rows = projectActivityRows(
      [
        conv({
          conversation_id: "c1",
          latest_run_status: "waiting_for_approval",
        }),
      ],
      [],
    );
    expect(rows[0]!.status).toBe("needs_input");
  });

  it("enriches meta from audit rows keyed by BOTH run id and conversation id, sorted + joined", () => {
    const rows = projectActivityRows(
      [conv({ conversation_id: "conv_1", latest_run_id: "run_1" })],
      [
        audit({ resource_id: "run_1", metadata: { connector_id: "Sheets" } }),
        audit({ resource_id: "conv_1", metadata: { server_id: "Dune" } }),
        audit({ resource_id: "run_1", metadata: { tool_name: "Safe" } }),
      ],
    );
    expect(rows[0]!.meta).toBe("Dune · Safe · Sheets");
  });

  it("sorts rows newest-first, with unparseable timestamps last (canonical NaN guard)", () => {
    const rows = projectActivityRows(
      [
        conv({
          conversation_id: "c_old",
          latest_run_id: "r_old",
          updated_at: "2026-07-10T00:00:00Z",
        }),
        conv({
          conversation_id: "c_bad",
          latest_run_id: "r_bad",
          updated_at: "not-a-date",
        }),
        conv({
          conversation_id: "c_new",
          latest_run_id: "r_new",
          updated_at: "2026-07-18T00:00:00Z",
        }),
      ],
      [],
    );
    expect(rows.map((r) => r.conversation_id)).toEqual([
      "c_new",
      "c_old",
      "c_bad",
    ]);
  });
});
