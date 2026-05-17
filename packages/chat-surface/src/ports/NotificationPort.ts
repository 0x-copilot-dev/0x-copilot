// NotificationPort — native notifications scoped to a destination.
// Source: cross-audit.md §1.2 (binding 2026-05-17).
//
// Substrate-agnostic contract: destinations call `notify(...)` directly;
// the web no-op (when permission not granted) and the desktop native
// dispatcher both satisfy this interface. No `window.Notification`
// reference lives in this file — the contract describes behavior, not
// the underlying API.

import type { ItemRef } from "@enterprise-search/api-types";

import type { ShellDestinationSlug } from "../shell/destinations";

export interface NotifyPayload {
  readonly title: string;
  readonly body: string;
  readonly destination: ShellDestinationSlug;
  /** Optional click-target. When the user activates the notification,
   *  the host navigates to this ref via the registry resolver. */
  readonly ref?: ItemRef;
  readonly priority?: "low" | "med" | "high";
}

export interface NotificationPort {
  /**
   * Show a native notification.
   *
   * Web substrate: no-op when permission isn't granted; otherwise
   *   `new Notification(...)` with click → router.navigate(ref).
   * Desktop substrate: OS notification (NSUserNotification / Win10
   *   ToastNotification / libnotify on Linux) with the same click
   *   binding.
   */
  notify(payload: NotifyPayload): void;

  /**
   * Whether the substrate can currently show native notifications
   * (permission granted AND substrate supports it). Destinations
   * gate UX hints on this (e.g. "Enable notifications" toast).
   */
  isAvailable(): boolean;

  /**
   * Web only: prompt the user for permission. Returns the resulting
   * state. Desktop substrate omits this (permission is granted at
   * install time) — destinations check for the method's presence with
   * a strict `!== undefined` guard.
   */
  requestPermission?(): Promise<"granted" | "denied" | "default">;
}
