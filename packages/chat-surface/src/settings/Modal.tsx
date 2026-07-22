// Settings flow chrome — <Modal> + <StepDots> (DESIGN-SPEC §5).
//
// `.scrim` + `.modal` (500px): head (logo + title + mono subtitle + close ×),
// body, foot (StepDots + actions). Drives the multi-step BYOK "Add provider
// key" and "Download local model" flows built in PR-5.4 / PR-5.5.
//
// Substrate-agnostic (chat-surface boundary, see packages/chat-surface/
// eslint.config.js): NO bare `window`/`document`/`createPortal`. The dialog
// renders as an inline fixed overlay (same idiom as
// destinations/projects/transfer-ownership-dialog.tsx) so it needs no portal
// container, ESC is a React `onKeyDown` (not `window.addEventListener`), focus
// capture/restore reads `containerRef.current.ownerDocument` (a member access,
// not the banned bare `document` global), and reduce-motion is inherited from
// the design-system `[data-reduce-motion]` root rule.
//
// Pure presentation. Colors resolve ONLY to design-system v2 tokens
// (var(--color-*)); no hard-coded hex. Dimensions that DESIGN-SPEC §0 pins
// (modal 500px) are numeric literals.

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from "react";

import { IconButton } from "@0x-copilot/design-system";

// DESIGN-SPEC §0: Modal width 500px.
export const MODAL_WIDTH = 500;

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

// ---------------------------------------------------------------------------
// StepDots — DESIGN-SPEC §5 ("3 StepDots"). Renders `total` dots reflecting
// the 1-based `current` step: done (< current) / active (=== current) /
// future (> current). Announced as "Step N of M" on the group.
// ---------------------------------------------------------------------------

export interface StepDotsProps {
  /** Total number of steps in the flow (e.g. 3 for Add-provider-key). */
  readonly total: number;
  /** 1-based index of the active step. Clamped into [1, total]. */
  readonly current: number;
  readonly className?: string;
}

const stepDotsRow: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};

function stepDotStyle(state: "done" | "active" | "future"): CSSProperties {
  return {
    // Design step dots — 5px dots, 16px active pill.
    width: state === "active" ? 16 : 5,
    height: 5,
    borderRadius: "var(--radius-full)",
    transition: "width var(--duration-fast) var(--ease-standard)",
    backgroundColor:
      state === "future" ? "var(--color-border-strong)" : "var(--color-accent)",
    opacity: state === "done" ? 0.55 : 1,
  };
}

export function StepDots({
  total,
  current,
  className,
}: StepDotsProps): ReactElement {
  const clamped = Math.min(Math.max(current, 1), Math.max(total, 1));
  return (
    <div
      role="group"
      aria-label={`Step ${clamped} of ${total}`}
      className={className}
      style={stepDotsRow}
      data-testid="step-dots"
    >
      {Array.from({ length: total }, (_, i) => {
        const index = i + 1;
        const state =
          index < clamped ? "done" : index === clamped ? "active" : "future";
        return (
          <span
            key={index}
            aria-hidden="true"
            data-state={state}
            style={stepDotStyle(state)}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modal — focus-trapped dialog with ESC-close, backdrop-close, and
// return-focus-to-trigger. Head (logo + title + mono subtitle + ×) / body /
// optional foot.
// ---------------------------------------------------------------------------

export interface ModalProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly title: string;
  /** Mono subtitle beneath the title (DESIGN-SPEC §5 flow head). */
  readonly subtitle?: string;
  /** Optional leading logo/glyph in the head (neutralized to monochrome by
   *  the caller — single-accent discipline). */
  readonly logo?: ReactNode;
  /** Modal body. */
  readonly children: ReactNode;
  /** Foot slot — typically <StepDots> + action buttons. */
  readonly footer?: ReactNode;
  /** Overrides the DESIGN-SPEC 500px width (rarely needed). */
  readonly width?: number;
  /** Accessible label for the close (×) control. */
  readonly closeLabel?: string;
  /** Element to focus first when the modal opens; defaults to the first
   *  focusable descendant. */
  readonly initialFocusRef?: RefObject<HTMLElement | null>;
}

const scrimStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  // The design system now owns the scrim (design `.scrim`, copilot.css:2223).
  // No fallback: a `var(--x, …)` on a token that exists is dead code that would
  // hide the next regression instead of surfacing it.
  backgroundColor: "var(--color-scrim)",
  backdropFilter: "blur(var(--blur-scrim))",
  WebkitBackdropFilter: "blur(var(--blur-scrim))",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 16,
  zIndex: 1100,
};

const headStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 12,
  padding: "16px 18px",
  borderBottom: "1px solid var(--color-border)",
};

const logoStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface-muted)",
  color: "var(--color-text-muted)",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display)",
  // Design modal title = 13.5px ≈ --font-size-sm (was --font-size-lg 16px).
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "-0.01em",
  color: "var(--color-text)",
};

