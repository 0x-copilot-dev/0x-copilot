import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_SETTINGS_SECTION } from "../features/settings/sections";

import { HashRouter, migrateLegacySettingsPath } from "./HashRouter";
import { ROOT_DESTINATION } from "./routes";

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
    expect(router.current()).toEqual({
      screen: "chat",
      destination: ROOT_DESTINATION,
    });

    setLocation("/settings", "#connectors");
    expect(router.current()).toEqual({
      screen: "settings",
      section: "connectors",
    });

    setLocation("/share/tok_123", "");
    expect(router.current()).toEqual({ screen: "share", token: "tok_123" });
  });

  it("maps /<destination> to the matching chat route for each known slug", () => {
    setLocation("/", "");
    const router = new HashRouter();
    const cases: ReadonlyArray<readonly [string, string]> = [
      ["/home", "home"],
      ["/chats", "chats"],
      ["/inbox", "inbox"],
      ["/todos", "todos"],
      ["/projects", "projects"],
      ["/library", "library"],
      ["/agents", "agents"],
      ["/tools", "tools"],
      ["/connectors", "connectors"],
      ["/team", "team"],
      ["/memory", "memory"],
    ];
    for (const [path, destination] of cases) {
      setLocation(path, "");
      // P12-C — destination routes now carry an optional `subPath` field
      // for in-destination URL slugs (`/team/<id>`, `/memory/<id>`,
      // `/memory/proposals`). Bare destinations parse to `subPath: null`.
      expect(router.current()).toEqual({
        screen: "chat",
        destination,
        subPath: null,
      });
    }
  });

  it("falls back to the root destination for unknown top-level paths", () => {
    setLocation("/not-a-real-destination", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "chat",
      destination: ROOT_DESTINATION,
    });
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

  it("navigate({ screen: 'chat', destination }) writes the destination path", () => {
    setLocation("/", "");
    const router = new HashRouter();
    const seen: Array<unknown> = [];
    const unsubscribe = router.subscribe((route) => seen.push(route));

    router.navigate({ screen: "chat", destination: "inbox" });

    expect(window.location.pathname).toBe("/inbox");
    expect(window.location.hash).toBe("");
    expect(seen).toEqual([{ screen: "chat", destination: "inbox" }]);

    unsubscribe();
  });

  it("collapses the root destination back to '/' on navigate", () => {
    setLocation("/inbox", "");
    const router = new HashRouter();
    router.navigate({ screen: "chat", destination: ROOT_DESTINATION });
    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("");
  });

  // WC-P2 (AD-10 / R6) — the Run cockpit (root destination) round-trips a bound
  // conversation as `/run/<id>` via the subPath slot; a fresh run (no subPath)
  // stays at the legacy `/`. The URL model must not collide with the root
  // special-case and must survive a full serialize→parse round-trip.
  it("writes /run/<conversationId> for the root destination with a subPath", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({
      screen: "chat",
      destination: ROOT_DESTINATION,
      subPath: "conv-42",
    });
    expect(window.location.pathname).toBe("/run/conv-42");
    expect(window.location.hash).toBe("");
  });

  it("keeps the root at '/' when the run destination has no subPath", () => {
    setLocation("/run/conv-9", "");
    const router = new HashRouter();
    router.navigate({
      screen: "chat",
      destination: ROOT_DESTINATION,
      subPath: null,
    });
    expect(window.location.pathname).toBe("/");
  });

  it("round-trips /run/<conversationId> back to the run route with its subPath", () => {
    setLocation("/run/conv-42", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "chat",
      destination: ROOT_DESTINATION,
      subPath: "conv-42",
    });
  });

  it("round-trips a path-like conversation id without double-encoding", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({
      screen: "chat",
      destination: ROOT_DESTINATION,
      subPath: "org/abc-123",
    });
    expect(window.location.pathname).toBe("/run/org/abc-123");
    // Re-parse the URL we just wrote — the structural `/` survives.
    expect(new HashRouter().current()).toEqual({
      screen: "chat",
      destination: ROOT_DESTINATION,
      subPath: "org/abc-123",
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

  // P12-C — Team + Memory in-destination sub-paths.
  it("parses /team/<id> into a chat route with subPath", () => {
    setLocation("/team/user_alice", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "chat",
      destination: "team",
      subPath: "user_alice",
    });
  });

  it("parses /memory/<id> into a chat route with subPath", () => {
    setLocation("/memory/mem_42", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "chat",
      destination: "memory",
      subPath: "mem_42",
    });
  });

  it("parses /memory/proposals into a chat route with subPath='proposals'", () => {
    setLocation("/memory/proposals", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "chat",
      destination: "memory",
      subPath: "proposals",
    });
  });

  it("round-trips a destination subPath through navigate()", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({
      screen: "chat",
      destination: "team",
      subPath: "user_alice",
    });
    expect(window.location.pathname).toBe("/team/user_alice");
  });

  // P12-C — Phase 12 settings pages.
  it("parses /settings/security/webhooks into settings-p12", () => {
    setLocation("/settings/security/webhooks", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "settings-p12",
      subPath: "security-webhooks",
    });
  });

  it("parses /settings/notification-defaults into settings-p12", () => {
    setLocation("/settings/notification-defaults", "");
    const router = new HashRouter();
    expect(router.current()).toEqual({
      screen: "settings-p12",
      subPath: "notification-defaults",
    });
  });

  it("round-trips settings-p12 routes through navigate()", () => {
    setLocation("/", "");
    const router = new HashRouter();
    router.navigate({ screen: "settings-p12", subPath: "security-webhooks" });
    expect(window.location.pathname).toBe("/settings/security/webhooks");
    router.navigate({
      screen: "settings-p12",
      subPath: "notification-defaults",
    });
    expect(window.location.pathname).toBe("/settings/notification-defaults");
  });
});
