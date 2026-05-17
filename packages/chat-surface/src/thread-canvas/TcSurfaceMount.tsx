import {
  Component,
  useMemo,
  type CSSProperties,
  type ErrorInfo,
  type ReactElement,
  type ReactNode,
} from "react";

import type { Transport } from "@enterprise-search/chat-transport";

import { resolveAdapter } from "../surfaces/SurfaceRegistry";
import type { SaaSRendererAdapter } from "../surfaces/SaaSRendererAdapter";

const RENDER_BUDGET_MS = 100;

type Clock = () => number;

const defaultClock: Clock =
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? () => performance.now()
    : () => Date.now();

let activeClock: Clock = defaultClock;

// Test-only seam. The render-budget timer measures wall-clock time around
// adapter.renderCurrent, and we cannot mock performance.now globally
// without React's internals consuming our mocked values. Production code
// never calls this.
export function __setRenderBudgetClockForTests(clock: Clock | null): void {
  activeClock = clock ?? defaultClock;
}

export interface TcSurfaceMountProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly pendingDiff?: unknown | null;
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

function SurfacePlaceholder(props: { readonly scheme: string }): ReactElement {
  return (
    <div
      role="status"
      data-testid="surface-placeholder"
      style={placeholderStyle}
    >
      No renderer registered for {props.scheme || "(unknown scheme)"}
    </div>
  );
}

function schemeOf(uri: string): string {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : "";
}

function renderAdapterSafely(
  adapter: SaaSRendererAdapter,
  fallback: ReactElement,
): { node: ReactElement; timedOut: boolean; threw: boolean } {
  const start = activeClock();
  try {
    const element = adapter.renderCurrent({});
    const elapsed = activeClock() - start;
    if (elapsed > RENDER_BUDGET_MS) {
      return { node: fallback, timedOut: true, threw: false };
    }
    return { node: element, timedOut: false, threw: false };
  } catch {
    return { node: fallback, timedOut: false, threw: true };
  }
}

interface HostActionsProps {
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
}

function HostActions(props: HostActionsProps): ReactElement {
  const { onApprove, onReject } = props;
  return (
    <div data-testid="tc-surface-mount-actions" style={actionsRowStyle}>
      <button
        type="button"
        data-testid="tc-surface-mount-reject"
        onClick={onReject}
        disabled={!onReject}
        style={rejectButtonStyle}
      >
        Reject
      </button>
      <button
        type="button"
        data-testid="tc-surface-mount-approve"
        onClick={onApprove}
        disabled={!onApprove}
        style={approveButtonStyle}
      >
        Approve
      </button>
    </div>
  );
}

export function TcSurfaceMount(props: TcSurfaceMountProps): ReactElement {
  const { uri, onApprove, onReject, pendingDiff } = props;
  const scheme = useMemo(() => schemeOf(uri), [uri]);
  const adapter = useMemo(() => resolveAdapter(uri), [uri]);
  const fallback = <SurfacePlaceholder scheme={scheme} />;
  const showActions = pendingDiff != null;

  if (!adapter) {
    return (
      <div style={containerStyle}>
        {fallback}
        {showActions ? (
          <HostActions onApprove={onApprove} onReject={onReject} />
        ) : null}
      </div>
    );
  }

  const { node, timedOut, threw } = renderAdapterSafely(adapter, fallback);
  if (timedOut) {
    console.warn(
      `TcSurfaceMount: adapter for scheme "${adapter.scheme}" exceeded ${RENDER_BUDGET_MS}ms render budget; falling back.`,
    );
  } else if (threw) {
    console.warn(
      `TcSurfaceMount: adapter for scheme "${adapter.scheme}" threw during renderCurrent; falling back.`,
    );
  }

  const handleBoundaryError = (error: unknown): void => {
    console.warn(
      `TcSurfaceMount: adapter for scheme "${adapter.scheme}" threw during commit; falling back.`,
      error,
    );
  };

  return (
    <div style={containerStyle}>
      <AdapterBoundary
        onError={handleBoundaryError}
        fallback={fallback}
        errored={timedOut || threw}
      >
        {node}
      </AdapterBoundary>
      {showActions ? (
        <HostActions onApprove={onApprove} onReject={onReject} />
      ) : null}
    </div>
  );
}

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minHeight: 0,
  flex: "1 1 auto",
};

const placeholderStyle: CSSProperties = {
  padding: 16,
  borderRadius: 8,
  border: "1px dashed #2a2d31",
  color: "#9aa0a6",
  background: "#181a1c",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: 13,
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  justifyContent: "flex-end",
  gap: 8,
  padding: "8px 0",
};

const baseActionButton: CSSProperties = {
  padding: "6px 12px",
  borderRadius: 6,
  border: "1px solid #2a2d31",
  background: "#181a1c",
  color: "#f4f5f6",
  cursor: "pointer",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: 12,
};

const approveButtonStyle: CSSProperties = {
  ...baseActionButton,
  background: "#c2ff5a",
  borderColor: "#c2ff5a",
  color: "#0b0c0e",
};

const rejectButtonStyle: CSSProperties = {
  ...baseActionButton,
};
