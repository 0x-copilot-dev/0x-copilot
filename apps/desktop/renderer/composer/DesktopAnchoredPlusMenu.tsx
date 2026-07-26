import {
  useEffect,
  useLayoutEffect,
  useRef,
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
  const panelRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const compute = (): void => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const SPACE = 8;
      const MAX_PANEL_WIDTH = 300;
      setStyle({
        position: "fixed",
        bottom: window.innerHeight - rect.top + SPACE,
        // The original left-only position could place a 300px menu beyond a
        // narrow cockpit's edge. Keep the `+` alignment where it fits, then
        // clamp to the visible desktop viewport.
        left: Math.min(
          Math.max(SPACE, rect.left),
          Math.max(SPACE, window.innerWidth - MAX_PANEL_WIDTH - SPACE),
        ),
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
      const target = event.target as Node;
      // The menu is portaled to document.body, so it is not a descendant of
      // its anchor. Treating every panel click as outside dismissed the menu
      // on pointerdown before its row's click handler could run.
      if (
        anchor &&
        !anchor.contains(target) &&
        !panelRef.current?.contains(target)
      ) {
        onDismiss();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, anchorRef, onDismiss]);

  if (!open) return null;
  if (typeof document === "undefined") return null;
  return createPortal(
    <div ref={panelRef} style={style} data-testid="desktop-anchored-plus-menu">
      {children}
    </div>,
    document.body,
  );
}
