// Web NotificationPort — wraps `window.Notification`.
//
// Source: cross-audit.md §1.2 + chats-canvas-prd §5.4. The contract:
//
//  - `isAvailable()` reflects the current permission state, NOT whether
//    the browser supports the API. Destinations gate UX hints (e.g.
//    "Enable notifications" toast) on the permission state, not on
//    feature detection.
//  - `notify(payload)` is a no-op when the substrate has no `Notification`
//    constructor (older browsers, jsdom) or when permission is not
//    "granted" — matches §1.2 "no-op when permission not granted".
//  - `requestPermission()` is web-only and prompts the user; desktop
//    implementations omit it (permission is granted at install time).
//  - Click navigation goes through the ItemRoute registry: `hasItemRoute` +
//    `resolveItemRoute(ref)` synchronously yield the HOST route (an `AppRoute`
//    on web, PRD-04 Seam B), which the caller hands to the Router. The route is
//    typed `unknown` here because it belongs to the host's own union; the
//    caller casts it back.

import {
  hasItemRoute,
  resolveItemRoute,
  type NotificationPort,
  type NotifyPayload,
} from "@0x-copilot/chat-surface";

/**
 * Caller hands in a `navigate` closure so the port stays decoupled from the
 * host's Router union. The registry resolves an `ItemRef` to a HOST route
 * (`unknown` here); the caller's closure casts it back to its own route union
 * (`AppRoute` on web) and navigates. Same pattern chat-surface's `<ItemLink>`
 * follows.
 */
export interface WebNotificationPortConfig {
  readonly navigate: (route: unknown) => void;
}

export class WebNotificationPort implements NotificationPort {
  readonly #navigate: (route: unknown) => void;

  constructor(config: WebNotificationPortConfig) {
    this.#navigate = config.navigate;
  }

  isAvailable(): boolean {
    if (typeof globalThis === "undefined") return false;
    const Ctor = (globalThis as { Notification?: typeof Notification })
      .Notification;
    if (Ctor === undefined) return false;
    return Ctor.permission === "granted";
  }

  notify(payload: NotifyPayload): void {
    if (!this.isAvailable()) return;
    const Ctor = (globalThis as { Notification?: typeof Notification })
      .Notification;
    if (Ctor === undefined) return;
    const native = new Ctor(payload.title, {
      body: payload.body,
      tag: payload.destination,
    });
    const ref = payload.ref;
    if (ref !== undefined) {
      native.onclick = (): void => {
        // No registered route for this kind → nothing to navigate to (the
        // notification is still shown; the click is just inert).
        if (!hasItemRoute(ref.kind)) return;
        let route: unknown;
        try {
          route = resolveItemRoute(ref);
        } catch {
          // Resolver failures are non-fatal — the click is best-effort.
          return;
        }
        if (route === null || route === undefined) return;
        this.#navigate(route);
      };
    }
  }

  async requestPermission(): Promise<"granted" | "denied" | "default"> {
    if (typeof globalThis === "undefined") return "denied";
    const Ctor = (globalThis as { Notification?: typeof Notification })
      .Notification;
    if (Ctor === undefined) return "denied";
    return await Ctor.requestPermission();
  }
}
