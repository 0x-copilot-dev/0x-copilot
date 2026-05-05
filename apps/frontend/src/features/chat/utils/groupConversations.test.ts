import type { Conversation } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import { groupConversations } from "./groupConversations";

function fixture(
  overrides: Partial<Conversation> & { id: string; updated: string },
): Conversation {
  return {
    conversation_id: overrides.id,
    org_id: "org_acme",
    user_id: "usr_sarah",
    assistant_id: "atlas",
    title: overrides.title ?? `Chat ${overrides.id}`,
    status: "active",
    created_at: overrides.updated,
    updated_at: overrides.updated,
    archived_at: null,
    metadata: {},
    schema_version: 1,
    folder: overrides.folder ?? null,
    deleted_at: overrides.deleted_at ?? null,
    parent_conversation_id: null,
  };
}

describe("groupConversations", () => {
  it("buckets rows by Today / Yesterday / Earlier in user-local time", () => {
    const now = new Date("2026-05-05T15:00:00Z");
    const yesterday = new Date(now.getTime() - 86_400_000);
    const earlier = new Date(now.getTime() - 5 * 86_400_000);
    const groups = groupConversations(
      [
        fixture({ id: "a", updated: now.toISOString() }),
        fixture({ id: "b", updated: yesterday.toISOString() }),
        fixture({ id: "c", updated: earlier.toISOString() }),
      ],
      now,
    );
    expect(groups.map((g) => g.id)).toEqual(["today", "yesterday", "earlier"]);
    expect(groups[0].conversations[0].conversation_id).toBe("a");
    expect(groups[1].conversations[0].conversation_id).toBe("b");
    expect(groups[2].conversations[0].conversation_id).toBe("c");
  });

  it("orders within a bucket by updated_at descending", () => {
    const now = new Date("2026-05-05T15:00:00Z");
    const groups = groupConversations(
      [
        fixture({ id: "older", updated: "2026-05-05T08:00:00Z" }),
        fixture({ id: "newer", updated: "2026-05-05T14:00:00Z" }),
      ],
      now,
    );
    expect(groups[0].conversations.map((c) => c.conversation_id)).toEqual([
      "newer",
      "older",
    ]);
  });

  it("subgroups Earlier rows by folder, leaving folderless ones in plain Earlier", () => {
    const now = new Date("2026-05-05T15:00:00Z");
    const earlier = new Date(now.getTime() - 5 * 86_400_000).toISOString();
    const groups = groupConversations(
      [
        fixture({ id: "a", updated: earlier, folder: "Launches" }),
        fixture({ id: "b", updated: earlier, folder: "Personal" }),
        fixture({ id: "c", updated: earlier, folder: null }),
        fixture({ id: "d", updated: earlier, folder: "Launches" }),
      ],
      now,
    );
    expect(groups.map((g) => g.label)).toEqual([
      "Earlier · Launches",
      "Earlier · Personal",
      "Earlier",
    ]);
    expect(
      groups[0].conversations.map((c) => c.conversation_id).sort(),
    ).toEqual(["a", "d"]);
  });

  it("excludes soft-deleted conversations", () => {
    const now = new Date("2026-05-05T15:00:00Z");
    const groups = groupConversations(
      [
        fixture({ id: "alive", updated: now.toISOString() }),
        fixture({
          id: "ghost",
          updated: now.toISOString(),
          deleted_at: now.toISOString(),
        }),
      ],
      now,
    );
    expect(groups[0].conversations.map((c) => c.conversation_id)).toEqual([
      "alive",
    ]);
  });

  it("returns empty array when there are no conversations", () => {
    expect(groupConversations([], new Date())).toEqual([]);
  });
});
