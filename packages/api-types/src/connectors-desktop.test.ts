// @vitest-environment node
import { describe, expect, it } from "vitest";

import type {
  // The SHARED web OAuth shapes — these must stay byte-identical (AC9
  // "Web impact: none"). The desktop transport must NOT have leaked a field
  // into them.
  ConnectorOAuthCallbackRequest,
  StartConnectorOAuthResponse,
} from "./connectors";
import {
  DESKTOP_CONNECTOR_DEEP_LINK_URI,
  DESKTOP_CONNECTOR_LOOPBACK_PATH,
  type DesktopConnectorCallback,
  type DesktopConnectorConnectionResult,
  type DesktopConnectorOAuthCallbackRequest,
  type DesktopStartConnectorOAuthRequest,
  type DesktopStartConnectorOAuthResponse,
} from "./connectors-desktop";
import type { ConnectorSlug } from "./projects";

// ===========================================================================
// Web-compatibility regression (AC9 hard requirement).
//
// The shipped web redirect flow (apps/frontend ConnectorsRoute.tsx /
// connectorsApi.ts) consumes the SHARED web OAuth shapes. AC9 must leave them
// byte-identical: no field added, removed, or made optional/required, and no
// desktop-only field (oauth_session_id, callback, requested_product_scope,
// optional code) folded in. These type-level assertions fail the typecheck if
// that ever regresses.
// ===========================================================================

describe("web OAuth shapes are unchanged by AC9", () => {
  it("StartConnectorOAuthResponse is exactly { authorization_url; state }", () => {
    // A value assignable to the web response with ONLY the two web fields.
    const web: StartConnectorOAuthResponse = {
      authorization_url: "https://auth.example/authorize?state=abc",
      state: "abc",
    };
    // Exhaustive key set — extra keys would make this fail to compile against
    // the exact interface (excess-property check on an object literal).
    const keys = Object.keys(web).sort();
    expect(keys).toEqual(["authorization_url", "state"]);

    // The desktop response is a DIFFERENT type: it is NOT assignable to the
    // web response (it lacks nothing, but proving separation, the web response
    // is not assignable to the desktop one, which requires oauth_session_id).
    // @ts-expect-error — web response has no oauth_session_id / expires_at.
    const _bad: DesktopStartConnectorOAuthResponse = web;
    void _bad;
  });

  it("ConnectorOAuthCallbackRequest is exactly { code; state } (code required)", () => {
    const web: ConnectorOAuthCallbackRequest = { code: "c", state: "s" };
    expect(Object.keys(web).sort()).toEqual(["code", "state"]);

    // `code` is REQUIRED on the web shape — omitting it must not compile.
    // @ts-expect-error — web callback requires `code`.
    const _missing: ConnectorOAuthCallbackRequest = { state: "s" };
    void _missing;
  });
});

// ===========================================================================
// Desktop-only transport compiles and carries its own richer shape.
// ===========================================================================

describe("desktop OAuth transport variant", () => {
  it("loopback callback pins the fixed server-reconstructed path", () => {
    const callback: DesktopConnectorCallback = {
      kind: "desktop_loopback",
      port: 53123,
      path: DESKTOP_CONNECTOR_LOOPBACK_PATH,
    };
    expect(callback.kind).toBe("desktop_loopback");
    expect(DESKTOP_CONNECTOR_LOOPBACK_PATH).toBe("/connectors/oauth/cb");
  });

  it("deep-link callback pins the single registered scheme", () => {
    const callback: DesktopConnectorCallback = {
      kind: "desktop_deep_link",
      uri: DESKTOP_CONNECTOR_DEEP_LINK_URI,
    };
    expect(callback.uri).toBe("enterprise://oauth/callback");
  });

  it("start request carries callback + requested product scope only", () => {
    const req: DesktopStartConnectorOAuthRequest = {
      callback: {
        kind: "desktop_loopback",
        port: 40000,
        path: DESKTOP_CONNECTOR_LOOPBACK_PATH,
      },
      requested_product_scope: "read",
    };
    expect(req.requested_product_scope).toBe("read");
  });

  it("callback request posts code + state (+ session id) — never a token", () => {
    const cb: DesktopConnectorOAuthCallbackRequest = {
      oauth_session_id: "state-256bit",
      state: "state-256bit",
      code: "auth-code-123",
    };
    // No token/secret field exists on the type — assert the key set is exactly
    // the safe transport fields.
    expect(Object.keys(cb).sort()).toEqual([
      "code",
      "oauth_session_id",
      "state",
    ]);
  });

  it("connection result carries only safe metadata (no token/secret keys)", () => {
    const result: DesktopConnectorConnectionResult = {
      server_id: "seed:atlassian",
      connector_slug: "atlassian" as ConnectorSlug,
      display_group: "Atlassian/Jira",
      auth_state: "authenticated",
    };
    const keys = Object.keys(result);
    for (const forbidden of [
      "access_token",
      "refresh_token",
      "token",
      "client_secret",
    ]) {
      expect(keys).not.toContain(forbidden);
    }
  });
});
