import { describe, expect, it, vi } from "vitest";

import type { LoopbackHandle } from "../auth/loopback-server";

import { CONNECT_SUPERSEDED } from "./channels";
import {
  CONNECT_CANCELLED,
  ConnectorOAuthCoordinator,
  ConnectorOAuthError,
  REDIRECT_TIMED_OUT,
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
  facadeTimeoutMs?: number,
): ConnectorOAuthCoordinator {
  return new ConnectorOAuthCoordinator({
    facadeBaseUrl: "http://127.0.0.1:8200",
    openExternal,
    getBearer: async () => "bearer-abc",
    fetch: fetchImpl,
    loopback,
    ...(facadeTimeoutMs === undefined ? {} : { facadeTimeoutMs }),
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

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------
//
// A Cancel that only resets the renderer is a lie: main would keep the loopback
// armed for its full timeout, so a user who cancelled and then approved anyway
// in the still-open browser tab would find the connector silently connected.
// These pin that cancelling actually reaches main and stops the flow.

describe("ConnectorOAuthCoordinator — cancellation", () => {
  it("rejects the pending connect and frees the port", async () => {
    const { loopback, controls } = fakeLoopback();
    // The browser opens and the user simply never returns to it.
    const openExternal = vi.fn(async () => undefined);
    const coordinator = makeCoordinator(fakeFetch(), loopback, openExternal);

    let cancel: () => void = () => undefined;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });

    // Let the flow reach the point where it is waiting on a redirect.
    await Promise.resolve();
    await Promise.resolve();
    cancel();

    await expect(pending).rejects.toThrow(/connect cancelled/);
    expect(controls.closed).toBe(true);
  });

  it("never opens a consent screen for a connect cancelled while starting", async () => {
    // `start-oauth` is a real network round-trip (over a second against a live
    // provider). Cancelling inside that window must not then open a tab the
    // user could still approve in — that would complete an authorization they
    // explicitly stopped.
    const { loopback } = fakeLoopback();
    const openExternal = vi.fn(async () => undefined);

    // Hold `start-oauth` open so the cancel provably lands DURING it.
    let releaseStart: () => void = () => undefined;
    const startHeld = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    const inner = fakeFetch();
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/desktop/start-oauth")) await startHeld;
      return inner(input, init);
    }) as unknown as typeof fetch;

    const coordinator = makeCoordinator(fetchImpl, loopback, openExternal);

    let cancel: (() => void) | null = null;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });

    // The hook is handed out as soon as the port binds, which is before the
    // held start POST resolves — so waiting for it puts us squarely inside the
    // window this test is about.
    await vi.waitFor(() => expect(cancel).not.toBeNull());
    cancel!();
    releaseStart();

    await expect(pending).rejects.toThrow(/connect cancelled/);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("cancels an MCP-server connect the same way", async () => {
    const { loopback, controls } = fakeLoopback();
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/start")) {
        return new Response(
          JSON.stringify({
            auth_url: `https://mcp.example/authorize?state=${START_STATE}`,
          }),
          { status: 200 },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    }) as unknown as typeof fetch;
    const coordinator = makeCoordinator(
      fetchImpl,
      loopback,
      vi.fn(async () => undefined),
    );

    let cancel: () => void = () => undefined;
    const pending = coordinator.connectMcpServer("seed:linear", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });
    await Promise.resolve();
    await Promise.resolve();
    cancel();

    await expect(pending).rejects.toThrow(/connect cancelled/);
    expect(controls.closed).toBe(true);
  });
});

