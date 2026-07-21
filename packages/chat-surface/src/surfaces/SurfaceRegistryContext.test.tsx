import { createElement, type ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { type SaaSRendererAdapter } from "./SaaSRendererAdapter";
import {
  clearRegistry,
  createSurfaceRegistry,
  globalSurfaceRegistry,
  registerAdapter,
  resolveAdapter,
} from "./SurfaceRegistry";
import {
  SurfaceRegistryProvider,
  useSurfaceRegistry,
} from "./SurfaceRegistryContext";

function makeAdapter(scheme: string, version: number): SaaSRendererAdapter {
  return {
    scheme,
    matches: (uri: string) => uri.startsWith(`${scheme}://`),
    renderCurrent: (): ReactElement =>
      createElement("div", { "data-scheme": scheme, "data-version": version }),
    renderDiff: (): ReactElement => createElement("div", null, "diff"),
    metadata: { origin: "first-party", schemaVersion: version },
  };
}

describe("createSurfaceRegistry — isolation", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("resolves two isolated instances independently", () => {
    const a = createSurfaceRegistry();
    const b = createSurfaceRegistry();

    const emailA = makeAdapter("email", 1);
    const emailB = makeAdapter("email", 2);
    a.registerAdapter(emailA);
    b.registerAdapter(emailB);

    expect(a.resolveAdapter("email://x")).toBe(emailA);
    expect(b.resolveAdapter("email://x")).toBe(emailB);
    // Neither leaked into the other, nor into the global.
    expect(a.resolveAdapter("email://x")).not.toBe(emailB);
    expect(resolveAdapter("email://x")).toBeNull();
  });

  it("keeps an isolated instance separate from the module-global", () => {
    const isolated = createSurfaceRegistry();
    const globalOnly = makeAdapter("email", 1);
    registerAdapter(globalOnly); // mutates the global via the free function

    expect(resolveAdapter("email://x")).toBe(globalOnly);
    expect(globalSurfaceRegistry.resolveAdapter("email://x")).toBe(globalOnly);
    // The isolated instance never saw the global registration.
    expect(isolated.resolveAdapter("email://x")).toBeNull();
  });

  it("clearRegistry on one instance does not touch another", () => {
    const a = createSurfaceRegistry();
    const b = createSurfaceRegistry();
    a.registerAdapter(makeAdapter("email", 1));
    b.registerAdapter(makeAdapter("email", 1));
    a.clearRegistry();
    expect(a.resolveAdapter("email://x")).toBeNull();
    expect(b.resolveAdapter("email://x")).not.toBeNull();
  });
});

function RegistryProbe(): ReactElement {
  const registry = useSurfaceRegistry();
  const hit = registry.resolveAdapter("email://x");
  return createElement(
    "div",
    { "data-testid": "probe" },
    hit ? `v${hit.metadata.schemaVersion}` : "none",
  );
}

describe("SurfaceRegistryProvider — React scoping", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("falls back to the global registry when no provider is present", () => {
    registerAdapter(makeAdapter("email", 7));
    render(<RegistryProbe />);
    expect(screen.getByTestId("probe").textContent).toBe("v7");
  });

  it("resolves against the provided registry inside a provider", () => {
    // Global has v7; the scoped instance has v3. The subtree must see v3.
    registerAdapter(makeAdapter("email", 7));
    const scoped = createSurfaceRegistry();
    scoped.registerAdapter(makeAdapter("email", 3));

    render(
      <SurfaceRegistryProvider registry={scoped}>
        <RegistryProbe />
      </SurfaceRegistryProvider>,
    );
    expect(screen.getByTestId("probe").textContent).toBe("v3");
  });

  it("two providers with distinct registries resolve independently", () => {
    const left = createSurfaceRegistry();
    left.registerAdapter(makeAdapter("email", 1));
    const right = createSurfaceRegistry();
    right.registerAdapter(makeAdapter("email", 2));

    render(
      <div>
        <SurfaceRegistryProvider registry={left}>
          <RegistryProbe />
        </SurfaceRegistryProvider>
        <SurfaceRegistryProvider registry={right}>
          <RegistryProbe />
        </SurfaceRegistryProvider>
      </div>,
    );

    const probes = screen.getAllByTestId("probe");
    expect(probes.map((p) => p.textContent).sort()).toEqual(["v1", "v2"]);
  });
});
