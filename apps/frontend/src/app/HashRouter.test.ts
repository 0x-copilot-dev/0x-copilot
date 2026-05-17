import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_SETTINGS_SECTION } from "../features/settings/sections";

import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";

function setLocation(path: string, hash: string): void {
  // jsdom mutates location through history. replaceState avoids triggering
  // a real navigation while seeding the URL the router will read.
  window.history.replaceState(null, "", `${path}${hash}`);
}

beforeEach(() => {
  setLocation("/", "");
});

describe("migrateLegacySettingsPath", () => {
  it("rewrites /settings/<section> to /settings#<section>", () => {
    setLocation("/settings/connectors", "");
    const result = migrateLegacySettingsPath();
    expect(result).toBe("connectors");
    expect(window.location.pathname).toBe("/settings");
    expect(window.location.hash).toBe("#connectors");
  });

  it("collapses the default section to a hash-less URL", () => {
    setLocation(`/settings/${DEFAULT_SETTINGS_SECTION}`, "");
    const result = migrateLegacySettingsPath();
    expect(result).toBe(DEFAULT_SETTINGS_SECTION);
    expect(window.location.pathname).toBe("/settings");
    expect(window.location.hash).toBe("");
  });

  it("returns null for non-Settings paths", () => {
    setLocation("/", "");
    expect(migrateLegacySettingsPath()).toBe(null);
  });

  it("falls back to default for unknown legacy slugs", () => {
    setLocation("/settings/not-a-real-section", "");
    const result = migrateLegacySettingsPath();
    expect(result).toBe(DEFAULT_SETTINGS_SECTION);
  });
});

describe("HashRouter", () => {
  it("derives current() from the URL", () => {
    setLocation("/", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({ screen: "chat" });

    setLocation("/settings", "#connectors");
    expect(router.current()).toEqual({
      screen: "settings",
      section: "connectors",
    });

    setLocation("/share/tok_123", "");
    expect(router.current()).toEqual({ screen: "share", token: "tok_123" });
  });

  it("falls back to default settings section when no hash is present", () => {
    setLocation("/settings", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "settings",
      section: DEFAULT_SETTINGS_SECTION,
    });
  });

  it("falls back to default for unknown settings hashes", () => {
    setLocation("/settings", "#not-a-real-section");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "settings",
      section: DEFAULT_SETTINGS_SECTION,
    });
  });

  it("navigate() pushes a new history entry and notifies subscribers", () => {
    setLocation("/", "");
    const router = new HashRouter();
    const seen: Array<unknown> = [];
    const unsubscribe = router.subscribe((route) => seen.push(route));

    router.navigate({ screen: "settings", section: "connectors" });

    expect(window.location.pathname).toBe("/settings");
    expect(window.location.hash).toBe("#connectors");
    expect(seen).toEqual([{ screen: "settings", section: "connectors" }]);

    unsubscribe();
  });

  it("navigate({ replace: true }) does not grow history length", () => {
    setLocation("/", "");
    const router = new HashRouter();
    const before = window.history.length;

    router.navigate(
      { screen: "settings", section: "connectors" },
      { replace: true },
    );

    expect(window.history.length).toBe(before);
    expect(window.location.hash).toBe("#connectors");
  });

  it("subscribers fire on browser hashchange events", () => {
    setLocation("/settings", "#profile");
    const router = new HashRouter();
    const seen: Array<unknown> = [];
    const unsubscribe = router.subscribe((route) => seen.push(route));

    setLocation("/settings", "#model-and-behavior");
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    expect(seen).toEqual([
      { screen: "settings", section: "model-and-behavior" },
    ]);

    unsubscribe();
  });

  it("detaches window listeners once the last subscriber unsubscribes", () => {
    setLocation("/settings", "#profile");
    const router = new HashRouter();
    const seen: Array<unknown> = [];
    const unsubscribe = router.subscribe((route) => seen.push(route));

    unsubscribe();

    setLocation("/settings", "#connectors");
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    expect(seen).toEqual([]);
  });

  it("collapses the default settings section to a hash-less URL", () => {
    setLocation("/", "");
    const router = new HashRouter();

    router.navigate({
      screen: "settings",
      section: DEFAULT_SETTINGS_SECTION,
    });

    expect(window.location.pathname).toBe("/settings");
    expect(window.location.hash).toBe("");
  });

  // Phase 7C — tier-2 adapter review (admin-only) routing.
  it("derives the admin adapter-review queue route from the URL", () => {
    setLocation("/admin/adapter-review", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "admin-adapter-review-queue",
    });
  });

  it("derives the admin adapter-review detail route from the URL", () => {
    setLocation("/admin/adapter-review/cand_42", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "admin-adapter-review-detail",
      candidateId: "cand_42",
    });
  });

  it("decodes the candidate id from the path", () => {
    setLocation("/admin/adapter-review/cand%2Fwith-slash", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "admin-adapter-review-detail",
      candidateId: "cand/with-slash",
    });
  });

  it("navigate to admin queue updates the URL", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({ screen: "admin-adapter-review-queue" });
    expect(window.location.pathname).toBe("/admin/adapter-review");
  });

  it("navigate to admin detail encodes the candidate id", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({
      screen: "admin-adapter-review-detail",
      candidateId: "cand/with-slash",
    });
    expect(window.location.pathname).toBe(
      "/admin/adapter-review/cand%2Fwith-slash",
    );
  });
});
