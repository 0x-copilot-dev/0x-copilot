import { describe, expect, it } from "vitest";

import { allSyntheticStates, syntheticStateFor } from "./SyntheticStateFactory";
import { LAYOUT_TEMPLATES } from "./types";

// Patterns that would betray real-PII smuggled into a synthetic sample.
// The factory is the only path that feeds the preview pane; if any of
// these match, the compliance bar (PRD §9.5.3) is broken.
const REAL_EMAIL_PATTERN =
  /[a-z0-9._%+-]+@(?!example\.com|example\.org)[a-z0-9.-]+\.[a-z]{2,}/i;
const SSN_PATTERN = /\b\d{3}-\d{2}-\d{4}\b/;
const CREDIT_CARD_PATTERN = /\b(?:\d{4}[- ]){3}\d{4}\b/;
const PHONE_PATTERN = /\b\+?1?[-. ]?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b/;
// Allow generic dummy IPs (no real public ranges).
const REAL_IP_PATTERN =
  /\b(?!127\.|10\.|192\.168\.|0\.|255\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/;

function flatten(value: unknown): string {
  return JSON.stringify(value);
}

describe("SyntheticStateFactory", () => {
  it("returns a state + diff for every supported layout template", () => {
    for (const template of LAYOUT_TEMPLATES) {
      const state = syntheticStateFor(template);
      expect(state.current).toBeDefined();
      expect(state.diff).toBeDefined();
    }
  });

  it("emits no real-PII patterns in any synthetic state", () => {
    // Sensitive-data guard test required by Phase 7C scope. The
    // preview pane is the only place a reviewer's eyes see the
    // ``state`` object; nothing here may resemble tenant data.
    for (const state of allSyntheticStates()) {
      const blob = `${flatten(state.current)}\n${flatten(state.diff)}`;
      expect(blob).not.toMatch(REAL_EMAIL_PATTERN);
      expect(blob).not.toMatch(SSN_PATTERN);
      expect(blob).not.toMatch(CREDIT_CARD_PATTERN);
      expect(blob).not.toMatch(PHONE_PATTERN);
      expect(blob).not.toMatch(REAL_IP_PATTERN);
    }
  });

  it("uses the RFC 2606 reserved example.com domain for every URL", () => {
    // Stronger version of the email check: every host the synthetic
    // state references must be a reserved test domain. The reviewer
    // can never be tricked into clicking a real customer URL.
    for (const state of allSyntheticStates()) {
      const blob = `${flatten(state.current)}\n${flatten(state.diff)}`;
      const urls = blob.match(/https?:\/\/[a-z0-9.-]+/gi) ?? [];
      for (const url of urls) {
        expect(url.toLowerCase()).toMatch(
          /\/\/[a-z0-9-]*\.example\.(com|org|net)/,
        );
      }
    }
  });

  it("includes 'synthetic' as a marker in every sample", () => {
    // Defence-in-depth: each sample carries a literal marker the
    // reviewer can spot at a glance. Tests pin the marker so future
    // edits don't silently drop it.
    for (const state of allSyntheticStates()) {
      const blob =
        `${flatten(state.current)}\n${flatten(state.diff)}`.toLowerCase();
      expect(blob).toContain("synthetic");
    }
  });
});
