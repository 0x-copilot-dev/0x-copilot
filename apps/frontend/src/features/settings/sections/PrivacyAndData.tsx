// PR 4.3 — Settings → AI & data → Privacy & data.
//
// Five sub-cards on one panel:
//
//   1. Training opt-out toggle (lives in workspace_defaults.behavior_overrides
//      .training_data_opt_out; persists via the same useWorkspaceDefaults hook
//      so the audit row + per-provider header plumbing fires the moment the
//      admin flips it).
//   2. Data residency (read-only display from deployment profile metadata).
//   3. Retention summary (read-only — calls /v1/retention/effective; the
//      slider that *writes* retention lives on the Workspace panel via PR 1.6).
//   4. Export workspace data (queues an export; v1 stub returns 202 +
//      export_id and writes one audit row).
//   5. Delete all workspace data (501 stub with a typed-confirmation dialog;
//      typed correctness is recorded in audit even though the cascade-delete
//      pipeline is gated).

import type {
  RetentionEffectiveResponse,
  RetentionKind,
  UpdateWorkspaceDefaultsRequest,
  WorkspaceBehaviorOverrides,
  WorkspaceExportResponse,
} from "@enterprise-search/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  Switch,
  TextInput,
  classNames,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  deleteWorkspaceData,
  getRetentionEffective,
  requestWorkspaceExport,
} from "../../../api/agentApi";
import type { RequestIdentity } from "../../../api/config";
import type { UseWorkspaceDefaultsResult } from "../useWorkspaceDefaults";

const RETENTION_LABELS: Readonly<Record<RetentionKind, string>> = {
  messages: "Messages",
  events: "Events",
  context_payloads: "Context payloads",
  checkpoints: "Checkpoints",
  memory_items: "Memory items",
};

const SECONDS_PER_DAY = 24 * 60 * 60;

export function PrivacyAndData({
  identity,
  workspaceDefaults,
  dataResidency,
}: {
  identity: RequestIdentity;
  workspaceDefaults: UseWorkspaceDefaultsResult;
  /** Read-only deployment region label. ``null`` ⇒ "Not configured". */
  dataResidency?: string | null;
}): ReactElement {
  const { defaults, save } = workspaceDefaults;
  const overrides: WorkspaceBehaviorOverrides =
    defaults?.behavior_overrides ?? { training_data_opt_out: false };

  return (
    <div className="settings-section">
      <h2>Privacy &amp; data</h2>
      <p>
        Workspace-wide privacy posture, retention summary, and data lifecycle
        actions.
      </p>

      <TrainingOptOutCard
        overrides={overrides}
        defaults={defaults}
        save={save}
      />

      <DataResidencyCard region={dataResidency ?? null} />

      <RetentionSummaryCard identity={identity} />

      <ExportCard identity={identity} />

      <DeleteAllCard identity={identity} orgId={identity.orgId} />
    </div>
  );
}

function TrainingOptOutCard({
  overrides,
  defaults,
  save,
}: {
  overrides: WorkspaceBehaviorOverrides;
  defaults: UseWorkspaceDefaultsResult["defaults"];
  save: UseWorkspaceDefaultsResult["save"];
}): ReactElement {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onToggle = useCallback(
    async (next: boolean) => {
      if (defaults === null) {
        return;
      }
      setBusy(true);
      setError(null);
      const request: UpdateWorkspaceDefaultsRequest = {
        default_model: defaults.default_model,
        default_connectors: defaults.default_connectors,
        retention_days: defaults.retention_days,
        behavior_overrides: { ...overrides, training_data_opt_out: next },
      };
      try {
        await save(request);
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : "Could not save training opt-out.",
        );
      } finally {
        setBusy(false);
      }
    },
    [defaults, overrides, save],
  );

  const optedOut = overrides.training_data_opt_out === true;
  return (
    <Card>
      <Field
        label="Training data opt-out"
        hint="When on, every outbound model call carries the provider's do-not-train signal."
      >
        <div className="settings-toggle-row">
          <Switch
            checked={optedOut}
            label={optedOut ? "Opted out" : "Default (training allowed)"}
            disabled={busy || defaults === null}
            onChange={(event) => void onToggle(event.target.checked)}
          />
        </div>
      </Field>
      {error && <p role="alert">{error}</p>}
    </Card>
  );
}

