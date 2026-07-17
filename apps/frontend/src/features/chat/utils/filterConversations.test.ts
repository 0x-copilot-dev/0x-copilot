import type { Conversation } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";
import { filterConversations } from "./filterConversations";

function row(
  id: string,
  title: string,
  folder: string | null = null,
): Conversation {
  return {
    conversation_id: id,
    org_id: "org_acme",
    user_id: "usr_sarah",
    assistant_id: "atlas",
    title,
    status: "active",
    created_at: "2026-05-05T00:00:00Z",
    updated_at: "2026-05-05T00:00:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
    folder,
    deleted_at: null,
    parent_conversation_id: null,
  };
}

const ROWS = [
  row("a", "FY26 Q1 launch announcement"),
  row("b", "Brand voice guidelines", "Personal"),
  row("c", "Pull Q4 close numbers"),
  row("d", null as unknown as string),
];

describe("filterConversations", () => {
  it("returns all rows when query is empty / whitespace", () => {
    expect(filterConversations(ROWS, "").length).toBe(ROWS.length);
    expect(filterConversations(ROWS, "   ").length).toBe(ROWS.length);
  });

  it("matches title case-insensitively", () => {
    expect(
      filterConversations(ROWS, "LAUNCH").map((c) => c.conversation_id),
    ).toEqual(["a"]);
  });

  it("matches folder case-insensitively", () => {
    expect(
      filterConversations(ROWS, "personal").map((c) => c.conversation_id),
    ).toEqual(["b"]);
  });

  it("returns no rows when nothing matches", () => {
    expect(filterConversations(ROWS, "nope")).toEqual([]);
  });

  it("ignores rows with null titles unless folder matches", () => {
    expect(filterConversations(ROWS, "untitled")).toEqual([]);
  });
});
