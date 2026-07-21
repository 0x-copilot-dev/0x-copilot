import { describe, expect, it } from "vitest";

import { isSafeInAppPath, parseGoogleLinkOutcome } from "./googleLinkLanding";

describe("googleLinkLanding", () => {
  it("parses a successful link outcome with an email upgrade", () => {
    const outcome = parseGoogleLinkOutcome(
      "?link_status=linked&provider=google&email_upgraded=true&return_to=%2Fsettings%23profile",
    );
    expect(outcome.status).toBe("linked");
    expect(outcome.provider).toBe("google");
    expect(outcome.emailUpgraded).toBe(true);
    expect(outcome.returnTo).toBe("/settings#profile");
  });

  it("maps merge_required through", () => {
    expect(parseGoogleLinkOutcome("?link_status=merge_required").status).toBe(
      "merge_required",
    );
  });

  it("falls back to error for an unknown/missing status", () => {
    expect(parseGoogleLinkOutcome("").status).toBe("error");
    expect(parseGoogleLinkOutcome("?link_status=bogus").status).toBe("error");
  });

  it("rejects an unsafe return_to (open-redirect defense)", () => {
    const outcome = parseGoogleLinkOutcome(
      "?link_status=linked&return_to=https%3A%2F%2Fevil.example",
    );
    expect(outcome.returnTo).toBeNull();
  });

  it("isSafeInAppPath only accepts same-origin relative paths", () => {
    expect(isSafeInAppPath("/settings#profile")).toBe(true);
    expect(isSafeInAppPath("//evil.example")).toBe(false);
    expect(isSafeInAppPath("https://evil.example")).toBe(false);
    expect(isSafeInAppPath("relative")).toBe(false);
    expect(isSafeInAppPath(null)).toBe(false);
  });
});