describe("ConnectorOAuthCoordinator — an unreachable facade fails, it does not spin", () => {
  // The shipped bug: `timeoutMs` bounded only the loopback — the step that
  // cannot hang, because it is a local listener we own. The facade call that
  // runs BEFORE it had no deadline, so a backend that accepted the connection
  // and never answered left `connect` awaiting forever: no browser opened, no
  // error surfaced, and the row sat on "Connecting…". The only escape was the
  // cancel button, which is why the logs recorded `connect cancelled` for a
  // failure that was never a cancellation.
  const hangingFetch = ((_url: string, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      // Exactly what a real unanswered request does: settle only on the
      // caller's signal. With no signal wired, this never settles at all.
      init?.signal?.addEventListener("abort", () => {
        reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      });
    })) as unknown as typeof fetch;

  it("rejects with a named cause rather than awaiting forever", async () => {
    const { loopback } = fakeLoopback();
    const openExternal = vi.fn(async () => {});
    // A real 20ms deadline rather than fake timers: `AbortSignal.timeout` runs
    // on a platform timer that `vi.useFakeTimers` does not intercept, so
    // advancing fake time would leave the request genuinely pending.
    const coordinator = makeCoordinator(
      hangingFetch,
      loopback,
      openExternal,
      20,
    );

    await expect(coordinator.connect("atlassian")).rejects.toThrow(
      /could not reach/i,
    );

    // The browser is never opened for a flow that never started — a consent
    // screen for an unstarted authorization is worse than no screen at all.
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("does not report an unreachable backend as a user cancellation", async () => {
    const { loopback } = fakeLoopback();
    const coordinator = makeCoordinator(
      hangingFetch,
      loopback,
      async () => {},
      20,
    );

    const error = await coordinator
      .connect("atlassian")
      .then(() => null)
      .catch((caught: unknown) => caught as Error);

    // The distinction that matters in a log: this failure is the backend's,
    // not the user's. Reporting it as `connect cancelled` is what sent the
    // original investigation looking at the renderer's cancel wiring.
    expect(error).toBeInstanceOf(ConnectorOAuthError);
    expect(error?.message).not.toContain(CONNECT_CANCELLED);
    expect(error?.message).toMatch(/could not reach/i);
  });
});

// ---------------------------------------------------------------------------
// Supersede vs cancel
// ---------------------------------------------------------------------------
//
// Main holds ONE pending connect slot, so starting a second connect aborts the
// first. Both aborts used to reject with the same `connect cancelled` string,
// and only the renderer that pressed Cancel knew to stay quiet — so the
// SUPERSEDED attempt fell through to the error branch and told the user a
// connector they had just started had failed.
//
// The reason is the whole contract here: only an Error message survives the IPC
// hop, so if the coordinator does not say WHY, nothing downstream can.

describe("ConnectorOAuthCoordinator — supersede carries its own reason", () => {
  it("rejects a superseded connect distinctly from a user cancel", async () => {
    const { loopback } = fakeLoopback();
    const coordinator = makeCoordinator(
      fakeFetch(),
      loopback,
      vi.fn(async () => undefined),
    );

    let cancel: (reason?: "user" | "superseded") => void = () => undefined;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });
    await Promise.resolve();
    await Promise.resolve();
    cancel("superseded");

    await expect(pending).rejects.toThrow(new RegExp(CONNECT_SUPERSEDED));
  });

  it("still says `cancelled` when the user is the one who stopped it", async () => {
    const { loopback } = fakeLoopback();
    const coordinator = makeCoordinator(
      fakeFetch(),
      loopback,
      vi.fn(async () => undefined),
    );

    let cancel: (reason?: "user" | "superseded") => void = () => undefined;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });
    await Promise.resolve();
    await Promise.resolve();
    // No argument: the Cancel button passes none, and the default must not
    // silently become "superseded".
    cancel();

    await expect(pending).rejects.toThrow(new RegExp(CONNECT_CANCELLED));
  });

  it("keeps the reason when the supersede lands BEFORE the browser opens", async () => {
    // `start-oauth` is a real round-trip. A supersede inside that window takes
    // the pre-browser throw path, which is a SECOND place the message is built
    // — it reported a user cancel until the reason was threaded through it too.
    const { loopback } = fakeLoopback();
    const openExternal = vi.fn(async () => undefined);
    let releaseStart: () => void = () => undefined;
    const startHeld = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    const inner = fakeFetch();
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/desktop/start-oauth")) await startHeld;
      return inner(input, init);
    }) as unknown as typeof fetch;
    const coordinator = makeCoordinator(fetchImpl, loopback, openExternal);

    let cancel: ((reason?: "user" | "superseded") => void) | null = null;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });

    // The hook is handed out as soon as the port binds — before the held start
    // POST resolves — which is exactly the window this test is about.
    await vi.waitFor(() => expect(cancel).not.toBeNull());
    cancel!("superseded");
    releaseStart();

    await expect(pending).rejects.toThrow(new RegExp(CONNECT_SUPERSEDED));
    // A consent screen for a flow that was already abandoned would let the user
    // complete an authorization nothing is waiting for.
    expect(openExternal).not.toHaveBeenCalled();
  });
});

// A timeout must describe the user's situation, not a socket.
//
// The loopback listener is shared with app login, so its own message is
// internal by design — `loopback redirect timed out` — and it was reaching the
// screen verbatim after five silent minutes. These pin the translation without
// touching login's wording.

describe("ConnectorOAuthCoordinator — redirect failure copy", () => {
  it("translates the loopback timeout into something a person can act on", async () => {
    const { loopback, controls } = fakeLoopback();
    const coordinator = makeCoordinator(
      fakeFetch(),
      loopback,
      vi.fn(async () => undefined),
    );

    const pending = coordinator.connect("atlassian");
    await vi.waitFor(() => expect(controls.armed).not.toBeNull());
    controls.rejectCode(new Error("loopback redirect timed out"));

    const error: unknown = await pending.then(
      () => new Error("expected the connect to fail"),
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ConnectorOAuthError);
    const message = (error as ConnectorOAuthError).message;
    expect(message).toBe(REDIRECT_TIMED_OUT);
    // The internal wording must not survive to the surface.
    expect(message).not.toContain("loopback");
  });

  it("leaves the cancel contracts untouched — they are IPC vocabulary", async () => {
    // These strings are what `ConnectorService` translates into outcomes, so
    // rewording them here would quietly break that mapping.
    const { loopback } = fakeLoopback();
    const coordinator = makeCoordinator(
      fakeFetch(),
      loopback,
      vi.fn(async () => undefined),
    );

    let cancel: ((reason?: "user" | "superseded") => void) | null = null;
    const pending = coordinator.connect("atlassian", {
      onCancelAvailable: (fn) => {
        cancel = fn;
      },
    });
    await vi.waitFor(() => expect(cancel).not.toBeNull());
    cancel!();

    await expect(pending).rejects.toThrow(new RegExp(CONNECT_CANCELLED));
  });
});
