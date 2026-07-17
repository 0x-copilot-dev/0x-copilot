// CommandPalette — global ⌘K palette (substrate-shared).
//
// Source: team-memory-cmdk-prd.md §1.3 + §3.3 + §7.3; cross-audit §1.1
// (ItemRef-routed entity hits) + §1.2 (port pattern).
//
// Substrate seam: ALL data flows through `PaletteSearchPort.search()`.
// This component does not import a fetch / transport / IO primitive
// directly. Web, desktop, and mobile hosts each implement the port and
// pass it in via `searchPort`.
//
// Behaviors:
//   * `open` prop controls visibility — host owns it (the ⌘K hotkey
//     hook flips it).
//   * ESC + click on scrim → onRequestClose.
//   * Input autofocuses on open.
//   * Search is debounced 150ms; one in-flight call at a time (newer
//     queries cancel older results via a generation counter).
//   * Hits are grouped by kind: navigation / entity / action / command.
//     Group headers render only when the group is non-empty.
//   * Keyboard: ↑↓ moves selection across the *flattened* visible list
//     (wraps); Enter activates the selected hit; ESC closes.
//   * Empty q: shows starter actions from `starterActions` prop (host
//     provides 4 quick-action hits).
//   * Non-empty q with zero hits: shows the contextual "No results"
//     empty state with a "Connect a tool →" hint that fires
//     `onConnectToolHint?.()` when activated.
//   * Entity-hit activation: the embedded <ItemLink> handles navigation
//     via the registry. Enter on a selected entity hit triggers a
//     programmatic click on that ItemLink (so router.navigate flows
//     through the same resolver).
//
// ARIA:
//   * role="dialog" + aria-modal="true" on the scrim.
//   * role="combobox" on the input + aria-controls + aria-expanded.
//   * role="listbox" on the result list.
//   * role="option" on each row (rendered by <PaletteHitRow>).
//   * aria-activedescendant on the input mirrors the selected row's id.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type {
  PaletteHit,
  PaletteHitKind,
  PaletteSearchContext,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";

import type { PaletteSearchPort } from "../ports/PaletteSearchPort";

import { PaletteHitRow } from "./PaletteHitRow";

export interface CommandPaletteProps {
  readonly open: boolean;
  readonly onRequestClose: () => void;
  readonly searchPort: PaletteSearchPort;
  /**
   * Hits to show when the input is empty. Host typically passes 4
   * quick-action hits (sub-PRD §7.3: "Search the team", "Open my todos",
   * "Start a chat", "Open settings"). At most 8 are rendered.
   */
  readonly starterActions: ReadonlyArray<PaletteHit>;
  /** Ranking context forwarded to the port. */
  readonly context?: PaletteSearchContext;
  /** Soft cap on hits per search. Server may clamp further. */
  readonly limit?: number;
  /**
   * Fired when the user clicks the "Connect a tool →" hint in the
   * empty-results state. Optional — when omitted the hint is hidden.
   */
  readonly onConnectToolHint?: () => void;
  /** Debounce window for the search input. Defaults to 150ms. */
  readonly debounceMs?: number;
}

const KIND_ORDER: ReadonlyArray<PaletteHitKind> = [
  "navigation",
  "entity",
  "action",
  "command",
];

const GROUP_LABELS: Readonly<Record<PaletteHitKind, string>> = {
  navigation: "Navigation",
  entity: "Entities",
  action: "Actions",
  command: "Commands",
};

const RESULTS_LIST_ID = "command-palette-results";

