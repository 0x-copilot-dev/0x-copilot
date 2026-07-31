/**
 * Main → preload window-chrome snapshot.
 *
 * This channel is intentionally app-local rather than part of chat-transport:
 * native window state is a desktop substrate concern, not an agent transport
 * capability.
 */
export const WINDOW_CHROME_STATE_CHANNEL = "window.chrome-state";

export interface WindowChromeState {
  readonly isFullScreen: boolean;
  readonly hasNativeTrafficLights: boolean;
}

export function isWindowChromeState(
  value: unknown,
): value is WindowChromeState {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<WindowChromeState>;
  return (
    typeof candidate.isFullScreen === "boolean" &&
    typeof candidate.hasNativeTrafficLights === "boolean"
  );
}
