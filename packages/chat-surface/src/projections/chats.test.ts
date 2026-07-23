// Chats per-row projection tests (PRD-03 DoD 8).

import type { Conversation } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { toChatArchiveRow } from "./chats";

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: "conv-1",
    org_id: "org-1",
    user_id: "user-1",
    assistant_id: "asst-1",
    title: "Watchlist digest",
    status: "active",
    created_at: "2026-07-22T00:00:00Z",
    updated_at: "2026-07-22T00:00:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
    ...overrides,
  };
}

describe("toChatArchiveRow", () => {
  it("reads pinned/preview/model from the FIRST-CLASS fields, never metadata", () => {
    // The exact drift the desktop binder shipped: first-class fields carry the
    // real values while a stale `metadata` blob carries contradictory ones.
    // Nothing writes `metadata.*`, so the first-class fields must win.
    const row = toChatArchiveRow(
      conversation({
        preview: "hello",
        model: "claude-sonnet-4.5",
        pinned: true,
        metadata: { preview: "WRONG", model: "WRONG", pinned: false },
      }),
    );
    expect(row.preview).toBe("hello");
    expect(row.model).toBe("claude-sonnet-4.5");
    expect(row.pinned).toBe(true);
  });

  it("defaults preview/model to empty and pinned to false when the fields are absent", () => {
    const row = toChatArchiveRow(conversation());
    expect(row.preview).toBe("");
    expect(row.model).toBe("");
    expect(row.pinned).toBe(false);
  });

  it("falls back to 'New chat' for a blank title", () => {
    expect(toChatArchiveRow(conversation({ title: "   " })).title).toBe(
      "New chat",
    );
    expect(toChatArchiveRow(conversation({ title: null })).title).toBe(
      "New chat",
    );
  });

  it("projects the archive status chip (archived wins over run status)", () => {
    expect(toChatArchiveRow(conversation({ status: "archived" })).status).toBe(
      "archived",
    );
    expect(
      toChatArchiveRow(conversation({ archived_at: "2026-07-22T01:00:00Z" }))
        .status,
    ).toBe("archived");
    expect(
      toChatArchiveRow(conversation({ latest_run_status: "running" })).status,
    ).toBe("running");
    expect(
      toChatArchiveRow(
        conversation({ latest_run_status: "waiting_for_approval" }),
      ).status,
    ).toBe("paused");
    expect(
      toChatArchiveRow(conversation({ latest_run_status: null })).status,
    ).toBe("done");
  });
});
