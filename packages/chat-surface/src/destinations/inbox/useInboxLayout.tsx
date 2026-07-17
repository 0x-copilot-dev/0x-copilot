// useInboxLayout — Inbox responsive-layout hook (P4-B3).
//
// Source:
//   docs/atlas-new-design/destinations/inbox-prd.md §8 / §3.1 layout
//   docs/atlas-new-design/cross-audit.md §9.2 — "Single-pane swap below
//     960px (list ↔ detail). Wider: two-pane with detail rendered
//     alongside list."
//
// Invariants:
//   - No JS `window` resize listeners. The hook observes the destination
//     container via `ResizeObserver`. This keeps the breakpoint local to
//     the destination — desktop/web embeds with shrunk side rails or
//     split workspace panes inherit the correct mode for free, because
//     the container width — not the viewport — is what matters.
//   - Works in jsdom: `ResizeObserver` is a class, and tests can shim it
//     to control the observed width directly. No layout/paint required.
//   - One source of truth for the breakpoint constant
//     (`INBOX_BREAKPOINT_PX`); both the hook and any styling consumer
//     must read it from here.
//   - Pure: the hook does not mutate `focusedItemId`. The caller still
//     owns the focus state; `onShowDetail` / `onShowList` are thin
//     wrappers that forward to caller-supplied callbacks so the same
//     navigation primitive is used in both pane modes.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type RefObject,
} from "react";

import type { InboxItemId } from "@0x-copilot/api-types";

// ===========================================================================
// Breakpoint constant — cross-audit §9.2 binding decision
// ===========================================================================

/**
 * Container-width threshold at which the Inbox swaps from two-pane
 * (list + detail side by side) to single-pane (list OR detail).
 *
 * Bound to the destination's own container, not the viewport, so the
 * breakpoint is correct whether the user has a wide window with a
 * collapsed rail or a narrow split workspace.
 */
export const INBOX_BREAKPOINT_PX = 960;

// ===========================================================================
// Modes
// ===========================================================================

/**
 * Pane-mode the destination renders.
 *
 * - `two-pane`: container >= 960px. List on the left, detail on the
 *   right when `focusedItemId` is set; if no item is focused, the right
 *   pane shows an empty hint and the list keeps the full content width.
 * - `single-pane-list`: container < 960px AND no item focused.
 * - `single-pane-detail`: container < 960px AND an item is focused.
 */
export type InboxLayoutMode =
  | "two-pane"
  | "single-pane-list"
  | "single-pane-detail";

// ===========================================================================
// Hook input + output
// ===========================================================================

export interface UseInboxLayoutOptions {
  /**
   * Ref to the destination's outer container. The hook installs a
   * `ResizeObserver` on this element to derive the current width.
   * Passing `null` while the ref is unattached is supported; the hook
   * uses the SSR-safe default until the observer fires.
   */
  readonly containerRef: RefObject<HTMLElement | null>;

  /**
   * Currently-focused inbox item, if any. Drives the single-pane
   * sub-mode: detail when set, list when null.
   */
  readonly focusedItemId: InboxItemId | null;

  /**
   * Caller-supplied open-detail callback. The list pane invokes this
   * when a row is opened; the hook just re-exports it so list rows and
   * the destination shell use the same primitive whether the layout is
   * two-pane or single-pane.
   */
  readonly onOpenDetail?: (id: InboxItemId) => void;

  /**
   * Caller-supplied close-detail callback. Powers the "back to inbox"
   * affordance in `single-pane-detail` mode.
   */
  readonly onCloseDetail?: () => void;

  /**
   * SSR / pre-observer width fallback. Defaults to a value above the
   * breakpoint so the first render matches the wide-screen layout (the
   * narrow-screen swap is a non-default opt-in, not a flash).
   */
  readonly defaultWidthPx?: number;
}

export interface InboxLayoutState {
  /** Computed mode for the current container width + focus state. */
  readonly mode: InboxLayoutMode;

