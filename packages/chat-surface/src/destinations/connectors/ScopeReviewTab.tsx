// <ScopeReviewTab /> — checkbox list of OAuth scopes the connector
// currently has, with a "Save" CTA that emits the desired set to the
// host. Host turns "Save" into a PATCH /v1/connectors/{id}/scopes (which
// triggers a re-OAuth round-trip — connectors-prd §4.4).
//
// Dirty-state lives locally; Save is disabled until the user toggles at
// least one row. The host owns the actual re-auth flow.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { ConnectorScopeEntry } from "@0x-copilot/api-types";

export interface ScopeReviewTabProps {
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
  /** Host receives the desired set; turns into PATCH /v1/connectors/{id}/scopes. */
  readonly onSave?: (next: ReadonlyArray<ConnectorScopeEntry>) => void;
}

export function ScopeReviewTab(props: ScopeReviewTabProps): ReactElement {
  const { scopes, onSave } = props;

  // Local set of selected scope strings. Initialize from the granted
  // flag; toggles run against this set without mutating props.
  const initial = useMemo(
    () => new Set(scopes.filter((s) => s.granted).map((s) => s.scope)),
    [scopes],
  );
  const [selected, setSelected] = useState<ReadonlySet<string>>(initial);

  const dirty = useMemo(() => {
    if (selected.size !== initial.size) return true;
    for (const v of selected) if (!initial.has(v)) return true;
    return false;
  }, [selected, initial]);

  const toggle = useCallback((scope: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }, []);

  const handleSave = useCallback(() => {
    if (onSave === undefined) return;
    const next: ReadonlyArray<ConnectorScopeEntry> = scopes.map((s) => ({
      scope: s.scope,
      granted: selected.has(s.scope),
      description: s.description,
    }));
    onSave(next);
  }, [scopes, selected, onSave]);

  const handleReset = useCallback(() => setSelected(initial), [initial]);

  return (
    <div data-testid="connector-scope-review" style={containerStyle}>
      <p style={hintStyle}>
        Toggle the scopes you want this connector to keep. Saving starts a
        re-OAuth flow so the provider can confirm the change.
      </p>
      {scopes.length === 0 ? (
        <p data-testid="connector-scope-empty" role="status" style={emptyStyle}>
          No scopes recorded yet.
        </p>
      ) : (
        <ul style={listStyle} data-testid="connector-scope-list">
          {scopes.map((s) => {
            const checked = selected.has(s.scope);
            return (
              <li
                key={s.scope}
                style={rowStyle}
                data-testid="connector-scope-row"
                data-scope={s.scope}
              >
                <label style={labelStyle}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(s.scope)}
                    data-testid={`connector-scope-checkbox-${s.scope}`}
                    aria-label={s.scope}
                  />
                  <span style={scopeTextWrapStyle}>
                    <code style={scopeCodeStyle}>{s.scope}</code>
                    {s.description.length > 0 ? (
                      <span style={descriptionStyle}>{s.description}</span>
                    ) : null}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <div style={actionRowStyle}>
        <button
          type="button"
          onClick={handleReset}
          disabled={!dirty}
          style={secondaryButtonStyle}
          data-testid="connector-scope-reset"
        >
          Reset
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || onSave === undefined}
          style={primaryButtonStyle}
          data-testid="connector-scope-save"
          data-dirty={dirty ? "true" : "false"}
        >
          Save
        </button>
      </div>
    </div>
  );
}

// === Styles ============================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
};

const labelStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 10,
  cursor: "pointer",
  width: "100%",
};

const scopeTextWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  minWidth: 0,
};

const scopeCodeStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
};

const descriptionStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontStyle: "italic",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const primaryButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};
