import type { ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

// Q3 / Q5 instrumentation (PRD §9.5.1, D29). Wraps an installed adapter so
// every live `renderCurrent` / `renderDiff` call is observed. On throw, the
// wrapper records the failure through `onError(scheme, version, method,
// error)` and RETHROWS — the host's React error boundary (Phase 4-A, in
// TcSurfaceMount) still catches the throw and renders tier-3. Two boundaries
// are intentional: ours records, theirs renders. The wrapper introduces no
// privileged globals — it is a plain try/catch around the underlying
// callable; the adapter still runs inside 6A's Worker (D29).

export interface BoundaryError {
  readonly scheme: string;
  readonly version: number;
  readonly method: "renderCurrent" | "renderDiff";
  readonly error: Error;
}

export type BoundaryListener = (info: BoundaryError) => void;

function asError(thrown: unknown): Error {
  if (thrown instanceof Error) return thrown;
  return new Error(typeof thrown === "string" ? thrown : String(thrown));
}

export function wrapWithBoundary(
  adapter: SaaSRendererAdapter,
  onError: BoundaryListener,
): SaaSRendererAdapter {
  const scheme = adapter.scheme;
  const version = adapter.metadata.schemaVersion;

  const renderCurrent = (state: unknown): ReactElement => {
    try {
      return adapter.renderCurrent(state);
    } catch (thrown) {
      const error = asError(thrown);
      try {
        onError({ scheme, version, method: "renderCurrent", error });
      } catch {
        // The audit callback itself is allowed to fail (logger / fs / etc.)
        // but the wrapper must still rethrow so the host's React boundary
        // catches and falls back to tier-3. Swallow listener failures.
      }
      throw thrown;
    }
  };

  const renderDiff = (diff: unknown): ReactElement => {
    try {
      return adapter.renderDiff(diff);
    } catch (thrown) {
      const error = asError(thrown);
      try {
        onError({ scheme, version, method: "renderDiff", error });
      } catch {
        // See renderCurrent comment.
      }
      throw thrown;
    }
  };

  return {
    scheme: adapter.scheme,
    matches: adapter.matches,
    renderCurrent,
    renderDiff,
    metadata: adapter.metadata,
  };
}
