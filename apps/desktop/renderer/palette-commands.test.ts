import { describe, expect, it } from "vitest";

import { destinationsForProfile } from "@0x-copilot/chat-surface";

import {
  PALETTE_COMMANDS,
  SETTINGS_ROUTE_PREFIX,
  isSettingsRoute,
  settingsSectionFromRoute,
} from "./palette-commands";

// Helpers -------------------------------------------------------------------

const byId = (id: string) => PALETTE_COMMANDS.find((hit) => hit.id === id);
const navigationHits = () =>
  PALETTE_COMMANDS.filter((hit) => hit.kind === "navigation");
const actionHits = () =>
  PALETTE_COMMANDS.filter((hit) => hit.kind === "action");
const railNavHits = () =>
  navigationHits().filter((hit) => !isSettingsRoute(hit.route ?? ""));
const settingsNavHits = () =>
  navigationHits().filter((hit) => isSettingsRoute(hit.route ?? ""));

// ---------------------------------------------------------------------------

describe("PALETTE_COMMANDS registry", () => {
  it("carries exactly the DESIGN-SPEC §6 entries: 6 rail nav + 3 settings + 4 actions", () => {
    expect(PALETTE_COMMANDS).toHaveLength(13);
    expect(railNavHits()).toHaveLength(6);
    expect(settingsNavHits()).toHaveLength(3);
    expect(actionHits()).toHaveLength(4);
  });

  it("has a unique, stable id for every entry", () => {
    const ids = PALETTE_COMMANDS.map((hit) => hit.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("mirrors the solo rail as navigation hits (SSOT: destinations.ts)", () => {
    // The 6 rail entries are DERIVED from the solo destinations — same order,
    // same labels, same slugs. This pins the derivation to the rail SSOT.
    const solo = destinationsForProfile("single_user_desktop");
    expect(railNavHits().map((hit) => hit.title)).toEqual(
      solo.map((dest) => `Go to ${dest.label}`),
    );
    expect(railNavHits().map((hit) => hit.route)).toEqual(
      solo.map((dest) => dest.slug),
    );
  });

  it("routes rail nav hits to the bare destination slug the shell expects", () => {
    // Regression guard for the solo relabel: label "Tools" → slug `connectors`,
    // label "Skills" → slug `tools` (there is no `skills` slug). The host
    // forwards these routes straight to `onNavigate(slug)`.
    expect(byId("nav-run")).toMatchObject({ title: "Go to Run", route: "run" });
    expect(byId("nav-connectors")).toMatchObject({
      title: "Go to Tools",
      route: "connectors",
    });
    expect(byId("nav-tools")).toMatchObject({
      title: "Go to Skills",
      route: "tools",
    });
    expect(byId("nav-activity")).toMatchObject({
      title: "Go to Activity",
      route: "activity",
    });
  });

  it("gives every navigation hit a route and no action_token", () => {
    for (const hit of navigationHits()) {
      expect(hit.kind).toBe("navigation");
      expect(typeof hit.route).toBe("string");
      expect(hit.route).not.toBe("");
      expect(hit.action_token).toBeUndefined();
      expect(hit.target).toBeUndefined();
    }
  });

  it("gives every action hit an action_token and no route", () => {
    for (const hit of actionHits()) {
      expect(hit.kind).toBe("action");
      expect(typeof hit.action_token).toBe("string");
      expect(hit.action_token).not.toBe("");
      expect(hit.route).toBeUndefined();
      expect(hit.target).toBeUndefined();
    }
  });

  it("exposes the §6 settings sections behind the settings-route convention", () => {
    expect(byId("settings-model-behavior")).toMatchObject({
      kind: "navigation",
      title: "Model & behavior",
      route: `${SETTINGS_ROUTE_PREFIX}/model-behavior`,
    });
    expect(byId("settings-appearance")).toMatchObject({
      kind: "navigation",
      title: "Appearance",
      route: `${SETTINGS_ROUTE_PREFIX}/appearance`,
    });
    // "Open Settings" carries the bare route → host opens the default section.
    expect(byId("settings-open")).toMatchObject({
      kind: "navigation",
      title: "Open Settings",
      route: SETTINGS_ROUTE_PREFIX,
    });
  });

  it("exposes the §6 action tokens the host dispatches", () => {
    expect(byId("action-new-chat")?.action_token).toBe("new-chat");
    expect(byId("action-add-provider-key")?.action_token).toBe(
      "add-provider-key",
    );
    expect(byId("action-download-local-model")?.action_token).toBe(
      "download-local-model",
    );
    expect(byId("action-connect-tool")?.action_token).toBe("connect-tool");
  });
});

describe("settings-route convention helpers", () => {
  it("isSettingsRoute recognises the bare and sectioned settings routes", () => {
    expect(isSettingsRoute("settings")).toBe(true);
    expect(isSettingsRoute("settings/appearance")).toBe(true);
    // A rail slug is not a settings route.
    expect(isSettingsRoute("run")).toBe(false);
    expect(isSettingsRoute("connectors")).toBe(false);
    // Guard against a slug that merely starts with the word.
    expect(isSettingsRoute("settingsx")).toBe(false);
  });

  it("settingsSectionFromRoute extracts the section, undefined for the bare route", () => {
    expect(settingsSectionFromRoute("settings/appearance")).toBe("appearance");
    expect(settingsSectionFromRoute("settings/model-behavior")).toBe(
      "model-behavior",
    );
    // Bare `settings` → no explicit section (host defaults to `profile`).
    expect(settingsSectionFromRoute("settings")).toBeUndefined();
    // Non-settings routes yield undefined.
    expect(settingsSectionFromRoute("run")).toBeUndefined();
  });
});
