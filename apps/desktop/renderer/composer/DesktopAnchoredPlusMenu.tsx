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
 * Host (desktop) portal + outside-click adapter for the composer's anchored
 * popups, injected into the shared `AssistantComposer`
 * (@0x-copilot/chat-surface) through its `renderPlusMenu` slot and into
 * `ProjectFilingChip` through its `renderMenu` slot — the two slot argument
 * types are the same four fields, and one anchored-popover implementation for
 * both composer popups is the point.
 *
 * Mirrors the web `AnchoredPlusMenu` (apps/frontend): the composer card has
 * `overflow: hidden`, so an absolutely-positioned popup inside it gets
 * clipped. Rendering at `document.body` with `position: fixed` coords from the
 * anchor's bounding rect lets it escape the card and sit above it. Outside-click
 * (pointerdown outside the anchor) collapses the menu back to its root view via
 * `onDismiss`. The Electron renderer owns `createPortal` / `window` / `document`
 * so this stays host-side, keeping the package substrate-agnostic.
 *
 * `placement` exists because the two anchors are on OPPOSITE sides of the
 * composer. The `+` button is inside the frame and must open UP. The filing
 * chip sits BELOW the frame, so opening up drew the menu straight over the
 * composer's own control row — found by
 * `tools/desktop-journeys/projects-filing`, and invisible to a unit test,
 * which can assert the slot was called but not where the pixels landed.
 * Either preference flips when the preferred side cannot fit the panel, so a
 * short window degrades instead of clipping.
 */
export type AnchoredMenuPlacement = "up" | "down";

const MAX_PANEL_WIDTH = 300;
const SPACE = 8;

/**
 * Fallback panel height for the ONE frame before the portal has been measured.
 * Deliberately small: `.ui-pop__list` clamps itself to 264px and scrolls
 * internally, so a popup never actually needs more than this to be usable, and
 * a large guess makes the fits-check flip menus that would have fitted fine.
 * That is precisely the bug the first cut of `placement` shipped — a 320px
 * assumption against a 135px panel sent the filing menu back over the composer.
 */
const MIN_USABLE_PANEL_HEIGHT = 140;

export function DesktopAnchoredPlusMenu({
  open,
  anchorRef,
  onDismiss,
  children,
  placement = "up",
}: {
  open: boolean;
  anchorRef: RefObject<HTMLDivElement | null>;
  onDismiss: () => void;
  children: ReactNode;
  placement?: AnchoredMenuPlacement;
}): ReactElement | null {
  const [style, setStyle] = useState<CSSProperties>({});
  const panelRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const compute = (): void => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const roomAbove = rect.top - SPACE;
      const roomBelow = window.innerHeight - rect.bottom - SPACE;
      // Measure the REAL panel — the portal is committed before layout effects
      // run, so this is available on the first pass. Only the unmeasured frame
      // falls back to a constant.
      const panelHeight =
        panelRef.current?.offsetHeight || MIN_USABLE_PANEL_HEIGHT;
      // Honour the caller's side unless it genuinely cannot hold the panel AND
      // the other side has more room — a flip that gains nothing only moves the
      // problem to the opposite edge.
      const needed = Math.min(panelHeight, MIN_USABLE_PANEL_HEIGHT);
      const opensUp =
        placement === "up"
          ? roomAbove >= needed || roomAbove >= roomBelow
          : roomBelow < needed && roomAbove > roomBelow;
      setStyle({
        position: "fixed",
        ...(opensUp
          ? { bottom: window.innerHeight - rect.top + SPACE }
          : { top: rect.bottom + SPACE }),
        // The original left-only position could place a 300px menu beyond a
        // narrow cockpit's edge. Keep the anchor alignment where it fits, then
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
  }, [open, anchorRef, placement]);

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
