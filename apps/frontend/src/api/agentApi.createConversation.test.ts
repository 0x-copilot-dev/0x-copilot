// Phase 6.5 §4 (P6.5-C1) — wire-shape tests for `createConversation`.
//
// These pin the omit-on-null contract: when the resolved project_id is
// `null` / `undefined` the wire payload omits `project_id` entirely so
// the Phase 1 request shape is preserved bit-for-bit (defence against
// the backend's `extra="forbid"` policy on `CreateConversationRequest`
// until the P6.5-A2 schema bump lands).
//
// When the resolver supplies a concrete ProjectId, it rides through as
// `project_id` on the wire body.

import type { Conversation, ProjectId } from "@0x-copilot/api-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createConversation } from "./agentApi";

const IDENTITY = { orgId: "org_acme", userId: "user_sarah" };

const STUB_CONVERSATION: Conversation = {
  conversation_id: "conv_1",
  org_id: "org_acme",
  user_id: "user_sarah",
  assistant_id: "asst_default",
  title: "New chat",
  status: "active",
  created_at: "2026-05-17T00:00:00Z",
  updated_at: "2026-05-17T00:00:00Z",
  archived_at: null,
  metadata: {},
  schema_version: 1,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

async function lastPostBody(
  fetchMock: ReturnType<typeof vi.fn>,
): Promise<Record<string, unknown>> {
  const calls = fetchMock.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  const lastCall = calls[calls.length - 1];
  // WebTransport calls fetch(url, init) — init.body is the JSON string.
  const init = lastCall[1] as RequestInit | undefined;
  expect(init).toBeDefined();
  expect(init?.method).toBe("POST");
  const body = init?.body;
  if (typeof body === "string") {
    return JSON.parse(body) as Record<string, unknown>;
  }
  throw new Error("expected string body");
}

describe("createConversation — project_id wire shape", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("omits project_id when projectId is undefined (default call)", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(STUB_CONVERSATION));
    vi.stubGlobal("fetch", fetchMock);

    await createConversation(IDENTITY, { title: "Hello" });

    const body = await lastPostBody(fetchMock);
    expect("project_id" in body).toBe(false);
    expect(body.org_id).toBe("org_acme");
    expect(body.user_id).toBe("user_sarah");
    expect(body.title).toBe("Hello");
  });

  it("omits project_id when projectId is null (route /chats, no override)", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(STUB_CONVERSATION));
    vi.stubGlobal("fetch", fetchMock);

    await createConversation(IDENTITY, { title: "Hello", projectId: null });

    const body = await lastPostBody(fetchMock);
    expect("project_id" in body).toBe(false);
  });

  it("sends project_id when a concrete ProjectId is supplied", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(STUB_CONVERSATION));
    vi.stubGlobal("fetch", fetchMock);

    const projectId = "proj_acme_q3" as ProjectId;
    await createConversation(IDENTITY, {
      title: "Q3 planning",
      projectId,
    });

    const body = await lastPostBody(fetchMock);
    expect(body.project_id).toBe("proj_acme_q3");
  });
});
