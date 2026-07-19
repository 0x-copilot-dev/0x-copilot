import { describe, expect, it } from "vitest";

import {
  applyBundledGoogleOAuth,
  BUNDLED_GOOGLE_OAUTH_FILE,
} from "./google-oauth-default";

const APP = "/app/desktop";
const BUNDLED_PATH = `${APP}/${BUNDLED_GOOGLE_OAUTH_FILE}`;

function reader(files: Record<string, string>): (path: string) => string {
  return (path: string) => {
    const body = files[path];
    if (body === undefined) throw new Error(`ENOENT: ${path}`);
    return body;
  };
}

describe("applyBundledGoogleOAuth", () => {
  it("seeds client id + secret from the bundled file when env is unset", () => {
    const env: Record<string, string | undefined> = {};
    const result = applyBundledGoogleOAuth(env, APP, {
      readFile: reader({
        [BUNDLED_PATH]: JSON.stringify({
          client_id: "bundled.apps.googleusercontent.com",
          client_secret: "GOCSPX-bundled",
        }),
      }),
    });

    expect(result.applied).toBe("bundled");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe(
      "bundled.apps.googleusercontent.com",
    );
    expect(env.GOOGLE_OAUTH_CLIENT_SECRET).toBe("GOCSPX-bundled");
  });

  it("lets an operator env client id win over the bundled default", () => {
    const env: Record<string, string | undefined> = {
      GOOGLE_OAUTH_CLIENT_ID: "operator.apps.googleusercontent.com",
    };
    const result = applyBundledGoogleOAuth(env, APP, {
      readFile: reader({
        [BUNDLED_PATH]: JSON.stringify({
          client_id: "bundled",
          client_secret: "x",
        }),
      }),
    });

    expect(result.applied).toBe("env");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe(
      "operator.apps.googleusercontent.com",
    );
    // Must not graft the bundled secret onto the operator's client.
    expect(env.GOOGLE_OAUTH_CLIENT_SECRET).toBeUndefined();
  });

  it("treats a blank operator client id as unset", () => {
    const env: Record<string, string | undefined> = {
      GOOGLE_OAUTH_CLIENT_ID: "   ",
    };
    const result = applyBundledGoogleOAuth(env, APP, {
      readFile: reader({
        [BUNDLED_PATH]: JSON.stringify({ client_id: "bundled-id" }),
      }),
    });

    expect(result.applied).toBe("bundled");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe("bundled-id");
  });

  it("seeds only the client id when the bundled file has no secret", () => {
    const env: Record<string, string | undefined> = {};
    const result = applyBundledGoogleOAuth(env, APP, {
      readFile: reader({
        [BUNDLED_PATH]: JSON.stringify({ client_id: "desktop-id" }),
      }),
    });

    expect(result.applied).toBe("bundled");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe("desktop-id");
    expect(env.GOOGLE_OAUTH_CLIENT_SECRET).toBeUndefined();
  });

  it("stays 'none' (Google disabled) when no bundled file exists", () => {
    const env: Record<string, string | undefined> = {};
    const result = applyBundledGoogleOAuth(env, APP, {
      readFile: reader({}),
    });

    expect(result.applied).toBe("none");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBeUndefined();
  });

  it("stays 'none' on malformed JSON or an empty client id", () => {
    const env: Record<string, string | undefined> = {};
    expect(
      applyBundledGoogleOAuth(env, APP, {
        readFile: reader({ [BUNDLED_PATH]: "{ not json" }),
      }).applied,
    ).toBe("none");
    expect(
      applyBundledGoogleOAuth(env, APP, {
        readFile: reader({ [BUNDLED_PATH]: JSON.stringify({ client_id: "" }) }),
      }).applied,
    ).toBe("none");
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBeUndefined();
  });
});
