import type {
  NotificationChannel,
  NotificationEvent,
} from "@0x-copilot/api-types";
import { Card, Switch } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { useUserPreferences } from "../../me/useUserPreferences";
import { NotificationsV2Panel } from "./NotificationsV2Panel";

const EVENT_LABELS: Record<NotificationEvent, { label: string; hint: string }> =
  {
    mention: {
      label: "@-mention",
      hint: "Someone @-mentions me in a chat or approval card.",
    },
    approval_needed: {
      label: "Approval needed",
      hint: "An action is queued for me to approve before it runs.",
    },
    run_finished: {
      label: "Run finished",
      hint: "A long-running agent finishes (success, failure, cancelled).",
    },
    weekly_digest: {
      label: "Weekly digest",
      hint: "Mondays — what your workspace shipped last week.",
    },
  };

const EVENT_ORDER: ReadonlyArray<NotificationEvent> = [
  "mention",
  "approval_needed",
  "run_finished",
  "weekly_digest",
];

const CHANNEL_LABELS: Record<NotificationChannel, string> = {
  email: "Email",
  slack: "Slack",
  desktop: "Desktop",
};

const CHANNEL_ORDER: ReadonlyArray<NotificationChannel> = [
  "email",
  "slack",
  "desktop",
];

/**
 * Settings → You → Notifications.
 *
 * 4-event × 3-channel matrix. v1 ships **storage + UI**; the senders
 * (email / Slack / desktop) read this matrix when they ship in a
 * follow-up PR. Toggling a cell saves immediately (no batch button)
 * because each cell is a single boolean — no cancel-and-discard
 * intent the user might want.
 */
export function Notifications(): ReactElement {
  const preferences = useUserPreferences();
  const data = preferences.data;

  if (preferences.loading && data === null) {
    return (
      <div className="settings-section">
        <h2>Notifications</h2>
        <Card>
          <p>Loading preferences…</p>
        </Card>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="settings-section">
        <h2>Notifications</h2>
        <Card>
          <p>{preferences.error ?? "Preferences are unavailable right now."}</p>
        </Card>
      </div>
    );
  }

  const matrix = data.notifications.matrix;

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Notifications</h2>
          <p>
            Per event × per channel. Senders pick this up automatically when
            they roll out — Slack and desktop arrive after email.
          </p>
        </div>
      </div>

      <Card>
        <div className="me-notifications-grid" role="grid">
          <div className="me-notifications-grid__head" role="row">
            <span className="me-notifications-grid__label" role="columnheader">
              Event
            </span>
            {CHANNEL_ORDER.map((channel) => (
              <span
                key={channel}
                role="columnheader"
                className="me-notifications-grid__channel"
              >
                {CHANNEL_LABELS[channel]}
              </span>
            ))}
          </div>

          {EVENT_ORDER.map((event) => {
            const row = matrix[event] ?? {};
            const meta = EVENT_LABELS[event];
            return (
              <div
                key={event}
                role="row"
                className="me-notifications-grid__row"
              >
                <div className="me-notifications-grid__event" role="rowheader">
                  <strong>{meta.label}</strong>
                  <small>{meta.hint}</small>
                </div>
                {CHANNEL_ORDER.map((channel) => {
                  const checked = Boolean(row[channel]);
                  return (
                    <div
                      key={channel}
                      role="gridcell"
                      className="me-notifications-grid__cell"
                    >
                      <Switch
                        label=""
                        checked={checked}
                        aria-label={`${meta.label} via ${CHANNEL_LABELS[channel]}`}
                        onChange={(e) =>
                          void preferences.save({
                            notifications: {
                              matrix: {
                                [event]: { [channel]: e.target.checked },
                              },
                            },
                          })
                        }
                      />
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        {preferences.error ? (
          <p className="app-error">{preferences.error}</p>
        ) : null}
      </Card>

      <NotificationsV2Panel />
    </div>
  );
}
