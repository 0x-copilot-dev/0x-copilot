import {
  Component,
  useMemo,
  type CSSProperties,
  type ErrorInfo,
  type ReactElement,
  type ReactNode,
} from "react";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import { TIER3_SCHEME } from "../surfaces/SaaSRendererAdapter";
import { resolveAdapter } from "../surfaces/SurfaceRegistry";
import type { SaaSRendererAdapter } from "../surfaces/SaaSRendererAdapter";
import type { PendingDiff } from "../surfaces/types";
import { projectAt, type SurfacePayload } from "./eventProjector";

const RENDER_BUDGET_MS = 100;
const TIER3_URI = `${TIER3_SCHEME}://`;

type Clock = () => number;

const defaultClock: Clock =
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? () => performance.now()
    : () => Date.now();

let activeClock: Clock = defaultClock;

// Test-only seam. React renders are synchronous, so the budget is a
// wall-clock measurement around the adapter call rather than a preemptive
// timeout. Tier-2 in Phase 6 moves to a Worker for real preemption (D29).
export function __setRenderBudgetClockForTests(clock: Clock | null): void {
  activeClock = clock ?? defaultClock;
}

export interface PendingDiffHandle<TDiff = unknown> {
  readonly diff: TDiff;
  readonly meta: PendingDiff;
}

/**
 * Client-side time-travel: derive the surface state at a past
 * `sequence_no` by replaying events through the projector. No backend
 * snapshot call — Phase 1 Q1 decision (impl-plan §3) chose client-side
 * reducers; this is the canonical entry point.
 *
 * Returns `undefined` when the URI never had a recorded payload at or
 * before the cursor. Callers (typically `TcSurfaceMount` itself or a
 * mini-timeline scrubber) treat `undefined` as "render the empty
 * placeholder" — there is no committed state yet at that moment in
 * time.
 *
 * Pure function; safe to call inside `useMemo` keyed by
 * `[events.length, sequenceNo]`. Internally delegates to the projector
 * so the surface-mount's view of surface state matches the chat side
 * and the swimlanes exactly.
 */
export function reduceTo(
  events: readonly RuntimeEventEnvelope[],
  sequenceNo: number,
  surfaceUri: string,
): SurfacePayload | undefined {
  return projectAt(events, sequenceNo).surfaceState.get(surfaceUri);
}

export interface TcSurfaceMountProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly state?: unknown;
  readonly pendingDiff?: PendingDiffHandle | null;
  readonly onApprove?: (diffId: string) => void;
  readonly onReject?: (diffId: string) => void;
  readonly onSuggestChanges?: (diffId: string) => void;
}

interface AdapterBoundaryProps {
  readonly children: ReactNode;
  readonly onError: (error: unknown) => void;
  readonly fallback: ReactElement;
  readonly errored: boolean;
}

interface AdapterBoundaryState {
  readonly errored: boolean;
}

// React error boundaries require a class. Scoped to this file so the rest
// of chat-surface remains functional-only per PRD §6.4.
class AdapterBoundary extends Component<
  AdapterBoundaryProps,
  AdapterBoundaryState
