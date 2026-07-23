// Web `ProjectDataPort` (PRD-07) — the twin of the desktop DoD-15 test.
//
// Asserts the web binding issues the project-filtered facade GETs and maps rows
// through the SHARED `toChatArchiveRow` / `LibraryFile → ProjectFileRow` shapes,
// and that each method resolves a `SectionResult` (never throws).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "../../api/http";
import { createWebProjectDataPort } from "./ProjectDataPort";
import type {
  Conversation,
  ConversationListResponse,
  LibraryFile,
  LibraryListResponse,
  ProjectId,
} from "@0x-copilot/api-types";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

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

function conversationFixture(over: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: "conv_1",
    org_id: "org_test",
    user_id: "user_test",
    assistant_id: "asst_1",
    title: "Renewal thread",
    status: "active",
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-21T09:00:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
    latest_run_status: "running",
    model: "gpt-4o",
    preview: "Let's map the accounts",
    project_id: "p1",
    ...over,
  };
}

function libraryFileFixture(over: Partial<LibraryFile> = {}): LibraryFile {
  return {
    id: "file_1",
    tenant_id: "tenant_1",
    owner_user_id: "user_test",
    project_id: "p1",
    kind: "file",
    file_kind: "pdf",
    name: "Deck.pdf",
    mime: "application/pdf",
    size_bytes: 1536,
    blob_ref: "blob://x",
    thumbnail_blob_ref: null,
    source: { kind: "user_upload", uploaded_by: "user_test" },
    tags: [],
    index_status: "ready",
    index_error: null,
    checksum_sha256: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    last_accessed_at: null,
    ...over,
  } as unknown as LibraryFile;
}

describe("createWebProjectDataPort.listProjectChats", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/agent/conversations with filter[project_id] + include_archived and maps via toChatArchiveRow", async () => {
    const body: ConversationListResponse = {
      conversations: [conversationFixture()],
      next_cursor: null,
      has_more: false,
    } as unknown as ConversationListResponse;
    const fetchMock = fetchMockReturning(() => jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createWebProjectDataPort(IDENTITY).listProjectChats(
      "p1" as ProjectId,
    );

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/agent/conversations");
    expect(url).toContain(encodeURIComponent("filter[project_id]") + "=p1");
    expect(url).toContain("include_archived=true");
    // Never the direct backend/ai-backend ports — facade only.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);

    expect(result.status).toBe("ok");
    // `model` + `status` prove the shared projector ran (not a local shape).
    expect(result.data?.[0]?.model).toBe("gpt-4o");
    expect(result.data?.[0]?.status).toBe("running");
    expect(result.data?.[0]?.title).toBe("Renewal thread");
  });

  it("drops soft-deleted conversations", async () => {
    const body = {
      conversations: [
        conversationFixture({ conversation_id: "keep" }),
        conversationFixture({
          conversation_id: "gone",
          deleted_at: "2026-07-22T00:00:00Z",
        }),
      ],
      next_cursor: null,
      has_more: false,
    } as unknown as ConversationListResponse;
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse(body)),
    );

    const result = await createWebProjectDataPort(IDENTITY).listProjectChats(
      "p1" as ProjectId,
    );
    expect(result.data).toHaveLength(1);
    expect(result.data?.[0]?.id).toBe("keep");
  });

  it("resolves an error SectionResult (never throws) on a failed request", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "boom" }, 500)),
    );
    const result = await createWebProjectDataPort(IDENTITY).listProjectChats(
      "p1" as ProjectId,
    );
    expect(result.status).toBe("error");
  });
});

describe("createWebProjectDataPort.listProjectFiles", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/library with filter[project_id] + filter[kind]=file and maps LibraryFile → ProjectFileRow", async () => {
    const body: LibraryListResponse = {
      items: [libraryFileFixture()],
      next_cursor: null,
      counts_by_kind: { file: 1, page: 0, dataset: 0 },
    } as unknown as LibraryListResponse;
    const fetchMock = fetchMockReturning(() => jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createWebProjectDataPort(IDENTITY).listProjectFiles(
      "p1" as ProjectId,
    );

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/library");
    expect(url).toContain(encodeURIComponent("filter[project_id]") + "=p1");
    expect(url).toContain(encodeURIComponent("filter[kind]") + "=file");

    expect(result.status).toBe("ok");
    expect(result.data?.[0]).toMatchObject({
      id: "file_1",
      name: "Deck.pdf",
      fileKind: "pdf",
      updatedAt: "2026-07-20T09:00:00Z",
      sizeLabel: "1.5 KB",
    });
  });

  it("resolves an error SectionResult (never throws) on a failed request", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "nope" }, 403)),
    );
    const result = await createWebProjectDataPort(IDENTITY).listProjectFiles(
      "p1" as ProjectId,
    );
    expect(result.status).toBe("error");
  });
});
