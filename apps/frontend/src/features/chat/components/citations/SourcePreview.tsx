// PR 3.7.2 — hover/focus preview card for citation chips and source rows.
//
// One context, one card portal-mounted at the document root. Triggers
// (chips, row glyphs) call `useSourcePreviewTrigger(source)` to get a
// set of pointer/focus handlers; the context owns the open card and
// debounces open/close so quick pointer movement between chips doesn't
// flap.
//
// Touch devices skip the preview entirely — tapping a chip already
// opens the source URL, and there's no hover affordance to translate.

import type { SourceEntry } from "@enterprise-search/api-types";
import { Badge } from "@enterprise-search/design-system";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DOMAttributes,
  type FocusEvent,
  type PointerEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  humanizeConnector,
  SourceFavicon,
  sourceFreshnessLabel,
} from "@enterprise-search/chat-surface";
import { createPortal } from "react-dom";

const OPEN_DELAY_MS = 200;
const CLOSE_DELAY_MS = 100;
const CARD_WIDTH = 320;
const CARD_MIN_VIEWPORT_GAP = 8;

interface ActiveCard {
  source: SourceEntry;
  anchor: HTMLElement;
}

interface SourcePreviewApi {
  scheduleOpen(anchor: HTMLElement, source: SourceEntry): void;
  cancelOpen(): void;
  scheduleClose(): void;
  cancelClose(): void;
}

const NOOP_API: SourcePreviewApi = {
  scheduleOpen: () => undefined,
  cancelOpen: () => undefined,
  scheduleClose: () => undefined,
  cancelClose: () => undefined,
};

const SourcePreviewContext = createContext<SourcePreviewApi>(NOOP_API);

export interface SourcePreviewProviderProps {
  children: ReactNode;
}

export function SourcePreviewProvider({
  children,
}: SourcePreviewProviderProps): ReactElement {
  const [active, setActive] = useState<ActiveCard | null>(null);
  const openTimerRef = useRef<number | null>(null);
  const closeTimerRef = useRef<number | null>(null);

  const clearOpenTimer = useCallback(() => {
    if (openTimerRef.current !== null) {
      window.clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);
  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  useEffect(
    () => () => {
      clearOpenTimer();
      clearCloseTimer();
    },
    [clearOpenTimer, clearCloseTimer],
  );

  const api = useMemo<SourcePreviewApi>(
    () => ({
      scheduleOpen(anchor, source) {
        clearCloseTimer();
        clearOpenTimer();
        openTimerRef.current = window.setTimeout(() => {
          openTimerRef.current = null;
          setActive({ anchor, source });
        }, OPEN_DELAY_MS);
      },
      cancelOpen() {
        clearOpenTimer();
      },
      scheduleClose() {
        clearOpenTimer();
        clearCloseTimer();
        closeTimerRef.current = window.setTimeout(() => {
          closeTimerRef.current = null;
          setActive(null);
        }, CLOSE_DELAY_MS);
      },
      cancelClose() {
        clearCloseTimer();
      },
    }),
    [clearCloseTimer, clearOpenTimer],
  );

  return (
    <SourcePreviewContext.Provider value={api}>
      {children}
      {active !== null ? (
        <SourcePreviewCard
          active={active}
          onPointerEnter={api.cancelClose}
          onPointerLeave={api.scheduleClose}
          onClose={() => {
            clearOpenTimer();
            clearCloseTimer();
            setActive(null);
          }}
        />
      ) : null}
    </SourcePreviewContext.Provider>
  );
}

interface SourcePreviewCardProps {
  active: ActiveCard;
  onPointerEnter(): void;
  onPointerLeave(): void;
  onClose(): void;
}

function SourcePreviewCard({
  active,
  onPointerEnter,
  onPointerLeave,
  onClose,
}: SourcePreviewCardProps): ReactElement | null {
  const { source, anchor } = active;
  const cardRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<{
    top: number;
    left: number;
  } | null>(null);

  useLayoutEffect(() => {
    const card = cardRef.current;
    if (card === null) {
      return;
    }
    const anchorRect = anchor.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;
    const flipAbove =
      anchorRect.bottom + cardRect.height + CARD_MIN_VIEWPORT_GAP >
      viewportHeight;
    const top = flipAbove
      ? Math.max(
          CARD_MIN_VIEWPORT_GAP,
          anchorRect.top - cardRect.height - CARD_MIN_VIEWPORT_GAP,
        )
      : anchorRect.bottom + CARD_MIN_VIEWPORT_GAP;
    const left = Math.min(
      Math.max(CARD_MIN_VIEWPORT_GAP, anchorRect.left),
      viewportWidth - cardRect.width - CARD_MIN_VIEWPORT_GAP,
    );
    setPosition({ top, left });
  }, [anchor, source.citation_id]);

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (typeof document === "undefined") {
    return null;
  }
  const title = source.title ?? source.source_doc_id;
  const hasUrl =
    typeof source.source_url === "string" && source.source_url.length > 0;
  return createPortal(
    <aside
      ref={cardRef}
      role="dialog"
      aria-label={title}
      className="source-preview-card"
      style={{
        position: "fixed",
        top: position?.top ?? -9999,
        left: position?.left ?? -9999,
        width: CARD_WIDTH,
        visibility: position === null ? "hidden" : "visible",
      }}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <header className="source-preview-card__header">
        <SourceFavicon source={source} size="sm" />
        <span className="source-preview-card__title">
          {hasUrl ? (
            <a href={source.source_url ?? "#"} target="_blank" rel="noreferrer">
              {title}
            </a>
          ) : (
            title
          )}
        </span>
        <Badge tone="neutral">
          {humanizeConnector(source.source_connector)}
        </Badge>
      </header>
      {source.snippet ? (
        <p className="source-preview-card__snippet">{source.snippet}</p>
      ) : null}
      <footer className="source-preview-card__footer">
        {sourceFreshnessLabel({
          connectorSlug: source.source_connector,
          freshnessAt: source.freshness_at,
          lastCitedAt: source.last_cited_at,
        })}
      </footer>
    </aside>,
    document.body,
  );
}

/**
 * Spread the returned props onto a trigger element (chip, row glyph).
 * Returns no-op handlers when the source is undefined or when the
 * device has no hover capability.
 */
export function useSourcePreviewTrigger(
  source: SourceEntry | undefined,
): DOMAttributes<HTMLElement> {
  const api = useContext(SourcePreviewContext);
  return useMemo<DOMAttributes<HTMLElement>>(() => {
    if (source === undefined) {
      return {};
    }
    if (typeof window !== "undefined") {
      const supportsHover = window.matchMedia?.("(hover: hover)").matches;
      if (!supportsHover) {
        return {};
      }
    }
    return {
      onPointerEnter: (event: PointerEvent<HTMLElement>) => {
        api.scheduleOpen(event.currentTarget, source);
      },
      onPointerLeave: () => {
        api.scheduleClose();
      },
      onFocus: (event: FocusEvent<HTMLElement>) => {
        // Open immediately on keyboard focus — no hover delay.
        api.cancelClose();
        api.scheduleOpen(event.currentTarget, source);
      },
      onBlur: () => {
        api.scheduleClose();
      },
    };
  }, [api, source]);
}