> {
  constructor(props: AdapterBoundaryProps) {
    super(props);
    this.state = { errored: props.errored };
  }

  static getDerivedStateFromError(): AdapterBoundaryState {
    return { errored: true };
  }

  static getDerivedStateFromProps(
    next: AdapterBoundaryProps,
    prev: AdapterBoundaryState,
  ): AdapterBoundaryState | null {
    if (next.errored && !prev.errored) {
      return { errored: true };
    }
    return null;
  }

  componentDidCatch(error: unknown, _info: ErrorInfo): void {
    this.props.onError(error);
  }

  render(): ReactNode {
    if (this.state.errored) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

function FallbackEmpty(props: { readonly scheme: string }): ReactElement {
  return (
    <div role="status" data-testid="surface-placeholder" style={fallbackStyle}>
      No adapter registered for {props.scheme || "(unknown scheme)"}
    </div>
  );
}

function schemeOf(uri: string): string {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : "";
}

interface SyncRenderResult {
  readonly node: ReactElement | null;
  readonly timedOut: boolean;
  readonly threw: boolean;
}

function callAdapter(
  adapter: SaaSRendererAdapter,
  state: unknown,
  pendingDiff: PendingDiffHandle | null | undefined,
): SyncRenderResult {
  const start = activeClock();
  try {
    const element = pendingDiff
      ? adapter.renderDiff(pendingDiff.diff)
      : adapter.renderCurrent(state ?? {});
    const elapsed = activeClock() - start;
    if (elapsed > RENDER_BUDGET_MS) {
      return { node: null, timedOut: true, threw: false };
    }
    return { node: element, timedOut: false, threw: false };
  } catch {
    return { node: null, timedOut: false, threw: true };
  }
}

interface HostControlsProps {
  readonly diffId: string;
  readonly onApprove?: (diffId: string) => void;
  readonly onReject?: (diffId: string) => void;
  readonly onSuggestChanges?: (diffId: string) => void;
}

function HostControls(props: HostControlsProps): ReactElement {
  const { diffId, onApprove, onReject, onSuggestChanges } = props;
  const handleApprove = (): void => onApprove?.(diffId);
  const handleReject = (): void => onReject?.(diffId);
  const handleSuggest = (): void => onSuggestChanges?.(diffId);
  return (
    <div
      role="group"
      aria-label="Pending diff actions"
      data-testid="tc-surface-mount-controls"
      style={controlsRowStyle}
    >
      <button
        type="button"
        onClick={handleReject}
        data-testid="tc-surface-mount-reject"
        style={secondaryButtonStyle}
      >
        Reject
      </button>
      <button
        type="button"
        onClick={handleSuggest}
        data-testid="tc-surface-mount-suggest"
        style={secondaryButtonStyle}
      >
        Suggest changes
      </button>
      <button
        type="button"
        onClick={handleApprove}
        data-testid="tc-surface-mount-approve"
        style={primaryButtonStyle}
      >
        Approve
      </button>
    </div>
  );
}

export function TcSurfaceMount(props: TcSurfaceMountProps): ReactElement {
  const { uri, state, pendingDiff, onApprove, onReject, onSuggestChanges } =
    props;
  const scheme = useMemo(() => schemeOf(uri), [uri]);
  const primary = useMemo(() => resolveAdapter(uri), [uri]);
  // Probe the wildcard bucket directly. Per PRD §3.4 tier-3 "Always works"
  // — its matches() returns true universally — so passing the wildcard
  // sentinel URI is equivalent to passing the original URI for the
  // contractual tier-3 adapter.
  const tier3 = useMemo(() => resolveAdapter(TIER3_URI), []);
  const placeholder = <FallbackEmpty scheme={scheme} />;

  let chosenNode: ReactElement | null = null;
  let chosenLabel: "primary" | "tier3" | "placeholder" = "placeholder";
  let primaryFailure: "throw" | "timeout" | null = null;
  let tier3Failure: "throw" | "timeout" | null = null;

  if (primary) {
    const primaryRender = callAdapter(primary, state, pendingDiff);
    if (primaryRender.node !== null) {
      chosenNode = primaryRender.node;
      chosenLabel = "primary";
    } else {
      primaryFailure = primaryRender.timedOut ? "timeout" : "throw";
    }
  }

  if (chosenNode === null && tier3 && tier3 !== primary) {
    const tier3Render = callAdapter(tier3, state, pendingDiff);
    if (tier3Render.node !== null) {
      chosenNode = tier3Render.node;
      chosenLabel = "tier3";
    } else {
      tier3Failure = tier3Render.timedOut ? "timeout" : "throw";
    }
  }

  if (primary && primaryFailure) {
    console.warn(
      `TcSurfaceMount: adapter for scheme "${primary.scheme}" ${
        primaryFailure === "timeout"
          ? `exceeded ${RENDER_BUDGET_MS}ms render budget`
          : "threw during render"
      }; falling back${tier3 && tier3 !== primary ? " to tier-3" : ""}.`,
    );
  }
  if (tier3Failure) {
    console.warn(
      `TcSurfaceMount: tier-3 adapter ${
        tier3Failure === "timeout"
          ? `exceeded ${RENDER_BUDGET_MS}ms render budget`
          : "threw during render"
      }; falling back to placeholder.`,
    );
  }

  const errored = chosenNode === null;
  const renderedChild = chosenNode ?? placeholder;

  const handleBoundaryError = (error: unknown): void => {
    console.warn(
      `TcSurfaceMount: ${chosenLabel} adapter for scheme "${
        scheme || "(unknown)"
      }" threw during commit; falling back.`,
      error,
    );
  };

  const showControls = Boolean(pendingDiff);

  return (
    <div
      data-testid="tc-surface-mount"
      data-tier={chosenLabel}
      style={rootStyle}
    >
      <div style={contentStyle}>
        <AdapterBoundary
          onError={handleBoundaryError}
          fallback={placeholder}
          errored={errored}
        >
          {renderedChild}
        </AdapterBoundary>
      </div>
      {showControls && pendingDiff ? (
        <HostControls
          diffId={pendingDiff.meta.diffId}
          onApprove={onApprove}
          onReject={onReject}
          onSuggestChanges={onSuggestChanges}
        />
      ) : null}
    </div>
  );
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  width: "100%",
};

const contentStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
};

const controlsRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "flex-end",
  paddingTop: 4,
};

const primaryButtonStyle: CSSProperties = {
  background: "#c2ff5a",
  color: "#101113",
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "#f4f5f6",
  border: "1px solid #2a2d31",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const fallbackStyle: CSSProperties = {
  padding: 16,
  borderRadius: 8,
  border: "1px dashed #2a2d31",
  color: "#9aa0a6",
  background: "#181a1c",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: "var(--font-size-sm)",
};
