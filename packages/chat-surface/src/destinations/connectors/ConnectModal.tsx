// <ConnectModal /> — Tools destination "Connect a tool" flow (DESIGN-SPEC §5,
// FR-4.23). Multi-step on the shared <Modal> + <StepDots> chrome:
//
//   catalog pick → OAuth spinner → permission (Read only / Read & act) → Connect
//
// Substrate-agnostic (chat-surface boundary, same rules as ./Modal): NO bare
// fetch / window / document / EventSource. This component owns ONLY the flow
// state; the host binder (PR-4.8b) performs the OAuth round-trip and persists
// the connection. It drives the modal purely through props:
//
//   • onSelectEntry(slug) — fired when a catalog card is picked. The host opens
//     the provider OAuth window and flips `pending` true.
//   • pending — true while the host is authorizing (OAuth window open) OR
//     persisting the final Connect; drives the spinner / disables Connect.
//   • error  — inline OAuth/connect failure copy, rendered role="alert".
//   • onConnect(slug, permission) — fired on the terminal Connect with the
//     picked entry + the chosen access mode ("read" | "read_act").
//
// Pure presentation. Colors resolve ONLY to design-system v2 tokens; the
// modal 500px width is inherited from <Modal>.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type {
  ConnectorAccessMode,
  ConnectorCatalogEntry,
  ConnectorSlug,
} from "@0x-copilot/api-types";
import { Button } from "@0x-copilot/design-system";

import { Modal, StepDots } from "../../settings/Modal";

// ---------------------------------------------------------------------------
// Permission choice — the connect flow only offers the two *granting* modes
// ("off" is a per-tool disable on an already-connected tool, not a connect-
// time choice). Aligns with `ConnectorAccessMode` from @0x-copilot/api-types.
// ---------------------------------------------------------------------------

export type ConnectPermission = Extract<
  ConnectorAccessMode,
  "read" | "read_act"
>;

export interface ConnectPermissionOption {
  readonly value: ConnectPermission;
  readonly label: string;
  readonly description: string;
}

export const CONNECT_PERMISSION_OPTIONS: ReadonlyArray<ConnectPermissionOption> =
  [
    {
      value: "read",
      label: "Read only",
      description: "The agent can see data from this app but never change it.",
    },
    {
      value: "read_act",
      label: "Read & act",
      description:
        "The agent can read and take actions — still gated by your approval policy.",
    },
  ];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ConnectModalProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Generic-SaaS-first catalog of slugs available to connect (FR-4.24). */
  readonly catalog: readonly ConnectorCatalogEntry[];
  /**
   * Fired when a catalog entry is picked. The host starts OAuth (opens the
   * provider window) and reflects progress back via `pending` / `error`.
   */
  readonly onSelectEntry?: (entryId: ConnectorSlug) => void;
  /** Fired on the terminal Connect with the picked slug + chosen permission. */
  readonly onConnect: (
    entryId: ConnectorSlug,
    permission: ConnectorAccessMode,
  ) => void;
  /**
   * True while the host is authorizing (OAuth window open) or persisting the
   * final Connect. Drives the OAuth spinner and disables Connect.
   */
  readonly pending?: boolean;
  /** Inline OAuth/connect failure copy; rendered as a `role="alert"` node. */
  readonly error?: string | null;
}

type ConnectPhase = "catalog" | "oauth" | "permission";

const PHASE_STEP: Record<ConnectPhase, number> = {
  catalog: 1,
  oauth: 2,
  permission: 3,
};

// ---------------------------------------------------------------------------
// ConnectModal
// ---------------------------------------------------------------------------

