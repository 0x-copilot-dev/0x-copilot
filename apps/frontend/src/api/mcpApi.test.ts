import { afterEach, describe, expect, it, vi } from "vitest";
import { configureAuthBearerProvider } from "./http";
import { completeMcpOAuth } from "./mcpApi";

describe("completeMcpOAuth", () => {
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("sends the active bearer token to the protected OAuth callback", async () => {
    configureAuthBearerProvider(() => "dev-token");
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => {
        return new Response(
          JSON.stringify({
            server_id: "linear",
            name: "linear",
            display_name: "Linear",
            url: "https://linear.example/mcp",
            auth_state: "authenticated",
            enabled: true,
          }),
          { status: 200 },
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await completeMcpOAuth("state-123", "oauth-code");

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/v1/mcp/oauth/callback?state=state-123&code=oauth-code",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      headers: expect.objectContaining({
        authorization: "Bearer dev-token",
      }),
    });
  });
});