function DataResidencyCard({
  region,
}: {
  region: string | null;
}): ReactElement {
  return (
    <Card>
      <Field
        label="Data residency"
        hint="Set at deployment time; contact your admin to change region."
      >
        <span className="settings-readonly-value">
          {region ?? "Not configured"}
        </span>
      </Field>
    </Card>
  );
}

function RetentionSummaryCard({
  identity,
}: {
  identity: RequestIdentity;
}): ReactElement {
  const [data, setData] = useState<RetentionEffectiveResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRetentionEffective(identity)
      .then((response) => {
        if (!cancelled) setData(response);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Could not load retention summary.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  return (
    <Card>
      <Field
        label="Retention"
        hint="Effective TTLs applied by the retention sweeper. Edit on the Workspace panel."
      >
        {loading ? (
          <p>Loading…</p>
        ) : error ? (
          <p role="alert">{error}</p>
        ) : data === null ? (
          <p>Not available.</p>
        ) : (
          <ul className="settings-retention-list">
            {Object.entries(data.effective).map(([kind, entry]) => (
              <li key={kind}>
                <span>{RETENTION_LABELS[kind as RetentionKind] ?? kind}</span>
                <span className="settings-retention-list__value">
                  {formatTtl(entry.ttl_seconds)}
                  <Badge
                    tone={entry.source_scope === null ? "neutral" : "accent"}
                  >
                    {entry.source_scope === null
                      ? "deployment default"
                      : entry.source_scope}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Field>
    </Card>
  );
}

function ExportCard({ identity }: { identity: RequestIdentity }): ReactElement {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WorkspaceExportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onExport = useCallback(async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await requestWorkspaceExport(identity);
      setResult(response);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not queue export.");
    } finally {
      setBusy(false);
    }
  }, [identity]);

  return (
    <Card>
      <Field
        label="Export workspace data"
        hint="Queues a workspace-wide NDJSON export. We email the download link when ready."
      >
        <div className="settings-action-row">
          <Button
            type="button"
            variant="secondary"
            onClick={() => void onExport()}
            disabled={busy}
          >
            {busy ? "Queueing…" : "Export workspace data"}
          </Button>
          {result && (
            <Badge tone="success" aria-live="polite">
              Queued · {result.export_id}
            </Badge>
          )}
        </div>
        {error && <p role="alert">{error}</p>}
      </Field>
    </Card>
  );
}

function DeleteAllCard({
  identity,
  orgId,
}: {
  identity: RequestIdentity;
  orgId: string;
}): ReactElement {
  const [confirmSlug, setConfirmSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const matches = confirmSlug.trim() === orgId;
  const onAttempt = useCallback(async () => {
    setBusy(true);
    setMessage(null);
    try {
      await deleteWorkspaceData(confirmSlug.trim(), identity);
      // Should never succeed in v1 — defensive copy.
      setMessage("Delete request accepted.");
    } catch (err: unknown) {
      // 501 surface — the message is the doc-required copy verbatim.
      setMessage(
        err instanceof Error
          ? err.message
          : "Workspace deletion is gated. Contact support.",
      );
    } finally {
      setBusy(false);
    }
  }, [confirmSlug, identity]);

  return (
    <Card>
      <Field
        label="Delete all workspace data"
        hint="High-risk, non-reversible. Type the workspace id to confirm."
      >
        <div className="settings-danger-zone">
          <TextInput
            value={confirmSlug}
            onChange={(event) => setConfirmSlug(event.target.value)}
            placeholder="Type the workspace id"
          />
          <Button
            type="button"
            variant="danger"
            disabled={!matches || busy}
            onClick={() => void onAttempt()}
            className={classNames("settings-danger-button")}
          >
            {busy ? "Submitting…" : "Delete workspace data"}
          </Button>
        </div>
        {message && (
          <p role="alert" className="settings-danger-message">
            {message}
          </p>
        )}
      </Field>
    </Card>
  );
}

function formatTtl(ttlSeconds: number | null): string {
  if (ttlSeconds === null) {
    return "Indefinite";
  }
  if (ttlSeconds <= 0) {
    return "0 days";
  }
  const days = Math.max(1, Math.round(ttlSeconds / SECONDS_PER_DAY));
  return days === 1 ? "1 day" : `${days} days`;
}
