import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InboxItemId } from "@0x-copilot/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  bulkInbox,
  dismissInbox,
  fetchInbox,
  fetchInboxItem,
  fetchUnreadCount,
  patchInbox,
  replyToInboxItem,
} from "./inboxApi";
import type {
  InboxBodyRef,
  InboxItem,
  InboxItemWithBody,
  InboxUnreadCount,
} from "./_inbox-stub";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function itemFixture(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    id: "inbox_1" as InboxItemId,
    tenant_id: "tenant_1",
    recipient_user_id: "user_test",
    sender: { kind: "agent", agent_id: "agent_1", agent_name: "Atlas" },
    kind: "mention",
    subject: "Doc draft ready",
    preview: "Atlas drafted the Q3 brief and tagged you.",
    body_ref: "body_1" as InboxBodyRef,
    status: "unread",
    priority: "med",
    labels: [],
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function itemWithBody(
  overrides: Partial<InboxItemWithBody> = {},
): InboxItemWithBody {
  return {
    ...itemFixture(),
    body: "# Hello\nFull markdown body.",
    ...overrides,
  };
}

function unreadFixture(): InboxUnreadCount {
  return {
    unread: 3,
    high_priority_unread: 1,
    as_of: "2026-05-18T09:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

describe("fetchInbox", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/inbox with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [itemFixture()], next_cursor: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchInbox(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/inbox");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    // Facade-only: never an absolute backend URL.
    expect(url).not.toContain(":8100");
    expect(url).not.toContain(":8000");
  });

  it("encodes allowlisted filters, sort, search, and cursor", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [], next_cursor: "cursor_2" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchInbox(IDENTITY, {
      filters: {
        status: "unread",
        kind: "approval_request",
        sender_kind: "agent",
        sender_id: "agent_42",
        project_id: "proj_1",
      },
      q: "press",
      sort: "priority:desc",
      after: "cursor_1",
      limit: 50,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("filter%5Bstatus%5D=unread");
    expect(url).toContain("filter%5Bkind%5D=approval_request");
    expect(url).toContain("filter%5Bsender_kind%5D=agent");
    expect(url).toContain("filter%5Bsender_id%5D=agent_42");
    expect(url).toContain("filter%5Bproject_id%5D=proj_1");
    expect(url).toContain("q=press");
    expect(url).toContain("sort=priority%3Adesc");
    expect(url).toContain("after=cursor_1");
    expect(url).toContain("limit=50");
  });

  it("surfaces facade errors as rejected promises", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "tenant lookup failed" }, 503),
      ),
    );

    await expect(fetchInbox(IDENTITY)).rejects.toThrow(/tenant lookup failed/);
  });
});

describe("fetchInboxItem", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs /v1/inbox/<id> and returns the item + body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(itemWithBody()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchInboxItem(IDENTITY, "inbox_1" as InboxItemId);

    expect(res.body).toContain("# Hello");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/inbox/inbox_1");
  });
});

describe("fetchUnreadCount", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs /v1/inbox/unread_count", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(unreadFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchUnreadCount(IDENTITY);

    expect(res.unread).toBe(3);
    expect(res.high_priority_unread).toBe(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/inbox/unread_count");
  });
});

describe("mutations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/inbox/<id> with the partial body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse({ id: "inbox_1" }));
    vi.stubGlobal("fetch", fetchMock);

    await patchInbox(IDENTITY, "inbox_1" as InboxItemId, { status: "read" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/inbox/inbox_1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      status: "read",
    });
  });

  it("DELETEs /v1/inbox/<id> for soft dismiss and resolves on 204", async () => {
    const fetchMock = fetchMockReturning(
      () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      dismissInbox(IDENTITY, "inbox_1" as InboxItemId),
    ).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("POSTs /v1/inbox/bulk-action with action + filter_payload", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ affected: 4, correlation_id: "corr_1" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await bulkInbox(IDENTITY, {
      action: "mark_read",
      filter_payload: { status: "unread", kind: "mention" },
    });

    expect(res.affected).toBe(4);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/inbox/bulk-action");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      action: "mark_read",
      filter_payload: { status: "unread", kind: "mention" },
    });
  });

  it("POSTs /v1/inbox/<id>/reply with the text and returns the routed thread_id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ thread_id: "conv_42", created_new_thread: false }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await replyToInboxItem(IDENTITY, "inbox_1" as InboxItemId, {
      text: "Looks good, ship it.",
    });

    expect(res.thread_id).toBe("conv_42");
    expect(res.created_new_thread).toBe(false);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/inbox/inbox_1/reply");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      text: "Looks good, ship it.",
    });
  });

  it("rejects when the server returns 4xx on patch", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "invalid status transition" }, 422),
      ),
    );

    await expect(
      patchInbox(IDENTITY, "inbox_1" as InboxItemId, { status: "done" }),
    ).rejects.toThrow(/invalid status transition/);
  });
});
