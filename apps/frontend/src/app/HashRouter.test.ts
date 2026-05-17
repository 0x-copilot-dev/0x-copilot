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
      expect(router.current()).toEqual({
        screen: "chat",
        destination,
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
});
