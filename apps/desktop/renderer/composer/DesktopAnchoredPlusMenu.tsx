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
 * Host (desktop) portal + outside-click adapter for the composer `+`
 * plus-menu popup, injected into the shared `AssistantComposer`
 * (@0x-copilot/chat-surface) through its `renderPlusMenu` slot.
 *
 * Mirrors the web `AnchoredPlusMenu` (apps/frontend): the composer card has
 * `overflow: hidden`, so an absolutely-positioned popup inside it gets
 * clipped. Rendering at `document.body` with `position: fixed` coords from the
 * anchor's bounding rect lets it escape the card and sit above it. Outside-click
 * (pointerdown outside the anchor) collapses the menu back to its root view via
 * `onDismiss`. The Electron renderer owns `createPortal` / `window` / `document`
 * so this stays host-side, keeping the package substrate-agnostic.
 */
export function DesktopAnchoredPlusMenu({
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
