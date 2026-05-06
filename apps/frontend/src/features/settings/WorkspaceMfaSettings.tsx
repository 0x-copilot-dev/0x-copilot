// PR 8.3 — admin editor for the workspace's MFA enforcement.
//
// One toggle (require MFA for sign-in) + one select (step-up window).
// Reads/writes via `/v1/workspace/mfa-policy`. Backend enforces
// ADMIN_USERS; the FE hides the Save button when the user isn't an
// admin and renders the form disabled.

import {
  Button,
  Card,
  Field,
  Select,
  Switch,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";
import {
  getWorkspaceMfaPolicy,
  updateWorkspaceMfaPolicy,
} from "../../api/workspaceMfaApi";

const STEP_UP_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 900, label: "15 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 28800, label: "8 hours" },
  { value: 86400, label: "24 hours" },
];

export function WorkspaceMfaSettings({
  isAdmin,
}: {
  isAdmin: boolean;
}): ReactElement {
  const [mfaRequired, setMfaRequired] = useState(false);
  const [stepUp, setStepUp] = useState(900);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getWorkspaceMfaPolicy()
      .then((policy) => {
        if (cancelled) return;
        setMfaRequired(policy.mfa_required);
        setStepUp(policy.step_up_window_seconds);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 403 for non-admins; show the section in read-only with a
        // soft message instead of pushing them to a different page.
        setError(
          err instanceof Error ? err.message : "Could not load MFA policy.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave(): Promise<void> {
    if (!isAdmin) return;
    setBusy(true);
    setError(null);
    try {
      const next = await updateWorkspaceMfaPolicy({
        mfa_required: mfaRequired,
        step_up_window_seconds: stepUp,
      });
      setMfaRequired(next.mfa_required);
      setStepUp(next.step_up_window_seconds);
      setSavedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save policy.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <header className="settings-card__head">
        <div>
          <h3 className="me-form__card-title">Two-step enforcement</h3>
          <p className="settings-meta">
            When required, every sign-in must complete an MFA challenge before
            sessions are usable.
          </p>
        </div>
      </header>

      {loading ? (
        <p className="settings-meta">Loading policy…</p>
      ) : (
        <>
          <Field
            label="Require MFA for sign-in"
            hint="Members without an enrolled factor will be forced through enrollment on next login."
          >
            <Switch
              label={mfaRequired ? "On" : "Off"}
              checked={mfaRequired}
              onChange={(e) => setMfaRequired(e.target.checked)}
              disabled={!isAdmin || busy}
            />
          </Field>

          <Field
            label="Step-up window"
            hint="How long an MFA verification holds before re-prompting for sensitive actions."
          >
            <Select
              value={String(stepUp)}
              onChange={(e) => setStepUp(Number(e.target.value))}
              disabled={!isAdmin || busy}
            >
              {STEP_UP_OPTIONS.map((opt) => (
                <option key={opt.value} value={String(opt.value)}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </Field>

          {error ? <p className="app-error">{error}</p> : null}

          {isAdmin ? (
            <div className="me-form__actions">
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => void onSave()}
                disabled={busy}
              >
                {busy ? "Saving…" : "Save policy"}
              </Button>
              {savedAt ? (
                <span className="settings-meta">
                  Saved at {new Date(savedAt).toLocaleTimeString()}
                </span>
              ) : null}
            </div>
          ) : (
            <p className="settings-meta">Read-only — admins can edit.</p>
          )}
        </>
      )}
    </Card>
  );
}
