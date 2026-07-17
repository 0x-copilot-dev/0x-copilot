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
//  - Click navigation goes through `resolveItemRef(ref)` → the resolved
//    `ArtifactRoute`, which the host hands to the Router. We do this
//    inside the port (rather than passing a Router in) because the
//    Router's TRoute type parameter is host-specific; the registry
//    resolver returns the substrate-portable `ArtifactRoute` instead.

import {
  resolveItemRef,
  type NotificationPort,
  type NotifyPayload,
} from "@0x-copilot/chat-surface";
import type { ArtifactRoute } from "@0x-copilot/chat-surface";

/**
 * Caller hands in a `navigate` closure so the port stays decoupled from
 * the host's wider Router<AppRoute> instantiation — the port speaks the
 * substrate-portable `ArtifactRoute` shape, the host knows how to widen
 * that into its app route union. Same pattern the chat-surface's
 * `<ItemLink>` follows.
 */
export interface WebNotificationPortConfig {
  readonly navigate: (route: ArtifactRoute) => void;
}

export class WebNotificationPort implements NotificationPort {
  readonly #navigate: (route: ArtifactRoute) => void;

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
        // Defer the navigate so the focus / blur dance the OS does when
        // the user clicks a notification settles before the route swap.
        void resolveItemRef(ref)
          .then((resolved) => {
            if (resolved === null) return;
            const route = resolved.route;
            if (route === null) return;
            this.#navigate(route);
          })
          .catch(() => {
            // Resolver failures are non-fatal — the click is best-effort.
          });
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