  /** True iff `mode` is one of the `single-pane-*` variants. */
  readonly isNarrow: boolean;

  /** Last observed container width in pixels (or the default fallback). */
  readonly containerWidthPx: number;

  /**
   * Forwarder for the row "open detail" action. Calls
   * `onOpenDetail(id)` when provided; otherwise a no-op. Stable across
   * renders.
   */
  readonly onShowDetail: (id: InboxItemId) => void;

  /**
   * Forwarder for the detail "back to inbox" action. Calls
   * `onCloseDetail()` when provided; otherwise a no-op. Stable across
   * renders.
   */
  readonly onShowList: () => void;
}

// ===========================================================================
// Implementation
// ===========================================================================

/**
 * Observe the destination container with `ResizeObserver` and derive
 * the pane-mode. Returns navigation forwarders so list rows and the
 * back affordance share one primitive across pane modes.
 */
export function useInboxLayout(
  options: UseInboxLayoutOptions,
): InboxLayoutState {
  const {
    containerRef,
    focusedItemId,
    onOpenDetail,
    onCloseDetail,
    defaultWidthPx = INBOX_BREAKPOINT_PX,
  } = options;

  // Start at the SSR-safe default — typically wide. Avoids a layout
  // flash to the narrow mode before the observer has reported.
  const [widthPx, setWidthPx] = useState<number>(defaultWidthPx);

  useEffect(() => {
    const node = containerRef.current;
    if (node === null) return;
    if (typeof ResizeObserver === "undefined") {
      // Defensive: extremely old environments lack ResizeObserver.
      // Keep the SSR default rather than wiring a `window` fallback
      // (the hard rule: no JS window listeners).
      return;
    }

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // Prefer `contentBoxSize` (a list in modern UAs); fall back to
        // `contentRect.width` (jsdom shims and the spec's legacy field).
        const next =
          extractWidthFromContentBoxSize(entry.contentBoxSize) ??
          entry.contentRect.width;
        if (!Number.isFinite(next)) continue;
        setWidthPx((prev) => (prev === next ? prev : next));
      }
    });
    ro.observe(node);
    return () => {
      ro.disconnect();
    };
  }, [containerRef]);

  const isNarrow = widthPx < INBOX_BREAKPOINT_PX;
  const mode: InboxLayoutMode = useMemo(() => {
    if (!isNarrow) return "two-pane";
    return focusedItemId !== null ? "single-pane-detail" : "single-pane-list";
  }, [isNarrow, focusedItemId]);

  const onShowDetail = useCallback(
    (id: InboxItemId) => {
      if (onOpenDetail !== undefined) onOpenDetail(id);
    },
    [onOpenDetail],
  );
  const onShowList = useCallback(() => {
    if (onCloseDetail !== undefined) onCloseDetail();
  }, [onCloseDetail]);

  return {
    mode,
    isNarrow,
    containerWidthPx: widthPx,
    onShowDetail,
    onShowList,
  };
}

// ===========================================================================
// Helpers
// ===========================================================================

/**
 * `ResizeObserverEntry.contentBoxSize` is a `ReadonlyArray<ResizeObserverSize>`
 * in modern UAs (and a single object in older Firefox). Normalise both
 * shapes and return `inlineSize` (== width in horizontal writing modes).
 */
function extractWidthFromContentBoxSize(
  contentBoxSize: ResizeObserverEntry["contentBoxSize"] | undefined,
): number | null {
  if (contentBoxSize === undefined || contentBoxSize === null) return null;
  // Array form (spec-compliant).
  if (Array.isArray(contentBoxSize)) {
    const first = contentBoxSize[0];
    return first !== undefined ? first.inlineSize : null;
  }
  // Single-object form (legacy Firefox).
  const legacy = contentBoxSize as unknown as ResizeObserverSize;
  return legacy.inlineSize;
}
