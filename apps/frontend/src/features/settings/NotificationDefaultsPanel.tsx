// NotificationDefaultsPanel — Phase 12 notification-defaults page
// (sub-PRD §7.4 / §4.4). Renders per-user defaults on top + an admin
// workspace-defaults tab below (read-only for non-admin callers).
//
// Until the P12-B4 chat-surface settings shell lands, this is the
// minimal host-side panel that consumes `settingsApi` directly.

import {
  useEffect,
  useState,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type {
  NotificationDefaults,
  WorkspaceNotificationDefaults,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  getUserNotificationDefaults,
  getWorkspaceNotificationDefaults,
  patchUserNotificationDefaults,
  patchWorkspaceNotificationDefaults,
} from "../../api/settingsApi";
import { errorMessage } from "../../utils/errors";

interface NotificationDefaultsPanelProps {
  readonly identity: RequestIdentity;
  readonly isAdmin: boolean;
}

type State<T> =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly value: T };

export function NotificationDefaultsPanel({
  identity,
  isAdmin,
}: NotificationDefaultsPanelProps): ReactElement {
  const [user, setUser] = useState<State<NotificationDefaults>>({
    kind: "loading",
  });
  const [ws, setWs] = useState<State<WorkspaceNotificationDefaults>>({
    kind: "loading",
  });
  const [pendingError, setPendingError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUserNotificationDefaults(identity)
      .then((value) => {
        if (!cancelled) setUser({ kind: "ready", value });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setUser({
            kind: "error",
            message: errorMessage(err, "Could not load notification defaults."),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  useEffect(() => {
    if (!isAdmin) {
      setWs({
        kind: "error",
        message: "Workspace defaults are admin-only.",
      });
      return;
    }
    let cancelled = false;
    getWorkspaceNotificationDefaults(identity)
      .then((value) => {
        if (!cancelled) setWs({ kind: "ready", value });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setWs({
            kind: "error",
            message: errorMessage(err, "Could not load workspace defaults."),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [identity, isAdmin]);

  async function handleUserToggle(
    destination: string,
    e: ChangeEvent<HTMLInputElement>,
  ): Promise<void> {
    setPendingError(null);
    if (user.kind !== "ready") return;
    const nextEnabled = e.target.checked;
    try {
      const value = await patchUserNotificationDefaults(identity, {
        destinations_enabled: {
          ...user.value.destinations_enabled,
          [destination]: nextEnabled,
        },
      });
      setUser({ kind: "ready", value });
    } catch (err) {
      setPendingError(errorMessage(err, "Could not save notification toggle."));
    }
  }

  async function handleWorkspaceToggle(
    destination: string,
    e: ChangeEvent<HTMLInputElement>,
  ): Promise<void> {
    setPendingError(null);
    if (ws.kind !== "ready" || !isAdmin) return;
    const nextEnabled = e.target.checked;
    try {
      const value = await patchWorkspaceNotificationDefaults(identity, {
        destinations_enabled: {
          ...ws.value.destinations_enabled,
          [destination]: nextEnabled,
        },
      });
      setWs({ kind: "ready", value });
    } catch (err) {
      setPendingError(
        errorMessage(err, "Could not save workspace notification toggle."),
      );
    }
  }

  return (
    <section
      aria-label="Notification defaults"
      data-testid="notification-defaults-panel"
    >
      <h2 style={{ margin: "0 0 12px 0" }}>Notification defaults</h2>
      {pendingError !== null && (
        <div
          role="status"
          data-testid="notification-defaults-pending-error"
          style={pendingErrorStyle}
        >
          {pendingError}
        </div>
      )}
      <section
        data-testid="notification-defaults-user"
        data-state={user.kind}
        style={blockStyle}
      >
        <h3 style={subhStyle}>Personal defaults</h3>
        {user.kind === "loading" ? (
          <div data-testid="notification-defaults-user-loading">Loading…</div>
        ) : user.kind === "error" ? (
          <div role="alert" data-testid="notification-defaults-user-error">
            {user.message}
          </div>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {Object.entries(user.value.destinations_enabled).map(
              ([dest, enabled]) => (
                <li
                  key={dest}
                  data-testid="notification-defaults-user-row"
                  data-destination={dest}
                  style={{ padding: "6px 0" }}
                >
                  <label style={{ display: "flex", gap: 8, fontSize: 13 }}>
                    <input
                      type="checkbox"
                      data-testid="notification-defaults-user-toggle"
                      data-destination={dest}
                      checked={enabled}
                      onChange={(e) => {
                        void handleUserToggle(dest, e);
                      }}
                    />
                    <span>{dest}</span>
                  </label>
                </li>
              ),
            )}
          </ul>
        )}
      </section>
      <section
        data-testid="notification-defaults-workspace"
        data-state={ws.kind}
        style={blockStyle}
      >
        <h3 style={subhStyle}>
          Workspace defaults {isAdmin ? "" : " (admin-only)"}
        </h3>
        {ws.kind === "loading" ? (
          <div data-testid="notification-defaults-workspace-loading">
            Loading…
          </div>
        ) : ws.kind === "error" ? (
          <div role="alert" data-testid="notification-defaults-workspace-error">
            {ws.message}
          </div>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {Object.entries(ws.value.destinations_enabled).map(
              ([dest, enabled]) => (
                <li
                  key={dest}
                  data-testid="notification-defaults-workspace-row"
                  data-destination={dest}
                  style={{ padding: "6px 0" }}
                >
                  <label style={{ display: "flex", gap: 8, fontSize: 13 }}>
                    <input
                      type="checkbox"
                      data-testid="notification-defaults-workspace-toggle"
                      data-destination={dest}
                      checked={enabled}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        void handleWorkspaceToggle(dest, e);
                      }}
                    />
                    <span>{dest}</span>
                  </label>
                </li>
              ),
            )}
          </ul>
        )}
      </section>
    </section>
  );
}

const blockStyle = {
  marginTop: 16,
  paddingTop: 12,
  borderTop: "1px solid var(--color-border)",
} as const;

const subhStyle = {
  margin: "0 0 8px 0",
  fontSize: 14,
  fontWeight: 600,
} as const;

const pendingErrorStyle = {
  marginBottom: 12,
  padding: 12,
  border: "1px solid var(--color-border-strong)",
  borderRadius: 8,
  background: "var(--color-surface)",
  fontSize: 13,
} as const;