const subtitleStyle: CSSProperties = {
  margin: "2px 0 0",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
};

const bodyStyle: CSSProperties = {
  // Design modal body pad = 15px (was 18px).
  padding: 15,
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
  lineHeight: "var(--line-height-base)",
};

const footStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  // Design modal foot = 12px/15px pad on the --ink2 inset ground.
  padding: "12px 15px",
  borderTop: "1px solid var(--color-border)",
  backgroundColor: "var(--color-bg-elevated)",
};

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  logo,
  children,
  footer,
  width = MODAL_WIDTH,
  closeLabel = "Close",
  initialFocusRef,
}: ModalProps): ReactElement | null {
  const cardRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const reactId = useId();
  const titleId = `${reactId}-title`;
  const subtitleId = subtitle !== undefined ? `${reactId}-subtitle` : undefined;

  // Capture the trigger, move focus into the modal on open, restore on close.
  useEffect(() => {
    if (!open) return;
    const card = cardRef.current;
    if (card === null) return;
    const owner = card.ownerDocument;
    restoreFocusRef.current =
      (owner.activeElement as HTMLElement | null) ?? null;

    const target =
      initialFocusRef?.current ??
      card.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ??
      card;
    // Focus after mount; card is always focusable via tabIndex=-1 fallback.
    target.focus();

    const toRestore = restoreFocusRef.current;
    return () => {
      toRestore?.focus?.();
    };
  }, [open, initialFocusRef]);

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const card = cardRef.current;
      if (card === null) return;
      const focusable = Array.from(
        card.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      );
      if (focusable.length === 0) {
        event.preventDefault();
        card.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = card.ownerDocument.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose],
  );

  if (!open) return null;

  return (
    <div
      style={scrimStyle}
      data-testid="settings-modal-scrim"
      onKeyDown={handleKeyDown}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitleId}
        tabIndex={-1}
        data-testid="settings-modal"
        style={{
          width,
          maxWidth: "calc(100vw - 32px)",
          maxHeight: "calc(100vh - 32px)",
          display: "flex",
          flexDirection: "column",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border-strong)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-soft)",
          overflow: "hidden",
          outline: "none",
        }}
      >
        <header style={headStyle}>
          {logo !== undefined ? (
            <span style={logoStyle} aria-hidden="true">
              {logo}
            </span>
          ) : null}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 id={titleId} style={titleStyle}>
              {title}
            </h2>
            {subtitle !== undefined ? (
              <p id={subtitleId} style={subtitleStyle}>
                {subtitle}
              </p>
            ) : null}
          </div>
          <IconButton
            type="button"
            aria-label={closeLabel}
            title={closeLabel}
            onClick={onClose}
            data-testid="settings-modal-close"
          >
            ×
          </IconButton>
        </header>
        <div style={bodyStyle}>{children}</div>
        {footer !== undefined ? (
          <footer style={footStyle}>{footer}</footer>
        ) : null}
      </div>
    </div>
  );
}
