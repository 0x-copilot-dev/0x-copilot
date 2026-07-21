import { describe, expect, it } from "vitest";

import { parseLinkOutcome, runGoogleLink } from "./google-link";
import { parseWalletLinkResponse, runWalletLink } from "./wallet-link";
import type {
  LoopbackWalletProof,
  LoopbackWalletProofHandle,
} from "./loopback-server";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("parseLinkOutcome (Google link callback → outcome)", () => {
  it("reads link_status + provider + email_upgraded off a 302 Location", async () => {
    const res = new Response(null, {
      status: 302,
      headers: {
        location:
          "/oauth/link/callback?link_status=linked&provider=google&email_upgraded=true",
      },
    });
    const outcome = await parseLinkOutcome(res);
    expect(outcome).toEqual({
      status: "linked",
      provider: "google",
      emailUpgraded: true,
      message: null,
    });
  });

  it("maps merge_required through", async () => {
    const res = new Response(null, {
      status: 302,
      headers: { location: "/oauth/link/callback?link_status=merge_required" },
    });
    expect((await parseLinkOutcome(res)).status).toBe("merge_required");
  });

  it("treats a non-redirect (e.g. an unexpected JSON handoff) as an error", async () => {
    const outcome = await parseLinkOutcome(
      jsonResponse(200, { bearer_token: "x" }),
    );
    expect(outcome.status).toBe("error");
  });
});

describe("parseWalletLinkResponse (link POST → outcome)", () => {
  it("passes through linked / already_linked / merged", async () => {
    expect(
      (await parseWalletLinkResponse(jsonResponse(200, { status: "linked" })))
        .status,
    ).toBe("linked");
    expect(
      (
        await parseWalletLinkResponse(
          jsonResponse(200, { status: "already_linked" }),
        )
      ).status,
    ).toBe("already_linked");
    expect(
      (await parseWalletLinkResponse(jsonResponse(200, { status: "merged" })))
        .status,
    ).toBe("merged");
  });

  it("maps a 409 merge_required to the merge outcome", async () => {
    const res = jsonResponse(409, {
      detail: { code: "merge_required", safe_message: "already owned" },
    });
    const outcome = await parseWalletLinkResponse(res);
    expect(outcome.status).toBe("merge_required");
    expect(outcome.message).toBe("already owned");
  });

  it("maps other failures to error with the safe_message", async () => {
    const res = jsonResponse(409, {
      detail: { code: "last_sign_in_method", safe_message: "guarded" },
    });
    const outcome = await parseWalletLinkResponse(res);
    expect(outcome.status).toBe("error");
    expect(outcome.message).toBe("guarded");
  });
});

describe("runWalletLink (proof relay → authenticated POST)", () => {
  it("opens the page in link mode and POSTs the proof with the bearer", async () => {
    const proof: LoopbackWalletProof = {
      message: "siwe-message",
      signature: "0xsig",
      state: "st",
    };
    const handle: LoopbackWalletProofHandle = {
      port: 51000,
      redirectUri: "http://127.0.0.1:51000/wallet/link/cb",
      proofPromise: Promise.resolve(proof),
      armState: () => {},
      close: () => {},
    };
    let openedUrl = "";
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetchImpl = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return jsonResponse(200, { status: "linked" });
    }) as unknown as typeof fetch;

    const result = await runWalletLink({
      facadeBaseUrl: "http://127.0.0.1:8200",
      bearer: "brr_caller",
      confirmMerge: false,
      openExternal: async (url) => {
        openedUrl = url;
      },
      fetch: fetchImpl,
      loopback: async () => handle,
      generateState: () => "st",
    });

    expect(result.status).toBe("linked");
    // The wallet page is opened in LINK mode with the loopback handoff.
    expect(openedUrl).toContain("/wallet.html?");
    expect(openedUrl).toContain("mode=link");
    // The proof is POSTed to the link endpoint with the caller's bearer.
    const post = calls.find((c) => c.url.endsWith("/v1/me/identities/wallet"));
    expect(post).toBeDefined();
    const headers = post!.init?.headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer brr_caller");
    expect(JSON.parse(post!.init?.body as string)).toEqual({
      message: "siwe-message",
      signature: "0xsig",
      confirm_merge: false,
    });
  });
});

describe("runGoogleLink (authenticated start → callback redirect)", () => {
  it("posts the start with the bearer and reads the callback outcome", async () => {
    const handle = {
      port: 51001,
      redirectUri: "http://127.0.0.1:51001/oidc/link/cb",
      codePromise: Promise.resolve({ code: "code-1", state: "st" }),
      armState: () => {},
      close: () => {},
    };
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetchImpl = (async (url: string | URL, init?: RequestInit) => {
      const u = String(url);
      calls.push({ url: u, init });
      if (u.endsWith("/v1/me/identities/google/link/start")) {
        return jsonResponse(200, {
          auth_url: "https://accounts.google/x",
          state: "st",
        });
      }
      // The callback returns the facade's link redirect.
      return new Response(null, {
        status: 302,
        headers: {
          location: "/oauth/link/callback?link_status=linked&provider=google",
        },
      });
    }) as unknown as typeof fetch;

    const result = await runGoogleLink({
      facadeBaseUrl: "http://127.0.0.1:8200",
      bearer: "brr_caller",
      openExternal: async () => {},
      fetch: fetchImpl,
      loopback: async () => handle,
    });

    expect(result.status).toBe("linked");
    const start = calls.find((c) =>
      c.url.endsWith("/v1/me/identities/google/link/start"),
    );
    expect((start!.init?.headers as Record<string, string>).authorization).toBe(
      "Bearer brr_caller",
    );
  });
});
