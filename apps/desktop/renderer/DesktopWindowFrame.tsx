import { useEffect, useState, type ReactElement, type ReactNode } from "react";

import type { WindowChromeState } from "../main/window-channels";

const DEFAULT_WINDOW_CHROME_STATE: WindowChromeState = {
  isFullScreen: false,
  hasNativeTrafficLights: false,
};

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
  const [windowChrome, setWindowChrome] = useState<WindowChromeState>(
    DEFAULT_WINDOW_CHROME_STATE,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    return window.bridge?.windowChrome?.subscribe(setWindowChrome);
  }, []);

  return (
    <div
      className="desktop-window-frame"
      data-full-screen={windowChrome.isFullScreen ? "true" : "false"}
      data-native-traffic-lights={
        windowChrome.hasNativeTrafficLights ? "true" : "false"
      }
      data-testid="desktop-window-frame"
      id={id}
    >
      {children}
    </div>
  );
}
