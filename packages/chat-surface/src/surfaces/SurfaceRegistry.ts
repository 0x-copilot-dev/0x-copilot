import type { ComponentType } from "react";

import { parseArtifactUri } from "../routing/uri/parser";
import type { SurfaceRendererProps } from "./types";

const registry = new Map<string, ComponentType<SurfaceRendererProps>>();

export function registerSurface(
  scheme: string,
  component: ComponentType<SurfaceRendererProps>,
): void {
  const existing = registry.get(scheme);
  if (existing && existing !== component) {
    throw new Error(
      `registerSurface: scheme "${scheme}" already registered to a different component`,
    );
  }
  registry.set(scheme, component);
}

export function resolveSurface(
  uri: string,
): ComponentType<SurfaceRendererProps> | null {
  const parsed = parseArtifactUri(uri);
  if (!parsed) {
    return null;
  }
  return registry.get(parsed.scheme) ?? null;
}

export function clearRegistry(): void {
  registry.clear();
}
