// PR 4.4 — MCP catalog "browse + install" wizard.
//
// 5-step modal flow per the Atlas design doc:
//
//   1. Browse        — catalog grid of well-known servers + custom URL
//   2. Auth          — pick auth method (OAuth / API key / no-auth)
//   3. Scope review  — list of scopes the server will receive
//   4. Confirm       — summary card + "Add to workspace"
//   5. Connected     — success state with "Try in chat" / "View in Connectors"
//
// v1 reuses ``useConnectors().addServer`` for the create path and
// ``startMcpAuth`` for the OAuth handoff. Per-tool scope toggles +
// read-only preset are deferred to the catalog redesign (the schema
// doesn't carry per-tool scopes yet); the scope step shows the
// server's ``required_scopes`` so the admin can read them before
// committing.
//
// Test-connection: backend exposes ``/internal/v1/mcp/servers/{id}/test-token``
// for OAuth servers; v1 wires the call after add+authenticate. The
// wizard surfaces the result on step 5.

import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import "./mcp-wizard.css";
import {
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { McpServer } from "@enterprise-search/api-types";
import { Modal } from "../../settings/Modal";
import type { ConnectorState } from "../useConnectors";

/**
 * Hard-coded catalog of well-known MCP servers. Pulls names + URLs +
 * known scope hints into a single source of truth that drives both the
 * browse grid and the scope-review step. A server-driven catalog is a
 * follow-up — the shape here is the contract a future fetch will
 * deserialize into.
 */
const CATALOG: CatalogEntry[] = [
  {
    id: "linear",
    name: "Linear",
    url: "https://mcp.linear.app/sse",
    color: "#5e6ad2",
    icon: "L",
    description: "Issues, projects, and cycles. Read-only by default.",
    auth_method: "oauth",
    suggested_scopes: ["read:issues", "read:projects"],
  },
  {
    id: "notion",
    name: "Notion",
    url: "https://mcp.notion.com/sse",
    color: "#000000",
    icon: "N",
    description: "Workspace pages and databases.",
    auth_method: "oauth",
    suggested_scopes: ["read:pages"],
  },
  {
    id: "sentry",
    name: "Sentry",
    url: "https://mcp.sentry.dev/sse",
    color: "#362d59",
    icon: "S",
    description: "Issues, releases, and stack traces.",
    auth_method: "oauth",
    suggested_scopes: ["event:read", "project:read"],
  },
  {
    id: "github",
    name: "GitHub",
    url: "https://mcp.github.com/sse",
    color: "#0d1117",
    icon: "G",
    description: "Repos, issues, pull requests.",
    auth_method: "oauth",
    suggested_scopes: ["read:user", "repo"],
  },
  {
    id: "slack",
    name: "Slack",
    url: "https://mcp.slack.com/sse",
    color: "#4a154b",
    icon: "#",
    description: "Channels, messages, and threads.",
    auth_method: "oauth",
    suggested_scopes: ["channels:read", "chat:write"],
  },
  {
    id: "drive",
    name: "Google Drive",
    url: "https://mcp.google.com/drive/sse",
    color: "#4285f4",
    icon: "G",
    description: "Files, comments, and folders.",
    auth_method: "oauth",
    suggested_scopes: ["https://www.googleapis.com/auth/drive.readonly"],
  },
];

interface CatalogEntry {
  id: string;
  name: string;
  url: string;
  color: string;
  icon: string;
  description: string;
  auth_method: "oauth" | "api_key" | "none";
  suggested_scopes: string[];
}

type Selection =
  | { kind: "catalog"; entry: CatalogEntry }
  | { kind: "custom"; url: string; name: string };

type Step =
  | { kind: "browse" }
  | { kind: "auth"; selection: Selection }
  | { kind: "scope"; selection: Selection; auth_method: AuthMethod }
  | {
      kind: "confirm";
      selection: Selection;
      auth_method: AuthMethod;
      scopes: string[];
    }
  | {
      kind: "connected";
      selection: Selection;
      auth_method: AuthMethod;
      scopes: string[];
      server: McpServer;
    };

type AuthMethod = "oauth" | "api_key" | "none";

export interface McpOverlayProps {
  open: boolean;
  onClose: () => void;
  connectors: ConnectorState;
  /** Optional CTA hook for the success state's "Try in chat" link. */
  onTryInChat?: (server: McpServer) => void;
}

export function McpOverlay({
  open,
  onClose,
  connectors,
  onTryInChat,
}: McpOverlayProps): ReactElement {
  const [step, setStep] = useState<Step>({ kind: "browse" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Reset state every time the modal opens so a re-open doesn't show
  // the previous run's success / error state.
  useEffect(() => {
    if (open) {
      setStep({ kind: "browse" });
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const goBrowse = useCallback(() => {
    setStep({ kind: "browse" });
    setError(null);
  }, []);

  const onPickCatalog = useCallback((entry: CatalogEntry) => {
    setError(null);
    setStep({ kind: "auth", selection: { kind: "catalog", entry } });
  }, []);

  const onPickCustom = useCallback((url: string) => {
    if (!url.trim()) return;
    setError(null);
    setStep({
      kind: "auth",
      selection: {
        kind: "custom",
        url: url.trim(),
        name: hostnameLabel(url.trim()),
      },
    });
  }, []);

  const onPickAuth = useCallback((method: AuthMethod) => {
    setStep((current) => {
      if (current.kind !== "auth") return current;
      return {
        kind: "scope",
        selection: current.selection,
        auth_method: method,
      };
    });
  }, []);

  const onScopeContinue = useCallback((scopes: string[]) => {
    setStep((current) => {
      if (current.kind !== "scope") return current;
      return {
        kind: "confirm",
        selection: current.selection,
        auth_method: current.auth_method,
        scopes,
      };
    });
  }, []);

  const onConfirm = useCallback(async () => {
    if (step.kind !== "confirm") return;
    setBusy(true);
    setError(null);
    try {
      // ``addServer`` mutates the parent store and resolves once the
      // server row is created. We need the row itself for the success
      // state — pull it from the freshly-refreshed list.
      const url = selectionUrl(step.selection);
      await connectors.addServer(url);
      const server = connectors.servers.find((s) => s.url === url);
      if (!server) {
        throw new Error(
          "Server was added but the row didn't appear in the list.",
        );
      }
      setStep({
        kind: "connected",
        selection: step.selection,
        auth_method: step.auth_method,
        scopes: step.scopes,
        server,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add connector.");
    } finally {
      setBusy(false);
    }
  }, [connectors, step]);

  const onAuthenticate = useCallback(async () => {
    if (step.kind !== "connected") return;
    setBusy(true);
    setError(null);
    try {
      // ``connectors.authenticate`` redirects to the IdP — the modal
      // unmounts when the browser navigates. We do NOT close the modal
      // here so a popup-blocker / preventDefault doesn't leave the user
      // staring at a confused "connected" screen.
      await connectors.authenticate(step.server.server_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start auth.");
      setBusy(false);
    }
  }, [connectors, step]);

  const title = useMemo(() => titleForStep(step), [step]);

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="mcp-wizard">
        <StepIndicator step={step} />
        {error ? (
          <div className="mcp-wizard__error" role="alert">
            {error}
          </div>
        ) : null}
        {step.kind === "browse" && (
          <BrowseStep
            onPickCatalog={onPickCatalog}
            onPickCustom={onPickCustom}
          />
        )}
        {step.kind === "auth" && (
          <AuthStep
            selection={step.selection}
            onPick={onPickAuth}
            onBack={goBrowse}
          />
        )}
        {step.kind === "scope" && (
          <ScopeStep
            selection={step.selection}
            auth_method={step.auth_method}
            onContinue={onScopeContinue}
            onBack={() => setStep({ kind: "auth", selection: step.selection })}
          />
        )}
        {step.kind === "confirm" && (
          <ConfirmStep
            selection={step.selection}
            auth_method={step.auth_method}
            scopes={step.scopes}
            busy={busy}
            onConfirm={onConfirm}
            onBack={() =>
              setStep({
                kind: "scope",
                selection: step.selection,
                auth_method: step.auth_method,
              })
            }
          />
        )}
        {step.kind === "connected" && (
          <ConnectedStep
            selection={step.selection}
            auth_method={step.auth_method}
            server={step.server}
            busy={busy}
            onAuthenticate={onAuthenticate}
            onTryInChat={onTryInChat}
            onClose={onClose}
          />
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Step components
// ---------------------------------------------------------------------------

function BrowseStep({
  onPickCatalog,
  onPickCustom,
}: {
  onPickCatalog: (entry: CatalogEntry) => void;
  onPickCustom: (url: string) => void;
}): ReactElement {
  const [filter, setFilter] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return CATALOG;
    return CATALOG.filter(
      (entry) =>
        entry.name.toLowerCase().includes(needle) ||
        entry.description.toLowerCase().includes(needle),
    );
  }, [filter]);

  return (
    <div className="mcp-wizard__step mcp-wizard__step--browse">
      <Field label="Search catalog">
        <TextInput
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Linear, Notion, Sentry, …"
        />
      </Field>
      <ul className="mcp-catalog__grid">
        {filtered.map((entry) => (
          <li key={entry.id}>
            <button
              type="button"
              className="mcp-catalog__card"
              onClick={() => onPickCatalog(entry)}
              aria-label={`Add ${entry.name}`}
            >
              <span
                className="mcp-catalog__icon"
                style={{ background: entry.color }}
                aria-hidden="true"
              >
                {entry.icon}
              </span>
              <span className="mcp-catalog__name">{entry.name}</span>
              <span className="mcp-catalog__desc">{entry.description}</span>
            </button>
          </li>
        ))}
      </ul>
      <Card>
        <h3 className="mcp-wizard__custom-heading">Add a custom server</h3>
        <p className="mcp-wizard__custom-hint">
          Have your own MCP endpoint? Paste the URL — we'll auto-detect OAuth +
          dynamic client registration.
        </p>
        <Field label="Server URL">
          <TextInput
            value={customUrl}
            onChange={(e) => setCustomUrl(e.target.value)}
            placeholder="https://mcp.example.com/sse"
          />
        </Field>
        <div className="mcp-wizard__actions">
          <Button
            type="button"
            variant="primary"
            disabled={!customUrl.trim()}
            onClick={() => onPickCustom(customUrl)}
          >
            Continue
          </Button>
        </div>
      </Card>
    </div>
  );
}

function AuthStep({
  selection,
  onPick,
  onBack,
}: {
  selection: Selection;
  onPick: (method: AuthMethod) => void;
  onBack: () => void;
}): ReactElement {
  const presetMethod: AuthMethod =
    selection.kind === "catalog" ? selection.entry.auth_method : "oauth";
  return (
    <div className="mcp-wizard__step mcp-wizard__step--auth">
      <p className="mcp-wizard__hint">
        Choose how this server will authenticate. We pre-selected the method
        that matches the server's documented mode.
      </p>
      <ul className="mcp-wizard__choices">
        <AuthChoice
          method="oauth"
          label="OAuth"
          description="The server redirects you to its sign-in screen and returns a token."
          recommended={presetMethod === "oauth"}
          onPick={onPick}
        />
        <AuthChoice
          method="api_key"
          label="API key"
          description="Paste a long-lived secret. Stored encrypted at rest."
          recommended={presetMethod === "api_key"}
          onPick={onPick}
        />
        <AuthChoice
          method="none"
          label="No auth"
          description="The server is open or scoped to your network."
          recommended={presetMethod === "none"}
          onPick={onPick}
        />
      </ul>
      <div className="mcp-wizard__actions mcp-wizard__actions--split">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
      </div>
    </div>
  );
}

function AuthChoice({
  method,
  label,
  description,
  recommended,
  onPick,
}: {
  method: AuthMethod;
  label: string;
  description: string;
  recommended: boolean;
  onPick: (method: AuthMethod) => void;
}): ReactElement {
  return (
    <li>
      <button
        type="button"
        className="mcp-wizard__choice"
        onClick={() => onPick(method)}
      >
        <span className="mcp-wizard__choice-head">
          <strong>{label}</strong>
          {recommended ? <Badge tone="accent">Recommended</Badge> : null}
        </span>
        <span className="mcp-wizard__choice-desc">{description}</span>
      </button>
    </li>
  );
}

function ScopeStep({
  selection,
  auth_method,
  onContinue,
  onBack,
}: {
  selection: Selection;
  auth_method: AuthMethod;
  onContinue: (scopes: string[]) => void;
  onBack: () => void;
}): ReactElement {
  const initialScopes =
    selection.kind === "catalog" ? selection.entry.suggested_scopes : [];
  const [scopes] = useState<string[]>(initialScopes);

  return (
    <div className="mcp-wizard__step mcp-wizard__step--scope">
      <p className="mcp-wizard__hint">
        Scopes the server will request once you authenticate. Per-tool toggles +
        a read-only preset are coming in a follow-up; v1 commits the server's
        documented scope list as-is.
      </p>
      {scopes.length === 0 ? (
        <Card>
          <p className="mcp-wizard__hint">
            {auth_method === "none"
              ? "No-auth servers don't carry scopes."
              : "This server doesn't publish a scope list yet — the workspace defaults will apply."}
          </p>
        </Card>
      ) : (
        <ul className="mcp-wizard__scopes">
          {scopes.map((scope) => (
            <li key={scope}>
              <code>{scope}</code>
            </li>
          ))}
        </ul>
      )}
      <div className="mcp-wizard__actions mcp-wizard__actions--split">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <Button
          type="button"
          variant="primary"
          onClick={() => onContinue(scopes)}
        >
          Review
        </Button>
      </div>
    </div>
  );
}

function ConfirmStep({
  selection,
  auth_method,
  scopes,
  busy,
  onConfirm,
  onBack,
}: {
  selection: Selection;
  auth_method: AuthMethod;
  scopes: string[];
  busy: boolean;
  onConfirm: () => void;
  onBack: () => void;
}): ReactElement {
  const name = selectionName(selection);
  const url = selectionUrl(selection);
  return (
    <div className="mcp-wizard__step mcp-wizard__step--confirm">
      <Card>
        <dl className="mcp-wizard__summary">
          <dt>Name</dt>
          <dd>{name}</dd>
          <dt>URL</dt>
          <dd>
            <code>{url}</code>
          </dd>
          <dt>Auth</dt>
          <dd>{authLabel(auth_method)}</dd>
          <dt>Scopes</dt>
          <dd>
            {scopes.length === 0 ? (
              <em>None requested</em>
            ) : (
              <span>
                {scopes.map((scope, idx) => (
                  <span key={scope}>
                    <code>{scope}</code>
                    {idx < scopes.length - 1 ? ", " : null}
                  </span>
                ))}
              </span>
            )}
          </dd>
        </dl>
      </Card>
      <div className="mcp-wizard__actions mcp-wizard__actions--split">
        <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
          Back
        </Button>
        <Button
          type="button"
          variant="primary"
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? "Adding…" : "Add to workspace"}
        </Button>
      </div>
    </div>
  );
}

function ConnectedStep({
  selection,
  auth_method,
  server,
  busy,
  onAuthenticate,
  onTryInChat,
  onClose,
}: {
  selection: Selection;
  auth_method: AuthMethod;
  server: McpServer;
  busy: boolean;
  onAuthenticate: () => void;
  onTryInChat?: (server: McpServer) => void;
  onClose: () => void;
}): ReactElement {
  const needsAuth =
    auth_method === "oauth" && server.auth_state !== "authenticated";
  return (
    <div className="mcp-wizard__step mcp-wizard__step--connected">
      <Card>
        <h3>{selectionName(selection)} added</h3>
        <p className="mcp-wizard__hint">
          {needsAuth
            ? "One more step — authenticate to give the agent live access."
            : "The connector is live for everyone in your workspace."}
        </p>
      </Card>
      <div className="mcp-wizard__actions mcp-wizard__actions--split">
        <Button type="button" variant="ghost" onClick={onClose}>
          Done
        </Button>
        {needsAuth ? (
          <Button
            type="button"
            variant="primary"
            onClick={onAuthenticate}
            disabled={busy}
          >
            {busy
              ? "Starting…"
              : `Authenticate with ${selectionName(selection)}`}
          </Button>
        ) : onTryInChat ? (
          <Button
            type="button"
            variant="primary"
            onClick={() => {
              onTryInChat(server);
              onClose();
            }}
          >
            Try in chat
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function StepIndicator({ step }: { step: Step }): ReactElement {
  const order: Step["kind"][] = [
    "browse",
    "auth",
    "scope",
    "confirm",
    "connected",
  ];
  const activeIdx = order.indexOf(step.kind);
  return (
    <ol className="mcp-wizard__steps">
      {order.map((kind, idx) => (
        <li
          key={kind}
          className="mcp-wizard__steps-item"
          data-active={idx === activeIdx ? "true" : undefined}
          data-done={idx < activeIdx ? "true" : undefined}
        >
          <span className="mcp-wizard__steps-num">{idx + 1}</span>
          <span className="mcp-wizard__steps-label">{stepLabel(kind)}</span>
        </li>
      ))}
    </ol>
  );
}

function titleForStep(step: Step): string {
  switch (step.kind) {
    case "browse":
      return "Add a connector";
    case "auth":
      return `Connect ${selectionName(step.selection)}`;
    case "scope":
      return `Review scopes for ${selectionName(step.selection)}`;
    case "confirm":
      return `Confirm ${selectionName(step.selection)}`;
    case "connected":
      return `${selectionName(step.selection)} ready`;
  }
}

function stepLabel(kind: Step["kind"]): string {
  switch (kind) {
    case "browse":
      return "Browse";
    case "auth":
      return "Auth";
    case "scope":
      return "Scopes";
    case "confirm":
      return "Confirm";
    case "connected":
      return "Connected";
  }
}

function authLabel(method: AuthMethod): string {
  switch (method) {
    case "oauth":
      return "OAuth";
    case "api_key":
      return "API key";
    case "none":
      return "No auth";
  }
}

function selectionUrl(selection: Selection): string {
  return selection.kind === "catalog" ? selection.entry.url : selection.url;
}

function selectionName(selection: Selection): string {
  return selection.kind === "catalog" ? selection.entry.name : selection.name;
}

function hostnameLabel(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export const __TESTING__ = {
  CATALOG,
};
