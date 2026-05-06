// PR B2 / 8.0.3f — per-user privacy & data overrides panel.
//
// Five toggles + one knob, scoped to the caller. Workspace-level
// defaults still live in PrivacyAndData.tsx via useWorkspaceDefaults;
// this panel surfaces *user-level* overrides through
// ``/v1/me/policies/privacy``.

import type {
  DataResidencyRegion,
  PrivacySettingsResponse,
  UpdatePrivacySettingsRequest,
} from "@enterprise-search/api-types";
import {
  Card,
  Field,
  Switch,
  classNames,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  getMyPrivacySettings,
  updateMyPrivacySettings,
} from "../../../api/meApi";

const REGIONS: ReadonlyArray<{ id: DataResidencyRegion; label: string }> = [
  { id: "us-east-1", label: "US (us-east-1)" },
  { id: "eu-west-1", label: "EU (eu-west-1)" },
  { id: "ap-northeast-1", label: "Asia (ap-northeast-1)" },
];

export function PrivacyOverridesPanel(): ReactElement {
  const [snapshot, setSnapshot] = useState<PrivacySettingsResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyPrivacySettings()
      .then((response) => {
        if (cancelled) return;
        setSnapshot(response);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Could not load privacy.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = useCallback(
    (patch: UpdatePrivacySettingsRequest) => {
      if (snapshot === null) return;
      const optimistic: PrivacySettingsResponse = {
        ...snapshot,
        training_opt_out: patch.training_opt_out ?? snapshot.training_opt_out,
        region: "region" in patch ? (patch.region ?? null) : snapshot.region,
        retention_days:
          "retention_days" in patch
            ? (patch.retention_days ?? null)
            : snapshot.retention_days,
        share_metadata: patch.share_metadata ?? snapshot.share_metadata,
        memory_enabled: patch.memory_enabled ?? snapshot.memory_enabled,
      };
      setSnapshot(optimistic);
      updateMyPrivacySettings(patch).then(
        (response) => setSnapshot(response),
        (err: unknown) =>
          setError(
            err instanceof Error ? err.message : "Could not save privacy.",
          ),
      );
    },
    [snapshot],
  );

  if (snapshot === null) {
    return (
      <Card>
        <p>{error ?? "Loading privacy overrides…"}</p>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <Field
          label="Training opt-out"
          hint="Send a do-not-train signal to the provider on every request."
        >
          <Switch
            checked={snapshot.training_opt_out}
            label={snapshot.training_opt_out ? "Opted out" : "Opted in"}
            onChange={(input) =>
              persist({ training_opt_out: input.target.checked })
            }
          />
        </Field>
      </Card>

      <Card>
        <Field
          label="Memory"
          hint="Atlas remembers preferences and context across chats. Toggle off to disable for new chats."
        >
          <Switch
            checked={snapshot.memory_enabled}
            label={snapshot.memory_enabled ? "Enabled" : "Disabled"}
            onChange={(input) =>
              persist({ memory_enabled: input.target.checked })
            }
          />
        </Field>
      </Card>

      <Card>
        <Field
          label="Admin-visible thread metadata"
          hint="Allow workspace admins to see thread titles, models, and approvals (message content stays private regardless)."
        >
          <Switch
            checked={snapshot.share_metadata}
            label={snapshot.share_metadata ? "Shared" : "Private"}
            onChange={(input) =>
              persist({ share_metadata: input.target.checked })
            }
          />
        </Field>
      </Card>

      <Card>
        <Field
          label="Data residency"
          hint="Pin your runs to a specific region. Leave blank to use the workspace default."
        >
          <div
            className="settings-pill-group"
            role="radiogroup"
            aria-label="Data residency"
          >
            <button
              type="button"
              role="radio"
              aria-checked={snapshot.region === null}
              className={classNames(
                "settings-pill",
                snapshot.region === null && "settings-pill--active",
              )}
              onClick={() => persist({ region: null })}
            >
              Default
            </button>
            {REGIONS.map((region) => {
              const active = snapshot.region === region.id;
              return (
                <button
                  key={region.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={classNames(
                    "settings-pill",
                    active && "settings-pill--active",
                  )}
                  onClick={() => persist({ region: region.id })}
                >
                  {region.label}
                </button>
              );
            })}
          </div>
        </Field>
      </Card>

      <Card>
        <Field
          label="Retention"
          hint="Auto-delete chats after N days. Leave blank to keep forever (subject to workspace policy)."
        >
          <input
            type="number"
            min={1}
            max={3650}
            placeholder="Forever"
            className="ui-input"
            value={snapshot.retention_days ?? ""}
            onChange={(event) => {
              const raw = event.target.value.trim();
              persist({
                retention_days: raw === "" ? null : Number(raw),
              });
            }}
          />
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
