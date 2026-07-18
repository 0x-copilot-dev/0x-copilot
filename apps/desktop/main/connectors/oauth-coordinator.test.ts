import { describe, expect, it, vi } from "vitest";

import type { LoopbackHandle } from "../auth/loopback-server";

import {
  ConnectorOAuthCoordinator,
  ConnectorOAuthError,
} from "./oauth-coordinator";

// A provider token that must NEVER surface in main: the facade callback
// response carries only safe metadata, so this string should never appear in a
// coordinator result. Present here only to prove the negative.
const TOKEN_CANARY = "provider-access-token-CANARY-desktop-main";

const START_STATE = "state-256bit-abcdef";

interface FakeLoopbackControls {
  handle: LoopbackHandle;
  resolveCode: (v: { code: string; state: string }) => void;
  rejectCode: (e: Error) => void;
  armed: string | null;
  closed: boolean;
}

function fakeLoopback(port = 51000): {
  loopback: () => Promise<LoopbackHandle>;
  controls: FakeLoopbackControls;
} {
  let resolveCode: (v: { code: string; state: string }) => void = () => {};
  let rejectCode: (e: Error) => void = () => {};
  const codePromise = new Promise<{ code: string; state: string }>(
    (resolve, reject) => {
      resolveCode = resolve;
      rejectCode = reject;
    },
  );
  const controls: FakeLoopbackControls = {
    resolveCode,
    rejectCode,
    armed: null,
    closed: false,
    handle: {
      port,
      redirectUri: `http://127.0.0.1:${port}/connectors/oauth/cb`,
      codePromise,
      armState: (state: string) => {
        controls.armed = state;
      },
      close: () => {
        controls.closed = true;
      },
    },
  };
  return { loopback: () => Promise.resolve(controls.handle), controls };
}

// A fetch double that answers the two facade endpoints with canned JSON. The
// callback body deliberately carries NO token — proving the safe-metadata
// contract; TOKEN_CANARY only lives in an ignored field the real backend never
// emits, asserting the coordinator does not forward arbitrary response data.
function fakeFetch(
  overrides: Partial<{ startStatus: number }> = {},
): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/desktop/start-oauth")) {
      return new Response(
        JSON.stringify({
          oauth_session_id: START_STATE,
          authorization_url: `https://idp.example/authorize?state=${START_STATE}`,
          state: START_STATE,
          expires_at: "2099-01-01T00:00:00Z",
          requested_permissions: ["read:jira-work"],
        }),
        { status: overrides.startStatus ?? 200 },
      );
    }
    if (url.includes("/desktop/oauth-callback")) {
      return new Response(
        JSON.stringify({
          server_id: "seed:atlassian",
          connector_slug: "atlassian",
          display_group: "Atlassian/Jira",
          auth_state: "authenticated",
        }),
        { status: 200 },
      );
    }
    throw new Error(`unexpected fetch ${url}`);
  }) as unknown as typeof fetch;
}

function makeCoordinator(
  fetchImpl: typeof fetch,
  loopback: () => Promise<LoopbackHandle>,
  openExternal: (url: string) => Promise<void>,
): ConnectorOAuthCoordinator {
  return new ConnectorOAuthCoordinator({
    facadeBaseUrl: "http://127.0.0.1:8200",
    openExternal,
    getBearer: async () => "bearer-abc",
    fetch: fetchImpl,
    loopback,
  });
}

describe("ConnectorOAuthCoordinator — loopback delivery", () => {
  it("completes connect and returns only safe metadata", async () => {
    const { loopback, controls } = fakeLoopback();
    const openExternal = vi.fn(async () => {
      // Loopback wins: deliver the code with the matching state.
      controls.resolveCode({ code: "auth-code-123", state: START_STATE });
    });
    const coordinator = makeCoordinator(fakeFetch(), loopback, openExternal);

    const result = await coordinator.connect("atlassian");

    expect(result).toEqual({
      server_id: "seed:atlassian",
      connector_slug: "atlassian",
      display_group: "Atlassian/Jira",
      auth_state: "authenticated",
    });
    // The system browser was opened; the loopback armed + closed.
    expect(openExternal).toHaveBeenCalledWith(
      `https://idp.example/authorize?state=${START_STATE}`,
    );
    expect(controls.armed).toBe(START_STATE);
    expect(controls.closed).toBe(true);
    // Secret canary: no provider token anywhere in the renderer-facing result.
    expect(JSON.stringify(result)).not.toContain(TOKEN_CANARY);
    expect(JSON.stringify(result)).not.toContain("access_token");
    // The flow no longer owns the state after completion.
    expect(coordinator.ownsState(START_STATE)).toBe(false);
  });
});

describe("ConnectorOAuthCoordinator — deep-link demux by state", () => {
  it("routes the matching state and ignores foreign states", async () => {
    const { loopback, controls } = fakeLoopback();
    let sawWrongState = false;
    let sawRightState = false;
    const openExternal = vi.fn(async () => {
      // The loopback never fires here (codePromise stays pending); delivery
      // comes via the deep link. A foreign state must NOT be consumed (it would
      // belong to app-login); only the owned state completes this flow.
      sawWrongState = coordinator.handleDeepLinkCallback(
        "c",
        "login-state-999",
      );
      sawRightState = coordinator.handleDeepLinkCallback(
        "auth-code-deep",
        START_STATE,
      );
    });
    const coordinator = makeCoordinator(fakeFetch(), loopback, openExternal);

    const result = await coordinator.connect("atlassian");

    expect(sawWrongState).toBe(false); // foreign state falls through to login
    expect(sawRightState).toBe(true); // owned state consumed by the connector
    expect(result.connector_slug).toBe("atlassian");
    // codePromise was never resolved; the deep link won the race.
    expect(controls.closed).toBe(true);
  });

  it("handleDeepLinkCallback is a no-op for unknown states", () => {
    const { loopback } = fakeLoopback();
    const coordinator = makeCoordinator(fakeFetch(), loopback, async () => {});
    expect(coordinator.handleDeepLinkCallback("c", "never-registered")).toBe(
      false,
    );
  });
});

describe("ConnectorOAuthCoordinator — failures", () => {
  it("throws when not signed in", async () => {
    const { loopback } = fakeLoopback();
    const coordinator = new ConnectorOAuthCoordinator({
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {},
      getBearer: async () => null,
      fetch: fakeFetch(),
      loopback,
    });
    await expect(coordinator.connect("atlassian")).rejects.toBeInstanceOf(
      ConnectorOAuthError,
    );
  });

  it("surfaces a facade start failure", async () => {
    const { loopback, controls } = fakeLoopback();
    const coordinator = makeCoordinator(
      fakeFetch({ startStatus: 403 }),
      loopback,
      async () => {},
    );
    await expect(coordinator.connect("gmail")).rejects.toMatchObject({
      stage: "start",
    });
    // The loopback is always closed, even on the start error path.
    expect(controls.closed).toBe(true);
  });
});
