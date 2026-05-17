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
    <div
      role="status"
      data-testid="tc-surface-mount-fallback"
      style={fallbackStyle}
    >
      No adapter registered for {props.scheme || "(unknown scheme)"}
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

export function TcSurfaceMount(props: TcSurfaceMountProps): ReactElement {
  const { uri } = props;
  const scheme = useMemo(() => schemeOf(uri), [uri]);
  const adapter = useMemo(() => resolveAdapter(uri), [uri]);
  const fallback = <FallbackEmpty scheme={scheme} />;

  if (!adapter) {
    return fallback;
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
    <AdapterBoundary
      onError={handleBoundaryError}
      fallback={fallback}
      errored={timedOut || threw}
    >
      {node}
    </AdapterBoundary>
  );
}

const fallbackStyle: CSSProperties = {
  padding: 16,
  borderRadius: 8,
  border: "1px dashed #2a2d31",
  color: "#9aa0a6",
  background: "#181a1c",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: 13,
};
