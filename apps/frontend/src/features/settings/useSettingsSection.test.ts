// PR 4.3 — hash-based Settings section routing.
//
// Three behaviours under test:
//   1. ``useSettingsSection`` reads the current hash on mount.
//   2. ``hashchange`` / ``popstate`` events resync the hook.
//   3. Calling ``navigate`` updates the URL hash via ``pushState`` and
//      sets the local state to the new section.
//   4. ``migrateLegacySettingsPath`` rewrites ``/settings/<section>``
//      into ``/settings#<section>`` once on mount.

import { describe, expect, it, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  DEFAULT_SETTINGS_SECTION,
  migrateLegacySettingsPath,
  useSettingsSection,
} from "./useSettingsSection";

function setLocation(path: string, hash: string): void {
  // jsdom mutates location through history. Use replaceState so the
  // browser doesn't try to navigate away.
  window.history.replaceState(null, "", `${path}${hash}`);
}

beforeEach(() => {
  setLocation("/settings", "");
});

describe("useSettingsSection", () => {
  it("returns DEFAULT_SETTINGS_SECTION when no hash is present", () => {
    setLocation("/settings", "");
    const { result } = renderHook(() => useSettingsSection());
    expect(result.current[0]).toBe(DEFAULT_SETTINGS_SECTION);
  });

  it("reads the active section from the URL hash on mount", () => {
    setLocation("/settings", "#privacy-data");
    const { result } = renderHook(() => useSettingsSection());
    expect(result.current[0]).toBe("privacy-data");
  });

  it("falls back to default for unknown hashes", () => {
    setLocation("/settings", "#not-a-real-section");
    const { result } = renderHook(() => useSettingsSection());
    expect(result.current[0]).toBe(DEFAULT_SETTINGS_SECTION);
  });

  it("syncs on browser hashchange events", () => {
    setLocation("/settings", "#profile");
    const { result } = renderHook(() => useSettingsSection());
    expect(result.current[0]).toBe("profile");
    act(() => {
      setLocation("/settings", "#model-and-behavior");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current[0]).toBe("model-and-behavior");
  });

  it("navigate() updates the URL hash and the active section", () => {
    setLocation("/settings", "");
    const { result } = renderHook(() => useSettingsSection());
    act(() => {
      result.current[1]("connectors");
    });
    expect(window.location.hash).toBe("#connectors");
    expect(result.current[0]).toBe("connectors");
  });

  it("navigate() is a no-op when the section is already active", () => {
    setLocation("/settings", "#privacy-data");
    const { result } = renderHook(() => useSettingsSection());
    const stateLength = window.history.length;
    act(() => {
      result.current[1]("privacy-data");
    });
    expect(window.history.length).toBe(stateLength);
    expect(result.current[0]).toBe("privacy-data");
  });
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
