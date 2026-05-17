import type { ReactElement } from "react";

export type SaaSRendererAdapterOrigin =
  | "first-party"
  | "agent-generated"
  | "community";

export interface SaaSRendererAdapterMetadata {
  readonly origin: SaaSRendererAdapterOrigin;
  readonly generatedAt?: string;
  readonly generatorModel?: string;
  readonly schemaVersion: number;
}

export interface SaaSRendererAdapter<TResource = unknown, TDiff = unknown> {
  readonly scheme: string;
  readonly matches: (uri: string) => boolean;
  readonly renderCurrent: (state: TResource) => ReactElement;
  readonly renderDiff: (diff: TDiff) => ReactElement;
  readonly metadata: SaaSRendererAdapterMetadata;
}

// Wildcard scheme reserved for the tier-3 GenericStructuredDiff fallback.
// SurfaceRegistry.resolveAdapter consults wildcards only after every exact
// scheme match has missed.
export const TIER3_SCHEME = "*";
