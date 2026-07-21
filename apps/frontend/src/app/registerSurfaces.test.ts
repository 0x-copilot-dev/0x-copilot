// PRD-05 — web host surface registration: coverage + idempotency + tier-2-free.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  clearRegistry,
  resolveAdapter,
  unregisterAdapter,
} from "@0x-copilot/chat-surface";

import { registerSurfaces } from "./registerSurfaces";

const ARCHETYPE_SCHEMES = [
  "record",
  "table",
  "message",
  "doc",
  "board",
] as const;

afterEach(() => {
  // Keep the shared global SurfaceRegistry hermetic across tests.
  clearRegistry();
});

describe("registerSurfaces (PRD-05)", () => {
  it("registers the tier-3 generic + tier-1 SaaS + PRD-03 archetype stack", () => {
    registerSurfaces();

    // PRD-03 archetypes — resolvable, each to its own scheme.
    for (const scheme of ARCHETYPE_SCHEMES) {
      expect(resolveAdapter(`${scheme}://x`)?.scheme).toBe(scheme);
    }
    // Tier-1 SaaS adapters (registerAll) — a representative pair.
    expect(resolveAdapter("email://draft-1")?.scheme).toBe("email");
    expect(resolveAdapter("sf-opp://oppty-9")?.scheme).toBe("sf-opp");
    // Tier-3 generic wildcard catches any otherwise-unclaimed scheme.
    expect(resolveAdapter("totally-unknown-scheme://z")).not.toBeNull();
  });

  // AC4 — double-invocation safe: the SurfaceRegistry replaces a same-{scheme,
  // version} entry in place, so a second call never creates a duplicate. Proven
  // by unregistering the single v1 entry once: the specific adapter is gone and
  // resolution falls through to the tier-3 wildcard ("*"). A duplicate would
  // leave a second specific entry, keeping the resolved scheme unchanged.
  it("is idempotent — a double call leaves exactly one adapter per scheme", () => {
    registerSurfaces();
    registerSurfaces();

    for (const scheme of [...ARCHETYPE_SCHEMES, "email", "sf-opp"] as const) {
      // The specific adapter wins over the tier-3 wildcard after two registers.
      expect(resolveAdapter(`${scheme}://x`)?.scheme).toBe(scheme);
      // Remove the single v1 entry ONCE — resolution now falls to tier-3.
      unregisterAdapter(scheme, 1);
      expect(resolveAdapter(`${scheme}://x`)?.scheme).toBe("*");
    }
  });

  // AC5 — web has NO Tier2Bridge reference and no IPC imports. This module is
  // the single surface-registration site; guard it structurally so the
  // desktop-only tier-2 IPC bridge never leaks into the web bundle (web tier-2
  // arrives in PRD-10).
  it("has no Tier2Bridge / IPC reference (web is tier-2-free until PRD-10)", () => {
    const source = readFileSync(
      join(import.meta.dirname, "registerSurfaces.ts"),
      "utf8",
    );
    // Strip comments so the rationale prose (which names the desktop bridge to
    // explain its ABSENCE) doesn't trip the guard — only the executable code
    // must be free of any tier-2/IPC reference. Removes `/* … */` blocks (incl.
    // JSDoc) and `//` line comments.
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("//"))
      .join("\n");
    expect(code).not.toMatch(/Tier2Bridge/);
    expect(code).not.toMatch(/IpcTransport/);
    expect(code).not.toMatch(/window\.bridge/);
    expect(code).not.toMatch(/\bipc\b/i);
  });
});
