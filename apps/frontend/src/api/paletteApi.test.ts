import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConversationId,
  PaletteSearchResponse,
} from "@enterprise-search/api-types";

import { configureAuthBearerProvider } from "./http";
import { searchPalette } from "./paletteApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function searchFixture(): PaletteSearchResponse {
  return {
    hits: [
      {
        id: "hit_1",
        kind: "navigation",
        title: "Open Inbox",
        route: "/inbox",
        score: 0.95,
      },
    ],
    took_ms: 17,
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

describe("searchPalette", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/palette/search with q + identity", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(searchFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await searchPalette(IDENTITY, { q: "inbox" });

    expect(res.hits).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/palette/search");
    expect(url).toContain("q=inbox");
    expect(url).toContain("org_id=org_test");
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("threads context fields into context[...] keys", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(searchFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await searchPalette(IDENTITY, {
      q: "make routine",
      limit: 25,
      context: {
        current_route: "/inbox",
        current_chat_id: "conv_42" as ConversationId,
      },
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(
      encodeURIComponent("context[current_route]") +
        "=" +
        encodeURIComponent("/inbox"),
    );
    expect(url).toContain(
      encodeURIComponent("context[current_chat_id]") + "=conv_42",
    );
    expect(url).toContain("limit=25");
  });

  it("propagates 503 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "palette unavailable" }, 503),
      ),
    );

    await expect(searchPalette(IDENTITY, { q: "x" })).rejects.toThrow(
      "palette unavailable",
    );
  });
});
