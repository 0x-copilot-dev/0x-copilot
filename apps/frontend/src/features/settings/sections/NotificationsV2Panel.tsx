// PR B4 / 8.0.3e — typed notification preferences + quiet hours panel.
//
// Lives alongside the legacy v1 matrix in ``Notifications.tsx`` until
// the dispatcher cuts over. The v2 shape adds two new event kinds
// (connector_error, product_updates) and a third channel (push) plus
// the quiet-hours card. Reads + writes through
// ``/v1/me/notifications`` via the facade.

import type {
  NotificationChannelV2,
  NotificationEventKind,
  NotificationPreferenceEntry,
  NotificationPreferencesResponse,
} from "@enterprise-search/api-types";
import { Card, Field, Switch } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  getMyNotificationPreferences,
  updateMyNotificationPreferences,
} from "../../../api/meApi";
import { errorMessage } from "../../../utils/errors";

const EVENT_ORDER: ReadonlyArray<NotificationEventKind> = [
  "long_task_finished",
  "approval_requested",
  "mention",
  "connector_error",
  "weekly_digest",
  "product_updates",
];

const CHANNEL_ORDER: ReadonlyArray<NotificationChannelV2> = [
  "in_app",
  "email",
  "push",
];

const EVENT_LABELS: Record<NotificationEventKind, string> = {
  long_task_finished: "Long task finished",
  approval_requested: "Approval needed",
  mention: "@-mention",
  connector_error: "Connector error",
  weekly_digest: "Weekly digest",
  product_updates: "Product updates",
};

const CHANNEL_LABELS: Record<NotificationChannelV2, string> = {
  in_app: "In-app",
  email: "Email",
  push: "Push",
};

export function NotificationsV2Panel(): ReactElement {
  const [snapshot, setSnapshot] =
    useState<NotificationPreferencesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyNotificationPreferences()
      .then((response) => {
        if (cancelled) return;
        setSnapshot(response);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errorMessage(err, "Could not load preferences."));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setCell = useCallback(
    (
      event_kind: NotificationEventKind,
      channel: NotificationChannelV2,
      enabled: boolean,
    ) => {
      if (snapshot === null) return;
      const next: NotificationPreferenceEntry[] = snapshot.preferences.map(
        (entry) =>
          entry.event_kind === event_kind && entry.channel === channel
            ? { event_kind, channel, enabled }
            : entry,
      );
      const optimistic: NotificationPreferencesResponse = {
        ...snapshot,
        preferences: next,
      };
      setSnapshot(optimistic);
      updateMyNotificationPreferences({
        preferences: [{ event_kind, channel, enabled }],
      }).then(
        (response) => setSnapshot(response),
        (err: unknown) =>
          setError(errorMessage(err, "Could not save preference.")),
      );
    },
    [snapshot],
  );

  const setQuietHours = useCallback(
    (patch: Partial<NotificationPreferencesResponse["quiet_hours"]>) => {
      if (snapshot === null) return;
      const merged = { ...snapshot.quiet_hours, ...patch };
      const optimistic: NotificationPreferencesResponse = {
        ...snapshot,
        quiet_hours: merged,
      };
      setSnapshot(optimistic);
      updateMyNotificationPreferences({ quiet_hours: merged }).then(
        (response) => setSnapshot(response),
        (err: unknown) =>
          setError(errorMessage(err, "Could not save quiet hours.")),
      );
    },
    [snapshot],
  );

  if (snapshot === null) {
    return (
      <Card>
        <p>{error ?? "Loading typed preferences…"}</p>
      </Card>
    );
  }

  const cellMap = new Map<string, boolean>();
  for (const entry of snapshot.preferences) {
    cellMap.set(`${entry.event_kind}:${entry.channel}`, entry.enabled);
  }

  return (
    <>
      <Card>
        <Field
          label="Typed notifications"
          hint="6 event kinds × 3 channels. Falls back to deployment defaults until you flip a cell."
        >
          <table className="settings-matrix">
            <thead>
              <tr>
                <th />
                {CHANNEL_ORDER.map((channel) => (
                  <th key={channel}>{CHANNEL_LABELS[channel]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {EVENT_ORDER.map((event) => (
                <tr key={event}>
                  <th scope="row">{EVENT_LABELS[event]}</th>
                  {CHANNEL_ORDER.map((channel) => {
                    const enabled = cellMap.get(`${event}:${channel}`) ?? false;
                    return (
                      <td key={channel}>
                        <Switch
                          checked={enabled}
                          label=""
                          onChange={(input) =>
                            setCell(event, channel, input.target.checked)
                          }
                          aria-label={`${EVENT_LABELS[event]} → ${CHANNEL_LABELS[channel]}`}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Field>
      </Card>
      <Card>
        <Field
          label="Quiet hours"
          hint="During quiet hours only critical approvals break through."
        >
          <div className="settings-quiet-hours">
            <Switch
              checked={snapshot.quiet_hours.enabled}
              label="Enabled"
              onChange={(input) =>
                setQuietHours({ enabled: input.target.checked })
              }
              aria-label="Quiet hours enabled"
            />
            <label>
              From
              <input
                type="time"
                value={snapshot.quiet_hours.from_local}
                onChange={(event) =>
                  setQuietHours({ from_local: event.target.value })
                }
              />
            </label>
            <label>
              To
              <input
                type="time"
                value={snapshot.quiet_hours.to_local}
                onChange={(event) =>
                  setQuietHours({ to_local: event.target.value })
                }
              />
            </label>
            <label>
              Timezone
              <input
                type="text"
                value={snapshot.quiet_hours.tz}
                onChange={(event) => setQuietHours({ tz: event.target.value })}
                placeholder="America/New_York"
              />
            </label>
          </div>
        </Field>
      </Card>
      {error && (
        <Card>
          <p role="alert">{error}</p>
        </Card>
      )}
    </>
  );
}