export function ConnectModal({
  open,
  onClose,
  catalog,
  onSelectEntry,
  onConnect,
  pending = false,
  error = null,
}: ConnectModalProps): ReactElement | null {
  const [phase, setPhase] = useState<ConnectPhase>("catalog");
  const [selected, setSelected] = useState<ConnectorCatalogEntry | null>(null);
  const [permission, setPermission] = useState<ConnectPermission>("read");

  // Reset the flow whenever the modal is (re)opened.
  useEffect(() => {
    if (open) {
      setPhase("catalog");
      setSelected(null);
      setPermission("read");
    }
  }, [open]);

  // OAuth → permission advance is host-driven: once the host has cleared
  // `pending` with no `error`, authorization succeeded and we reveal the
  // permission choice. Errors keep us on the OAuth step (Back / Retry).
  useEffect(() => {
    if (phase === "oauth" && !pending && error === null) {
      setPhase("permission");
    }
  }, [phase, pending, error]);

  const handlePick = useCallback(
    (entry: ConnectorCatalogEntry) => {
      setSelected(entry);
      setPermission("read");
      setPhase("oauth");
      onSelectEntry?.(entry.slug);
    },
    [onSelectEntry],
  );

  const backToCatalog = useCallback(() => {
    setSelected(null);
    setPhase("catalog");
  }, []);

  const retryOAuth = useCallback(() => {
    if (selected !== null) onSelectEntry?.(selected.slug);
  }, [selected, onSelectEntry]);

  const handleConnect = useCallback(() => {
    if (selected !== null) onConnect(selected.slug, permission);
  }, [selected, permission, onConnect]);

  const footer = (
    <>
      <StepDots total={3} current={PHASE_STEP[phase]} />
      <div style={{ display: "inline-flex", gap: "var(--space-sm)" }}>
        {phase === "catalog" ? (
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="connect-cancel"
          >
            Cancel
          </Button>
        ) : null}
        {phase === "oauth" && error !== null ? (
          <>
            <Button
              variant="ghost"
              onClick={backToCatalog}
              data-testid="connect-back"
            >
              Back
            </Button>
            <Button
              variant="secondary"
              onClick={retryOAuth}
              data-testid="connect-retry"
            >
              Retry
            </Button>
          </>
        ) : null}
        {phase === "oauth" && error === null ? (
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="connect-cancel"
          >
            Cancel
          </Button>
        ) : null}
        {phase === "permission" ? (
          <>
            <Button
              variant="ghost"
              onClick={backToCatalog}
              data-testid="connect-back"
            >
              Back
            </Button>
            <Button
              variant="primary"
              onClick={handleConnect}
              disabled={pending}
              data-testid="connect-confirm"
            >
              {pending ? "Connecting…" : "Connect"}
            </Button>
          </>
        ) : null}
      </div>
    </>
  );

  if (!open) return null;

  const subtitle =
    selected !== null ? selected.display_name : "Bring a SaaS source in";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Connect a tool"
      subtitle={subtitle}
      logo={<span aria-hidden="true">◆</span>}
      footer={footer}
    >
      {phase === "catalog" ? (
        <CatalogStep catalog={catalog} onPick={handlePick} />
      ) : null}
      {phase === "oauth" ? (
        <OAuthStep name={selected?.display_name ?? "the tool"} error={error} />
      ) : null}
      {phase === "permission" ? (
        <PermissionStep
          name={selected?.display_name ?? "the tool"}
          value={permission}
          onChange={setPermission}
          error={error}
        />
      ) : null}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — catalog pick
// ---------------------------------------------------------------------------

function CatalogStep({
  catalog,
  onPick,
}: {
  readonly catalog: readonly ConnectorCatalogEntry[];
  readonly onPick: (entry: ConnectorCatalogEntry) => void;
}): ReactElement {
  if (catalog.length === 0) {
    return (
      <p style={mutedNoteStyle} data-testid="connect-catalog-empty">
        No connectors are available to add right now.
      </p>
    );
  }
  return (
    <ul style={listStyle} data-testid="connect-catalog-list">
      {catalog.map((entry) => (
        <li key={entry.slug}>
          <button
            type="button"
            style={pickRowStyle}
            onClick={() => onPick(entry)}
            data-testid="connect-catalog-option"
            data-slug={entry.slug}
          >
            <span aria-hidden="true" style={glyphStyle}>
              ◆
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={pickNameStyle}>{entry.display_name}</span>
              <span style={pickSubStyle}>{entry.description}</span>
            </span>
            <span aria-hidden="true" style={chevronStyle}>
              ›
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — OAuth spinner / error
// ---------------------------------------------------------------------------

function OAuthStep({
  name,
  error,
}: {
  readonly name: string;
  readonly error: string | null;
}): ReactElement {
  if (error !== null) {
    return (
      <p style={alertNoteStyle} role="alert" data-testid="connect-oauth-error">
        Couldn&rsquo;t authorize with {name}: {error} Retry, or go back to pick
        a different tool.
      </p>
    );
  }
  return (
    <div style={centerColStyle} role="status" data-testid="connect-oauth">
      <style>{spinnerKeyframes}</style>
      <span className="cm-spinner" aria-hidden="true" style={spinnerStyle} />
      <p style={mutedNoteStyle}>
        Authorizing with {name}… approve in the window that opened.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — permission choice (radiogroup)
// ---------------------------------------------------------------------------

function PermissionStep({
  name,
  value,
  onChange,
  error,
}: {
  readonly name: string;
  readonly value: ConnectPermission;
  readonly onChange: (next: ConnectPermission) => void;
  readonly error: string | null;
}): ReactElement {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const move = (delta: number): void => {
    const count = CONNECT_PERMISSION_OPTIONS.length;
    const current = CONNECT_PERMISSION_OPTIONS.findIndex(
      (o) => o.value === value,
    );
    const next = (current + delta + count) % count;
    const option = CONNECT_PERMISSION_OPTIONS[next];
    onChange(option.value);
    refs.current[next]?.focus();
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      move(1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      move(-1);
    }
  };

  return (
    <div style={permissionRootStyle}>
      <p style={{ margin: 0 }}>What can {name} do?</p>
      <div
        role="radiogroup"
        aria-label={`Permission for ${name}`}
        onKeyDown={handleKeyDown}
        style={optionColStyle}
        data-testid="connect-permission"
      >
        {CONNECT_PERMISSION_OPTIONS.map((option, i) => {
          const selected = option.value === value;
          return (
            <button
              key={option.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(option.value)}
              style={optionRowStyle(selected)}
              data-testid="connect-permission-option"
              data-value={option.value}
            >
              <span style={optionLabelStyle}>{option.label}</span>
              <span style={optionDescStyle}>{option.description}</span>
            </button>
          );
        })}
      </div>
      {error !== null ? (
        <p
          style={alertNoteStyle}
          role="alert"
          data-testid="connect-permission-error"
        >
          Couldn&rsquo;t connect {name}: {error}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles (token-only)
// ---------------------------------------------------------------------------

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const pickRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-md)",
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface-muted)",
  color: "var(--color-text)",
  font: "inherit",
  textAlign: "left",
  cursor: "pointer",
  transition: "background-color var(--duration-fast) var(--ease-standard)",
};

const glyphStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 28,
  borderRadius: "var(--radius-sm)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-muted)",
};

const pickNameStyle: CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

const pickSubStyle: CSSProperties = {
  display: "block",
  margin: "1px 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const chevronStyle: CSSProperties = {
  flex: "0 0 auto",
  color: "var(--color-text-subtle)",
};

const centerColStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "var(--space-md)",
  padding: "var(--space-lg) 0",
  textAlign: "center",
};

const spinnerStyle: CSSProperties = {
  width: 24,
  height: 24,
  borderRadius: "var(--radius-full)",
  border: "2px solid var(--color-border)",
  borderTopColor: "var(--color-accent)",
  boxSizing: "border-box",
};

const spinnerKeyframes = `@keyframes cm-spin { to { transform: rotate(360deg); } }
.cm-spinner { animation: cm-spin 0.8s linear infinite; }`;

const mutedNoteStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const alertNoteStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-danger)",
};

const permissionRootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
};

const optionColStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

function optionRowStyle(selected: boolean): CSSProperties {
  return {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 2,
    width: "100%",
    padding: "var(--space-sm) var(--space-md)",
    borderRadius: "var(--radius-md)",
    border: selected
      ? "1px solid var(--color-accent)"
      : "1px solid var(--color-border)",
    backgroundColor: selected
      ? "var(--color-accent-soft)"
      : "var(--color-surface-muted)",
    color: "var(--color-text)",
    font: "inherit",
    textAlign: "left",
    cursor: "pointer",
    transition: "background-color var(--duration-fast) var(--ease-standard)",
  };
}

const optionLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-semibold)",
  color: "var(--color-text)",
};

const optionDescStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};
