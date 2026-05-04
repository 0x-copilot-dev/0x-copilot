/**
 * Settings → Sessions panel (A9): list active sessions, revoke each.
 *
 * Lives under ``features/settings`` (separate from the ``features/auth``
 * tree) because it's reached via the in-app settings nav, not the login
 * flow. Reuses ``listAccountSessions`` / ``revokeAccountSession`` from
 * the shared auth API client.
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

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

  return (
    <section className="settings-sessions" data-testid="sessions-panel">
      <header>
        <h2>Active sessions</h2>
        <button type="button" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
      </header>
      {loading && <p data-testid="sessions-loading">Loading…</p>}
      {error && (
        <p className="settings-sessions__error" role="alert">
          {error}
        </p>
      )}
      {!loading && sessions.length === 0 && !error && (
        <p>No active sessions.</p>
      )}
      <ul className="settings-sessions__list">
        {sessions.map((session) => (
          <li
            key={session.session_id}
            className="settings-sessions__row"
            data-session-id={session.session_id}
          >
            <div>
              <p>
                <strong>{session.device_label ?? "Unknown device"}</strong>
                {session.client_ip && <> · {session.client_ip}</>}
              </p>
              <p className="settings-sessions__meta">
                Created {session.created_at} · expires {session.expires_at}
                {session.mfa_satisfied ? " · MFA verified" : " · MFA pending"}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void onRevoke(session.session_id)}
              disabled={revoking === session.session_id}
              data-testid={`session-revoke-${session.session_id}`}
            >
              {revoking === session.session_id ? "Revoking…" : "Revoke"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
