import type { ReactElement, ReactNode } from "react";

/**
 * The renderer-owned visual frame for the desktop window's web contents.
 *
 * Electron owns native controls and the OS shadow; this component owns the
 * application-side clipping, border, and surface that make the hidden-inset
 * titlebar a coherent desktop window. Keeping this in the desktop host (rather
 * than chat-surface) prevents web and embedded hosts from inheriting desktop
 * chrome.
 */
export function DesktopWindowFrame({
  children,
  id,
}: {
  readonly children: ReactNode;
  /** Optional only for stable host-level DOM ownership (for example, tests). */
  readonly id?: string;
}): ReactElement {
  return (
    <div
      className="desktop-window-frame"
      data-testid="desktop-window-frame"
      id={id}
    >
      {children}
    </div>
  );
}
