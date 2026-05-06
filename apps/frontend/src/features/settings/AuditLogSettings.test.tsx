/**
 * PR 7.1 — AuditLogSettings shallow tests.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  AuditEvent,
  ListAuditEventsResponse,
} from "@enterprise-search/api-types";
import { AuditLogSettings } from "./AuditLogSettings";

vi.mock("../../api/auditApi", () => ({
  listAuditEvents: vi.fn(async (): Promise<ListAuditEventsResponse> => {
    return {
      rows: [makeRow()],
      next_cursor: null,
      has_more: false,
      degraded_streams: [],
    };
  }),
}));

function makeRow(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    stream: "identity_audit_events",
    seq: 1,
    audit_id: "audit-1",
    org_id: "org_a",
    actor_user_id: "user_marcus",
    actor_kind: "user",
    subject_user_id: "user_sarah",
    action: "member.added",
    resource_type: "user",
    resource_id: "user_sarah",
    outcome: "success",
    metadata: {},
    chain: { seq: 1, prev_hash: null, signature: null, key_version: null },
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const identity = { orgId: "org_a", userId: "u1" };

describe("AuditLogSettings", () => {
  it("hides table for non-admin callers", () => {
    render(<AuditLogSettings identity={identity} isAdmin={false} />);
    expect(screen.getByText(/visible to workspace admins/i)).toBeTruthy();
  });

  it("renders audit row for admin caller", async () => {
    render(<AuditLogSettings identity={identity} isAdmin={true} />);
    expect(await screen.findByText("member.added")).toBeTruthy();
  });
});
