// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ADAPTER_ALLOWLIST } from "./adapterAllowlist";

// Soft snapshot of the JSON shipped from
// packages/service-contracts/src/enterprise_service_contracts/adapter_allowlist.json.
// The intent is to give anyone editing the JSON a visible signal that
// both runtimes (this TS scanner + the AI backend's 6B auditor) need to
// stay in sync — a mirror canary lives at
// services/ai-backend/tests/unit/agent_runtime/capabilities/render_adapter_generator/test_adapter_allowlist_loader.py.
describe("ADAPTER_ALLOWLIST — lock-in snapshot", () => {
  it("is on schema_version 1", () => {
    expect(ADAPTER_ALLOWLIST.schema_version).toBe(1);
  });

  it("exposes the three allowed modules in the documented order", () => {
    expect(Object.keys(ADAPTER_ALLOWLIST.allowed_imports)).toEqual([
      "react",
      "react-dom",
      "@enterprise-search/design-system",
    ]);
  });

  it("keeps react's named-export list narrow (D28 invariant)", () => {
    const react = ADAPTER_ALLOWLIST.allowed_imports.react;
    expect(react).toContain("createElement");
    expect(react).toContain("Fragment");
    expect(react).toContain("useState");
    expect(react).not.toContain("useEffect");
    expect(react).not.toContain("useRef");
    expect(react).not.toContain("useLayoutEffect");
  });

  it("forbids the well-known DOM / network globals", () => {
    const forbidden = new Set(ADAPTER_ALLOWLIST.forbidden_globals);
    for (const name of [
      "window",
      "document",
      "fetch",
      "XMLHttpRequest",
      "WebSocket",
      "EventSource",
      "localStorage",
      "sessionStorage",
      "navigator",
      "process",
      "globalThis",
      "require",
    ]) {
      expect(forbidden.has(name)).toBe(true);
    }
  });

  it("declares eval / Function / __proto__ as forbidden syntax", () => {
    expect([...ADAPTER_ALLOWLIST.forbidden_syntax].sort()).toEqual(
      ["Function", "__proto__", "eval"].sort(),
    );
  });

  it("ships a non-trivial forbidden-globals list", () => {
    // Soft length check: detects accidental wholesale deletion without
    // pinning every entry (which would be brittle as the union grows).
    expect(ADAPTER_ALLOWLIST.forbidden_globals.length).toBeGreaterThanOrEqual(
      20,
    );
  });

  it("encodes a render budget in milliseconds", () => {
    expect(ADAPTER_ALLOWLIST.budget_ms).toBeGreaterThan(0);
  });
});
