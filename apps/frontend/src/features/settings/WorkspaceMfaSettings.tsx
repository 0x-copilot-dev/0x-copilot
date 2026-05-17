// PR 8.3 — admin editor for the workspace's MFA enforcement.
//
// One toggle (require MFA for sign-in) + one select (step-up window).
// Reads/writes via `/v1/workspace/mfa-policy`. Backend enforces
// ADMIN_USERS; the FE hides the Save button when the user isn't an
// admin and renders the form disabled.
//
// PRD 05 — load + save + cancellation come from `useWorkspaceMfaPolicy`
// (the same `useMutableRecord` shape that backs `useWorkspace` and
// `useWorkspaceDefaults`). The form keeps a local edit buffer
// (`mfaRequired`, `stepUp`) seeded from the server snapshot; on Save
// we call the hook's `save()` and let the hook own the busy/error
// surface.

import {
  Button,
  Card,
  Field,
  Select,
  Switch,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { useWorkspaceMfaPolicy } from "./useWorkspaceMfaPolicy";

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
  const policy = useWorkspaceMfaPolicy();

  // Local edit buffer — distinct from the server snapshot so the user
  // can flip the switch / change the dropdown without each click
  // firing a save. Reseeded whenever the server snapshot lands or
  // refreshes underneath us (e.g. another admin saved in parallel).
  const [mfaRequired, setMfaRequired] = useState(false);
  const [stepUp, setStepUp] = useState(900);
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    if (policy.data === null) return;
    setMfaRequired(policy.data.mfa_required);
    setStepUp(policy.data.step_up_window_seconds);
  }, [policy.data]);

  async function onSave(): Promise<void> {
    if (!isAdmin) return;
    setBusy(true);
    try {
      await policy.save({
        mfa_required: mfaRequired,
        step_up_window_seconds: stepUp,
      });
      setSavedAt(new Date().toISOString());
    } catch {
      // `useMutableRecord` already routes the error into `policy.error`;
      // nothing to do here.
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

      {policy.loading && policy.data === null ? (
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

          {policy.error ? <p className="app-error">{policy.error}</p> : null}

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
