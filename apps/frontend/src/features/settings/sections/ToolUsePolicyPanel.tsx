// PR B1 / 8.0.3d — per-user tool-use policy panel.
//
// Three axes (read / write / destructive) × four modes (auto / ask /
// require / block). Reads + writes through the new
// ``/v1/me/policies/tool-use`` facade route. The same shape ships for
// workspace-default writes via ``/v1/workspace/policies/tool-use`` —
// that wiring lives in the workspace-admin surface and isn't part of
// this panel.

import type { ToolUsePolicyResponse } from "@enterprise-search/api-types";
import { Card, Field, classNames } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import { getMyToolUsePolicy, updateMyToolUsePolicy } from "../../../api/meApi";

const KINDS = ["read", "write", "destructive"] as const;
const MODES = ["auto", "ask", "require", "block"] as const;

type Kind = (typeof KINDS)[number];
type Mode = (typeof MODES)[number];

const KIND_LABELS: Record<Kind, { label: string; hint: string }> = {
  read: {
    label: "Read tools",
    hint: "Search, fetch, summarise — anything that doesn't write back.",
  },
  write: {
    label: "Write tools",
    hint: "Send, post, edit — actions that change something.",
  },
  destructive: {
    label: "Destructive tools",
    hint: "Delete, purge, drop — irreversible actions.",
  },
};

const MODE_LABELS: Record<Mode, { label: string; hint: string }> = {
  auto: { label: "Auto", hint: "Allowed without prompting." },
  ask: { label: "Ask", hint: "Confirm once per session." },
  require: { label: "Require", hint: "Confirm every time." },
  block: { label: "Block", hint: "Never run." },
};

export function ToolUsePolicyPanel(): ReactElement {
  const [snapshot, setSnapshot] = useState<ToolUsePolicyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getMyToolUsePolicy()
      .then((response) => {
        if (cancelled) return;
        setSnapshot(response);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load policy.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = useCallback(
    (kind: Kind, mode: Mode) => {
      if (snapshot === null) return;
      // Optimistic update — flip the picked cell, then send the full
      // three-axis shape (atomic replace, matches the backend's PUT
      // semantics).
      // The wire request only carries kind+mode; the server fills
      // in `updated_at` / `updated_by_user_id` and returns a fresh
      // `ToolUsePolicyResponse` we re-seat below.
      const nextRequestPolicies = KINDS.map((axis) => ({
        kind: axis,
        mode:
          axis === kind
            ? mode
            : (snapshot.policies.find((entry) => entry.kind === axis)?.mode ??
              "auto"),
      }));
      const optimistic: ToolUsePolicyResponse = {
        ...snapshot,
        policies: snapshot.policies.map((entry) => {
          const next = nextRequestPolicies.find((p) => p.kind === entry.kind);
          return next ? { ...entry, mode: next.mode } : entry;
        }),
      };
      setSnapshot(optimistic);
      updateMyToolUsePolicy({ policies: nextRequestPolicies }).then(
        (response) => {
          setSnapshot(response);
          setSubmitError(null);
        },
        (err: unknown) => {
          setSubmitError(
            err instanceof Error ? err.message : "Could not save policy.",
          );
        },
      );
    },
    [snapshot],
  );

  if (loading) {
    return (
      <Card>
        <Field label="Tool use" hint="Loading your tool-permission overrides…">
          <span aria-hidden="true">…</span>
        </Field>
      </Card>
    );
  }
  if (snapshot === null) {
    return (
      <Card>
        <Field label="Tool use" hint={error ?? "Could not load policy."}>
          <span role="alert">{error ?? "Could not load policy."}</span>
        </Field>
      </Card>
    );
  }

  const modeFor = (kind: Kind): Mode => {
    const found = snapshot.policies.find((entry) => entry.kind === kind);
    return (found?.mode as Mode | undefined) ?? "auto";
  };

  return (
    <Card>
      <Field
        label="Tool use"
        hint="Decide which tool kinds run automatically and which need confirmation. Falls back to your workspace default."
      >
        <div className="settings-policy-grid" role="group">
          {KINDS.map((kind) => {
            const current = modeFor(kind);
            const labels = KIND_LABELS[kind];
            return (
              <div className="settings-policy-row" key={kind}>
                <div>
                  <strong>{labels.label}</strong>
                  <p>{labels.hint}</p>
                </div>
                <div
                  className="settings-pill-group"
                  role="radiogroup"
                  aria-label={`${labels.label} mode`}
                >
                  {MODES.map((mode) => {
                    const active = mode === current;
                    return (
                      <button
                        key={mode}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        className={classNames(
                          "settings-pill",
                          active && "settings-pill--active",
                        )}
                        title={MODE_LABELS[mode].hint}
                        onClick={() => persist(kind, mode)}
                      >
                        {MODE_LABELS[mode].label}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
        {submitError && <p role="alert">{submitError}</p>}
      </Field>
    </Card>
  );
}
