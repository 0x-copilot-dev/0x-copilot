/**
 * Settings → Sessions panel (A9): list active sessions, revoke each.
 *
 * Lives under ``features/settings`` (separate from the ``features/auth``
 * tree) because it's reached via the in-app settings nav, not the login
 * flow. Reuses ``listAccountSessions`` / ``revokeAccountSession`` from
 * the shared auth API client. Built on the design-system primitives so
 * it matches the rest of the settings tree.
 */

import {
  Badge,
  Button,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { listAccountSessions, revokeAccountSession } from "../../api/authApi";
import type { AccountSession } from "@enterprise-search/api-types";

export function AccountSessionsPanel(): ReactElement {
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSessions(await listAccountSessions());
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not list sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onRevoke = useCallback(
    async (sessionId: string) => {
      setRevoking(sessionId);
      try {
        await revokeAccountSession(sessionId);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : "could not revoke");
      } finally {
        setRevoking(null);
      }
    },
    [reload],
  );

  const sortedSessions = useMemo(() => {
    return [...sessions].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  }, [sessions]);

  return (
    <section className="sessions-panel" data-testid="sessions-panel">
      <header className="sessions-panel__header">
        <div>
          <h2>Active sessions</h2>
          <p className="sessions-panel__subtitle">
            Review devices signed in to your account. Revoke any you don't
            recognise — the next request from that session is rejected
            instantly.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void reload()}
          disabled={loading}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </header>

      {error && (
        <Card tone="danger" className="sessions-panel__alert" role="alert">
          {error}
        </Card>
      )}

      {!loading && sortedSessions.length === 0 && !error && (
        <Card tone="muted" className="sessions-panel__empty">
          No active sessions.
        </Card>
      )}

      <ul className="sessions-panel__list">
        {sortedSessions.map((session) => (
          <li
            key={session.session_id}
            data-session-id={session.session_id}
            className="sessions-panel__item"
          >
            <Card
              tone="default"
              className={classNames(
                "sessions-panel__row",
                revoking === session.session_id && "sessions-panel__row--busy",
              )}
            >
              <div className="sessions-panel__row-meta">
                <p className="sessions-panel__row-title">
                  <strong>{session.device_label ?? "Unknown device"}</strong>
                  <Badge
                    tone={session.mfa_satisfied ? "success" : "warning"}
                    className="sessions-panel__badge"
                  >
                    {session.mfa_satisfied ? "MFA verified" : "MFA pending"}
                  </Badge>
                </p>
                <p className="sessions-panel__row-detail">
                  {session.client_ip ? `${session.client_ip} · ` : ""}
                  Signed in {_formatTimestamp(session.created_at)} · expires{" "}
                  {_formatTimestamp(session.expires_at)}
                </p>
              </div>
              <Button
                type="button"
                variant="danger"
                size="sm"
                onClick={() => void onRevoke(session.session_id)}
                disabled={revoking === session.session_id}
                data-testid={`session-revoke-${session.session_id}`}
              >
                {revoking === session.session_id ? "Revoking…" : "Revoke"}
              </Button>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

function _formatTimestamp(iso: string): string {
  // Tolerant of both ``Z`` and ``+00:00`` suffixes; falls back to the
  // raw string when the value isn't an ISO timestamp.
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