export function CommandPalette({
  open,
  onRequestClose,
  searchPort,
  starterActions,
  context,
  limit,
  onConnectToolHint,
  debounceMs = 150,
}: CommandPaletteProps): ReactElement | null {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ReadonlyArray<PaletteHit>>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [hasSearched, setHasSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  // Generation counter so a stale in-flight search doesn't overwrite
  // newer results. Increments on every dispatched query.
  const generationRef = useRef(0);

  // Reset state when the palette opens/closes. We deliberately reset
  // on close too so the next open starts at q="" with starter actions.
  useEffect(() => {
    if (!open) {
      setQuery("");
      setHits([]);
      setSelectedIndex(0);
      setHasSearched(false);
      return;
    }
    // Focus on next tick so the input is in the DOM.
    const handle = setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => clearTimeout(handle);
  }, [open]);

  // Debounced search.
  useEffect(() => {
    if (!open) {
      return;
    }
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      // Empty input: starter actions are shown by the render path
      // (no port call).
      setHits([]);
      setHasSearched(false);
      return;
    }
    const myGen = ++generationRef.current;
    const handle = setTimeout(() => {
      searchPort
        .search({ q: trimmed, context, limit })
        .then((res: PaletteSearchResponse) => {
          if (myGen !== generationRef.current) {
            // A newer query is in flight; discard.
            return;
          }
          setHits(res.hits);
          setHasSearched(true);
          setSelectedIndex(0);
        })
        .catch(() => {
          if (myGen !== generationRef.current) {
            return;
          }
          // Hard failure → empty list + "No results" hint state.
          setHits([]);
          setHasSearched(true);
          setSelectedIndex(0);
        });
    }, debounceMs);
    return () => clearTimeout(handle);
  }, [open, query, searchPort, context, limit, debounceMs]);

  // The flattened, ordered list shown to the user. When q is empty,
  // starter actions; otherwise the port's hits (preserving order).
  const visibleHits = useMemo<ReadonlyArray<PaletteHit>>(() => {
    if (query.trim().length === 0) {
      return starterActions.slice(0, 8);
    }
    return hits;
  }, [query, hits, starterActions]);

  // Bucket hits by kind for group headers (rendering only — selection
  // index runs over the flattened `visibleHits`).
  const grouped = useMemo<
    ReadonlyArray<readonly [PaletteHitKind, ReadonlyArray<PaletteHit>]>
  >(() => {
    const map = new Map<PaletteHitKind, PaletteHit[]>();
    for (const k of KIND_ORDER) {
      map.set(k, []);
    }
    for (const hit of visibleHits) {
      map.get(hit.kind)?.push(hit);
    }
    return KIND_ORDER.map((k) => [k, map.get(k) ?? []] as const).filter(
      ([, list]) => list.length > 0,
    );
  }, [visibleHits]);

  // Keep selection in range as the visible list changes.
  useEffect(() => {
    if (selectedIndex >= visibleHits.length) {
      setSelectedIndex(0);
    }
  }, [visibleHits.length, selectedIndex]);

  const activateHit = useCallback(
    (hit: PaletteHit) => {
      if (hit.kind === "entity" && hit.target !== undefined) {
        // Programmatically click the row's ItemLink so router.navigate
        // flows through the shared registry resolver.
        const row = listRef.current?.querySelector<HTMLElement>(
          `[data-hit-id="${cssEscape(hit.id)}"] [data-testid="item-link"]`,
        );
        if (row !== null && row !== undefined) {
          row.click();
        }
      }
      onRequestClose();
    },
    [onRequestClose],
  );

  // Selected row's DOM id (for aria-activedescendant).
  const selectedRowId = useMemo<string | undefined>(() => {
    const hit = visibleHits[selectedIndex];
    return hit !== undefined ? rowDomId(hit) : undefined;
  }, [visibleHits, selectedIndex]);

  const onInputKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onRequestClose();
        return;
      }
      if (visibleHits.length === 0) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % visibleHits.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex(
          (prev) => (prev - 1 + visibleHits.length) % visibleHits.length,
        );
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const hit = visibleHits[selectedIndex];
        if (hit !== undefined) {
          activateHit(hit);
        }
      }
    },
    [visibleHits, selectedIndex, activateHit, onRequestClose],
  );

  if (!open) {
    return null;
  }

  const isEmptyQuery = query.trim().length === 0;
  const showNoResults =
    !isEmptyQuery && hasSearched && visibleHits.length === 0;

  // Running counter so each rendered row gets a stable index across groups.
  let flatIdx = -1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      style={scrimStyle}
      onClick={onRequestClose}
      data-testid="command-palette"
    >
      <div
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
        data-testid="command-palette-card"
      >
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Search"
          aria-expanded={true}
          aria-controls={RESULTS_LIST_ID}
          aria-autocomplete="list"
          aria-activedescendant={selectedRowId}
          value={query}
          placeholder="Search the team, your work, or run a command…"
          onChange={(event) => {
            setQuery(event.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={onInputKeyDown}
          style={inputStyle}
          data-testid="command-palette-input"
        />
        <ul
          ref={listRef}
          id={RESULTS_LIST_ID}
          role="listbox"
          aria-label="Results"
          style={listStyle}
          data-testid="command-palette-listbox"
        >
          {grouped.map(([kind, list]) => (
            <li key={kind} style={groupStyle} role="presentation">
              <div
                style={groupHeaderStyle}
                data-testid="palette-group-header"
                data-group-kind={kind}
              >
                {GROUP_LABELS[kind]}
              </div>
              <ul style={groupListStyle} role="presentation">
                {list.map((hit) => {
                  flatIdx++;
                  const isSelected = flatIdx === selectedIndex;
                  return (
                    <PaletteHitRow
                      key={hit.id}
                      hit={hit}
                      isSelected={isSelected}
                      id={rowDomId(hit)}
                      onActivate={activateHit}
                      onHover={() => setSelectedIndex(flatIdx)}
                    />
                  );
                })}
              </ul>
            </li>
          ))}
          {showNoResults ? (
            <li
              role="presentation"
              style={emptyStyle}
              data-testid="palette-no-results"
            >
              <div>No results.</div>
              {onConnectToolHint !== undefined ? (
                <button
                  type="button"
                  onClick={onConnectToolHint}
                  style={hintButtonStyle}
                  data-testid="palette-connect-tool-hint"
                >
                  Connect a tool →
                </button>
              ) : null}
            </li>
          ) : null}
          {isEmptyQuery && visibleHits.length === 0 ? (
            <li
              role="presentation"
              style={emptyStyle}
              data-testid="palette-empty"
            >
              <div>Start typing to search.</div>
            </li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function rowDomId(hit: PaletteHit): string {
  return `palette-hit-${hit.id}`;
}

// Tiny CSS.escape polyfill (jsdom doesn't ship CSS.escape in older
// versions, and we only need to defang the `hit_` id prefix's chars).
function cssEscape(value: string): string {
  const css = (globalThis as { CSS?: { escape?: (s: string) => string } }).CSS;
  if (css?.escape !== undefined) {
    return css.escape(value);
  }
  return value.replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const scrimStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  backgroundColor: "rgba(0, 0, 0, 0.45)",
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  paddingTop: "12vh",
  zIndex: 1000,
};

const cardStyle: CSSProperties = {
  width: "min(640px, 92vw)",
  maxHeight: "64vh",
  backgroundColor: "var(--color-surface, #1a1a1a)",
  color: "var(--color-text, #ededee)",
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  boxShadow: "0 24px 48px rgba(0, 0, 0, 0.4)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const inputStyle: CSSProperties = {
  border: "none",
  borderBottom: "1px solid var(--color-border, #2a2a2c)",
  outline: "none",
  padding: "14px 16px",
  fontSize: "var(--font-size-md, 15px)",
  background: "transparent",
  color: "inherit",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 6,
  overflowY: "auto",
};

const groupStyle: CSSProperties = {
  listStyle: "none",
  paddingTop: 4,
};

const groupHeaderStyle: CSSProperties = {
  padding: "6px 12px 2px 12px",
  fontSize: "var(--font-size-xs, 11px)",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--color-text-subtle, #7e7e84)",
};

const groupListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const emptyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 6,
  padding: "12px 16px",
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-sm, 13px)",
};

const hintButtonStyle: CSSProperties = {
  background: "transparent",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  padding: "4px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  cursor: "pointer",
};
