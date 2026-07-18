import {
  useEffect,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

/**
 * Host (web) portal + outside-click adapter for the composer `+`
 * plus-menu popup. Extracted out of `AssistantComposer.tsx` (PR-1.3):
 * the composer shell now lives in `@0x-copilot/chat-surface` and is
 * substrate-agnostic, so this DOM-bound piece (`createPortal` +
 * `window` positioning + `document` outside-click) stays host-side and
 * is injected into the moved core through its `renderPlusMenu` slot.
 *
 * The composer card has ``overflow: hidden`` and a fixed
 * ``--composer-shell-height``, so an absolutely-positioned popup
 * inside the card gets clipped (or worse, overlays the textarea
 * because the card is tall enough to "fit" it). Rendering the popup
 * at ``document.body`` with ``position: fixed`` coords computed from
 * the anchor's bounding rect lets it escape the composer entirely
 * and sit above the card the way every other dropdown in the app
 * does.
 *
 * Mirrors the design-system ``Menu`` primitive's positioning logic
 * (PR 4.4.6 fix) — kept inline here because ``ComposerPlusMenu``
 * isn't built on top of ``Menu`` and rolling its own fixed-position
 * shell beats refactoring its 200-line body.
 *
 * Outside-click dismissal (previously a `document.addEventListener`
 * effect inside the composer) is owned here too: a pointerdown outside
 * the anchor collapses the menu back to its root view via `onDismiss`.
 */
export function AnchoredPlusMenu({
  open,
  anchorRef,
  onDismiss,
  children,
}: {
  open: boolean;
  anchorRef: RefObject<HTMLDivElement | null>;
  onDismiss: () => void;
  children: ReactNode;
}): ReactElement | null {
  const [style, setStyle] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open) return;
    const compute = (): void => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const SPACE = 8;
      setStyle({
        position: "fixed",
        bottom: window.innerHeight - rect.top + SPACE,
        left: rect.left,
        zIndex: 50,
      });
    };
    compute();
    window.addEventListener("resize", compute);
    window.addEventListener("scroll", compute, true);
    return () => {
      window.removeEventListener("resize", compute);
      window.removeEventListener("scroll", compute, true);
    };
  }, [open, anchorRef]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent): void {
      const anchor = anchorRef.current;
      if (anchor && !anchor.contains(event.target as Node)) {
        onDismiss();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, anchorRef, onDismiss]);

  if (!open) return null;
  if (typeof document === "undefined") return null;
  return createPortal(<div style={style}>{children}</div>, document.body);
}
