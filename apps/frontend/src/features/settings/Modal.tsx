// Feature-local <Modal> for the Settings panels.
//
// Deliberately minimal: portal + overlay + Esc-to-close + click-outside,
// preserves focus trap by focusing the first interactive descendant. The
// design-system promotion (`<Dialog>` wrapping @radix-ui/react-dialog) is
// PR 4.4's surface; until then this avoids adding a new dep tree from a
// non-modal-heavy PR.
//
// Used by: <InviteModal>, danger-zone confirmation, future small overlays.

import { useEffect, useRef, type ReactElement, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "@enterprise-search/design-system";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  /** Optional footer slot rendered with default spacing. */
  footer?: ReactNode;
  /** Close (X) button label. Defaults to "Close". */
  closeLabel?: string;
  /** A hint id used for `aria-describedby`. */
  describedById?: string;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  closeLabel = "Close",
  describedById,
}: ModalProps): ReactElement | null {
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Land focus on the first focusable descendant.
    const handle = window.requestAnimationFrame(() => {
      const focusable = contentRef.current?.querySelector<HTMLElement>(
        'input, textarea, button, [href], select, [tabindex]:not([tabindex="-1"])',
      );
      focusable?.focus();
    });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.cancelAnimationFrame(handle);
    };
  }, [open, onClose]);

  if (!open) return null;
  if (typeof document === "undefined") return null;

  return createPortal(
    <div className="settings-modal-overlay" onMouseDown={onClose}>
      <div
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-describedby={describedById}
        className="settings-modal"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="settings-modal__head">
          <div>
            <h3>{title}</h3>
            {description ? (
              <p id={describedById} className="settings-modal__description">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton
            type="button"
            aria-label={closeLabel}
            title={closeLabel}
            onClick={onClose}
          >
            ×
          </IconButton>
        </header>
        <div className="settings-modal__body">{children}</div>
        {footer ? (
          <footer className="settings-modal__foot">{footer}</footer>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
