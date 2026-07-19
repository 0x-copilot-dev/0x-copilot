// Renders the NotificationCenter's active notifications as a fixed toast stack.
// Reuses the existing `Toast` primitive (no fourth toast component). Mounted once
// per host, high in the tree, so it floats above the full-bleed Run/Chats surfaces.

import { type ReactElement } from "react";

import {
  useNotificationCenter,
  type AppNotification,
  type NotifyTone,
} from "../providers/NotificationCenterProvider";
import { Toast, type ToastTone } from "../settings/SaveBar";

const MAX_VISIBLE = 3;

function toneToToast(tone: NotifyTone): ToastTone {
  switch (tone) {
    case "error":
      return "danger";
    case "success":
      return "success";
    default:
      return "info";
  }
}

function NotificationMessage({
  notification,
  onAction,
}: {
  readonly notification: AppNotification;
  readonly onAction: () => void;
}): ReactElement {
  const { title, body, action } = notification;
  return (
    <span
      style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
    >
      <strong style={{ fontWeight: 600 }}>{title}</strong>
      {body !== undefined && body !== "" ? (
        <span
          style={{ color: "var(--color-text-muted)", wordBreak: "break-word" }}
        >
          {body}
        </span>
      ) : null}
      {action !== undefined ? (
        <button
          type="button"
          onClick={onAction}
          data-testid="notification-toast-action"
          style={{
            alignSelf: "flex-start",
            marginTop: 2,
            padding: 0,
            border: "none",
            background: "none",
            color: "var(--color-accent)",
            font: "inherit",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {action.label}
        </button>
      ) : null}
    </span>
  );
}

export function ToastStack(): ReactElement | null {
  const { notifications, dismiss } = useNotificationCenter();
  if (notifications.length === 0) return null;

  // Newest last in state → show the newest MAX_VISIBLE, newest on top.
  const shown = notifications.slice(-MAX_VISIBLE).reverse();
  const overflow = notifications.length - shown.length;

  return (
    <div
      data-testid="notification-toast-stack"
      role="region"
      aria-label="Notifications"
      style={{
        position: "fixed",
        top: "var(--space-lg, 16px)",
        right: "var(--space-lg, 16px)",
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm, 8px)",
        maxWidth: "min(92vw, 380px)",
        pointerEvents: "none",
      }}
    >
      {shown.map((n) => (
        <div key={n.id} style={{ pointerEvents: "auto" }}>
          <Toast
            open
            tone={toneToToast(n.tone)}
            onDismiss={() => dismiss(n.id)}
            dismissLabel="Dismiss"
            message={
              <NotificationMessage
                notification={n}
                onAction={() => {
                  n.action?.onClick();
                  dismiss(n.id);
                }}
              />
            }
          />
        </div>
      ))}
      {overflow > 0 ? (
        <span
          data-testid="notification-toast-overflow"
          style={{
            alignSelf: "flex-end",
            fontSize: "var(--font-size-xs, 12px)",
            color: "var(--color-text-muted)",
          }}
        >
          +{overflow} more
        </span>
      ) : null}
    </div>
  );
}
