// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { ConversationId } from "./brands";
import {
  CHAT_ARCHIVE_STATUSES,
  type ChatArchiveRow,
  type ChatsArchive,
} from "./chats";

// Runtime assertions over the Chats archive contract (desktop redesign,
// Phase 4). The status tuple is the runtime SSOT the `ChatArchiveStatus`
// union derives from, so pinning it here is both a value check and a guard
// against silent union drift (FR-4.5/4.6/4.33).

describe("ChatArchiveStatus — archive row status union", () => {
  it("is exactly running / done / paused / archived, in order", () => {
    expect([...CHAT_ARCHIVE_STATUSES]).toEqual([
      "running",
      "done",
      "paused",
      "archived",
    ]);
  });

  it("has no duplicate members", () => {
    expect(new Set(CHAT_ARCHIVE_STATUSES).size).toBe(
      CHAT_ARCHIVE_STATUSES.length,
    );
  });
});

describe("ChatArchiveRow — shape", () => {
  const row: ChatArchiveRow = {
    id: "conv_001" as ConversationId,
    title: "Draft the Q3 board update",
    status: "running",
    preview: "Pulled the latest metrics from the workspace…",
    model: "gpt-4o",
    updated_at: "2026-07-18T12:00:00Z",
    pinned: true,
  };

  it("carries exactly the archive-row fields", () => {
    const expected = [
      "id",
      "model",
      "pinned",
      "preview",
      "status",
      "title",
      "updated_at",
    ];
    expect(Object.keys(row).sort()).toEqual(expected);
  });

  it("uses a status drawn from the status tuple", () => {
    expect(CHAT_ARCHIVE_STATUSES).toContain(row.status);
  });

  it("carries a boolean pinned flag the shell can bucket on", () => {
    expect(typeof row.pinned).toBe("boolean");
  });
});

describe("ChatsArchive — bucketed shape", () => {
  const archive: ChatsArchive = {
    pinned: [],
    recent: [],
    archived: [],
  };

  it("exposes exactly the three ordered sections", () => {
    expect(Object.keys(archive)).toEqual(["pinned", "recent", "archived"]);
  });

  it("buckets are arrays (empty sections are empty arrays, not absent)", () => {
    expect(Array.isArray(archive.pinned)).toBe(true);
    expect(Array.isArray(archive.recent)).toBe(true);
    expect(Array.isArray(archive.archived)).toBe(true);
  });
});
