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
  type FormEvent as ReactFormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type {
  ConnectorAccessMode,
  ConnectorCatalogEntry,
  ConnectorSlug,
  McpOAuthClientConfigRequest,
} from "@0x-copilot/api-types";
import { DESKTOP_CONNECTOR_DEEP_LINK_URI } from "@0x-copilot/api-types";

import type { ConnectCallbackMode } from "./useConnectFlow";
import {
  AppIcon,
  Button,
  Field,
  Spinner,
  TextInput,
} from "@0x-copilot/design-system";

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
  /**
   * Abort the authorization in flight (the OAuth step's Cancel). Absent ⇒ the
   * host cannot really stop its flow, and Cancel only closes the dialog — which
   * is what this used to do unconditionally, while main kept running.
   */
  readonly onCancelAuthorize?: () => void;
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
  /**
   * Set when the last connect stopped because the connector needs a
   * pre-registered OAuth client (`ConnectFlow.clientRequiredSlug`). The modal
   * shows its client form for that slug instead of an error the user cannot
   * act on. This is deliberately NOT folded into `error`: the two demand
   * different UI, and collapsing them is how "needs a client_id" became
   * indistinguishable from "something went wrong".
   */
  readonly clientRequiredSlug?: ConnectorSlug | null;
  /**
   * Entry to pick the moment the modal opens (`ConnectFlow.initialEntrySlug`),
   * set when the user hit Connect on a catalog ROW rather than the CTA. It runs
   * the identical pick the catalog step would have run, so there is one flow
   * and not a second, quieter one that skips the access-mode question.
   */
  readonly initialEntrySlug?: ConnectorSlug | null;
  /** Retry the blocked connect with the supplied client. */
  readonly onSubmitOAuthClient?: (
    client: McpOAuthClientConfigRequest,
    callbackMode?: ConnectCallbackMode,
  ) => void;
  /**
   * Open the pinned escape-hatch row's destination — "Manage MCP", the whole
   * configuration as one editable document. Both apps pass it. The modal used
   * to own a built-in URL form here instead, but that form could express
   * exactly one kind of server (remote, no credential, no headers) while the
   * row opening it advertised "paste a JSON config — stdio or remote"; the
   * document editor is what keeps that promise. Omit to hide the row entirely.
   */
  readonly onManageMcp?: () => void;
}

type ConnectPhase = "catalog" | "oauth" | "client" | "permission";

const PHASE_STEP: Record<ConnectPhase, number> = {
  catalog: 1,
  oauth: 2,
  // Same dot as `oauth`: supplying a client is not an extra step in the user's
  // journey, it is the authorization step failing in a way they can resolve.
  client: 2,
  permission: 3,
};

// ---------------------------------------------------------------------------
// ConnectModal
// ---------------------------------------------------------------------------

export function ConnectModal({
  open,
  onClose,
  onCancelAuthorize,
  catalog,
  onSelectEntry,
  onConnect,
  pending = false,
  error = null,
  clientRequiredSlug = null,
  initialEntrySlug = null,
  onSubmitOAuthClient,
  onManageMcp,
}: ConnectModalProps): ReactElement | null {
  const [phase, setPhase] = useState<ConnectPhase>("catalog");
  const [selected, setSelected] = useState<ConnectorCatalogEntry | null>(null);
  const [permission, setPermission] = useState<ConnectPermission>("read");
  // Held in a ref so the open-effect can fire the initial pick without making
  // an unstable callback prop a dependency of a reset.
  const onSelectEntryRef = useRef(onSelectEntry);
  onSelectEntryRef.current = onSelectEntry;

  // Reset the flow whenever the modal is (re)opened. When it was opened FOR a
  // specific entry, run that entry's pick here rather than showing the catalog
  // — same state transition the catalog step performs, so nothing downstream
  // can tell which control started it.
  useEffect(() => {
    if (!open) return;
    setPermission("read");
    const entry =
      initialEntrySlug === null
        ? undefined
        : catalog.find((row) => row.slug === initialEntrySlug);
    if (entry !== undefined) {
      setSelected(entry);
      setPhase("oauth");
      onSelectEntryRef.current?.(entry.slug);
      return;
    }
    setSelected(null);
    setPhase("catalog");
    // NOTE: `catalog` and `onSelectEntry` are deliberately NOT dependencies.
    // Both are fresh identities on every parent render, and depending on either
    // would re-run this reset — restarting the OAuth round-trip — on any
    // unrelated re-render while the modal is open. `onSelectEntry` is reached
    // through a ref for exactly that reason; `catalog` is only read to resolve
    // the initial slug, which cannot change without `initialEntrySlug` changing.
  }, [open, initialEntrySlug]);

  // OAuth success is host-driven: once the host has cleared `pending` with no
  // `error`, authorization succeeded and the permission choice is revealed.
  // Errors keep us on the OAuth step (Back / Retry).
  useEffect(() => {
    // `clientRequiredSlug` must gate this. A blocked-on-client connect clears
    // BOTH `pending` and `error` — that is the correct flow state, since the
    // attempt is over and there is nothing to report — and this effect reads
    // exactly that pair as success. Without the guard the modal would jump to
    // the permission step for a connector that never authorized anything.
    if (
      phase === "oauth" &&
      !pending &&
      error === null &&
      clientRequiredSlug === null
    ) {
      setPhase("permission");
    }
  }, [phase, pending, error, clientRequiredSlug]);

  // The host reported that this connector needs a pre-registered client — show
  // the form rather than leaving the spinner spinning on a finished attempt.
  useEffect(() => {
    if (clientRequiredSlug !== null && phase === "oauth") {
      setPhase("client");
    }
  }, [clientRequiredSlug, phase]);

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

  // Cancel during the OAuth step has to ABORT, not just close. Closing alone
  // left main's loopback armed for its full timeout, so a user who cancelled
  // and then approved anyway in the still-open browser tab ended up connected —
  // the opposite of what the button told them. Closing after is still right:
  // the dialog has nothing left to show.
  const cancelAuthorization = useCallback(() => {
    onCancelAuthorize?.();
    onClose();
  }, [onCancelAuthorize, onClose]);

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
            onClick={cancelAuthorization}
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

  // Design subtitle states the TRUST MODEL, not a task (copilot-flows.jsx:455).
  const subtitle =
    selected !== null
      ? selected.display_name
      : "the agent acts through your accounts";

  // Header identity tile. A picked connector shows its per-slug neutral tile;
  // the unselected catalog step shows a neutral plug glyph (PRD-11 D7).
  const logo =
    selected !== null ? (
      <AppIcon name={selected.slug} size="tile" tone="neutral" />
    ) : (
      <span aria-hidden="true">🔌</span>
    );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Connect a tool"
      subtitle={subtitle}
      logo={logo}
      footer={footer}
    >
      {phase === "catalog" ? (
        <CatalogStep
          catalog={catalog}
          onPick={handlePick}
          onAddCustom={onManageMcp}
        />
      ) : null}
      {phase === "oauth" ? (
        <OAuthStep name={selected?.display_name ?? "the tool"} error={error} />
      ) : null}
      {phase === "client" ? (
        <OAuthClientStep
          name={selected?.display_name ?? "the tool"}
          onSubmit={(client, callbackMode) =>
            onSubmitOAuthClient?.(client, callbackMode)
          }
        />
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
  onAddCustom,
}: {
  readonly catalog: readonly ConnectorCatalogEntry[];
  readonly onPick: (entry: ConnectorCatalogEntry) => void;
  /** When set, a trailing pinned "Custom MCP server" row opens Manage MCP. */
  readonly onAddCustom?: () => void;
}): ReactElement {
  const customRow =
    onAddCustom !== undefined ? (
      // PRD-11 D7 — the escape hatch is PINNED, not dashed (the design's
      // .mrow--dash is dead CSS after the cascade; only the sticky treatment
      // survives). Full-bleed against Modal's 15px body pad.
      //
      // The sticky lives on the <li>, NOT on the button inside it. A sticky box
      // is constrained by its CONTAINING BLOCK, and the <li> is exactly as tall
      // as the button — so the button had nowhere to shift to and this row just
      // sat last in the list, below the fold of a catalog with a dozen-plus
      // entries. On the <li> the containing block is the whole <ul>, which is
      // what lets it float above the scrolling rows.
      <li style={customPickRowItemStyle}>
        <button
          type="button"
          style={customPickRowStyle}
          onClick={onAddCustom}
          data-testid="connect-catalog-custom"
        >
          <span aria-hidden="true" style={customGlyphStyle}>
            {"{ }"}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={pickNameStyle}>Manage MCP</span>
            <span style={pickSubStyle}>
              edit the JSON config — stdio or remote
            </span>
          </span>
          <span aria-hidden="true" style={chevronStyle}>
            ›
          </span>
        </button>
      </li>
    ) : null;

  if (catalog.length === 0) {
    if (onAddCustom === undefined) {
      return (
        <p style={mutedNoteStyle} data-testid="connect-catalog-empty">
          No connectors are available to add right now.
        </p>
      );
    }
    return (
      <ul style={listStyle} data-testid="connect-catalog-list">
        {customRow}
      </ul>
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
            <AppIcon name={entry.slug} tone="neutral" style={mrowLogoStyle} />
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
      {customRow}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// OAuth-client step — reached only when the backend answered
// `connector_oauth_client_required`.
//
// This is the whole point of that error being distinct. Atlassian, Google and
// Microsoft expose neither RFC 8414 metadata nor RFC 7591 dynamic client
// registration, so the connect flow physically cannot invent a `client_id`;
// before this step the attempt just failed and the user had no way to fix it
// from the product.
// ---------------------------------------------------------------------------

function OAuthClientStep({
  name,
  onSubmit,
}: {
  readonly name: string;
  readonly onSubmit: (
    client: McpOAuthClientConfigRequest,
    callbackMode: ConnectCallbackMode,
  ) => void;
}): ReactElement {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scope, setScope] = useState("");
  const [callbackMode, setCallbackMode] =
    useState<ConnectCallbackMode>("loopback");
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = (event: ReactFormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    try {
      const client = oauthClientFromForm({
        clientId,
        clientSecret,
        scope,
        authorizationEndpoint: "",
        tokenEndpoint: "",
      });
      if (client === undefined) {
        setFormError("An OAuth client ID is required.");
        return;
      }
      setFormError(null);
      onSubmit(client, callbackMode);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not save the OAuth client.",
      );
    }
  };

  return (
    <form
      style={customFormStyle}
      onSubmit={handleSubmit}
      data-testid="connect-client-form"
    >
      {/* Muted, NOT the alert style: this is what to do next, not a failure.
          Rendering guidance in the alert colour makes a resolvable step look
          like the dead end it exists to replace. */}
      <p style={mutedNoteStyle} role="status">
        {name} doesn&apos;t support automatic app registration, so it needs an
        OAuth client you register with them. Paste it once — it&apos;s stored
        encrypted and reused for every later connect.
      </p>
      <Field label="OAuth client ID">
        <TextInput
          autoComplete="off"
          value={clientId}
          onChange={(event) => setClientId(event.target.value)}
          placeholder="client_id"
          required
        />
      </Field>
      <Field
        label="OAuth client secret"
        hint="Leave empty for a public (PKCE-only) client."
      >
        <TextInput
          type="password"
          autoComplete="new-password"
          value={clientSecret}
          onChange={(event) => setClientSecret(event.target.value)}
          placeholder="client_secret"
        />
      </Field>
      <Field
        label="OAuth scope"
        hint="Optional — overrides the discovered scope."
      >
        <TextInput
          autoComplete="off"
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          placeholder="e.g. read:jira-work"
        />
      </Field>
      {/* The redirect must MATCH what was registered with the provider, and the
          two are not interchangeable. Loopback varies its port per attempt,
          which providers that demand one exact callback URL reject outright —
          so offering only loopback made those providers unreachable no matter
          what client was supplied. */}
      <Field
        label="Redirect URI you registered"
        hint="Must match the provider app exactly."
      >
        <select
          value={callbackMode}
          onChange={(event) =>
            setCallbackMode(event.target.value as ConnectCallbackMode)
          }
          style={selectStyle}
          data-testid="connect-client-callback-mode"
        >
          <option value="loopback">
            http://127.0.0.1:&lt;any port&gt;/connectors/oauth/cb
          </option>
          <option value="deep_link">{DESKTOP_CONNECTOR_DEEP_LINK_URI}</option>
        </select>
      </Field>
      {formError !== null ? (
        <p
          style={alertNoteStyle}
          role="alert"
          data-testid="connect-client-error"
        >
          {formError}
        </p>
      ) : null}
      <Button
        variant="primary"
        type="submit"
        data-testid="connect-client-submit"
      >
        Save and continue
      </Button>
    </form>
  );
}

function oauthClientFromForm({
  clientId,
  clientSecret,
  scope,
  authorizationEndpoint,
  tokenEndpoint,
}: {
  clientId: string;
  clientSecret: string;
  scope: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
}): McpOAuthClientConfigRequest | undefined {
  const trimmedClientId = clientId.trim();
  const trimmedClientSecret = clientSecret.trim();
  const trimmedScope = scope.trim();
  const trimmedAuthorizationEndpoint = authorizationEndpoint.trim();
  const trimmedTokenEndpoint = tokenEndpoint.trim();
  const hasOAuthConfig = [
    trimmedClientId,
    trimmedClientSecret,
    trimmedScope,
    trimmedAuthorizationEndpoint,
    trimmedTokenEndpoint,
  ].some(Boolean);
  if (!hasOAuthConfig) {
    return undefined;
  }
  if (!trimmedClientId) {
    throw new Error(
      "OAuth client ID is required when OAuth settings are provided.",
    );
  }
  return {
    client_id: trimmedClientId,
    ...(trimmedClientSecret
      ? {
          client_secret: trimmedClientSecret,
          token_endpoint_auth_method: "client_secret_post",
        }
      : { token_endpoint_auth_method: "none" }),
    ...(trimmedScope ? { scope: trimmedScope } : {}),
    ...(trimmedAuthorizationEndpoint
      ? { authorization_endpoint: trimmedAuthorizationEndpoint }
      : {}),
    ...(trimmedTokenEndpoint ? { token_endpoint: trimmedTokenEndpoint } : {}),
  };
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
      {/* The wrapper owns `role="status"`, so the ring stays decorative. */}
      <Spinner size={24} stroke={2} />
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

// PRD-11 D7 — the design `.mrow` (copilot.css:2350-2364): padding 10px, 1px
// --line2 (== --color-border-strong) border, radius 8px, bg --ink2
// (== --color-bg-elevated), gap 10px.
const pickRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  padding: 10,
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border-strong)",
  backgroundColor: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  font: "inherit",
  textAlign: "left",
  cursor: "pointer",
  transition: "background-color var(--duration-fast) var(--ease-standard)",
};

// The escape hatch, ported from the design's `.mrow--pin` (copilot.css:514):
// `position: sticky; bottom: -15px; margin: 10px -15px -15px` — full-bleed
// against the Modal body's 15px pad (settings/Modal.tsx bodyStyle), reading as
// a BAR rather than a card: no radius, no box border, one top hairline
// (`--line` == --color-border), on `--ink2` == --color-bg-elevated.
//
// Positioning is on the <li> so the containing block is the list; the button
// keeps the chrome. See `customRow` for why that split matters.
const customPickRowItemStyle: CSSProperties = {
  position: "sticky",
  bottom: -15,
  margin: "0 -15px -15px",
  borderTop: "1px solid var(--color-border)",
  // Opaque: the rows above scroll UNDER this bar.
  backgroundColor: "var(--color-bg-elevated)",
};

const customPickRowStyle: CSSProperties = {
  ...pickRowStyle,
  padding: "12px 15px",
  borderRadius: 0,
  border: 0,
  backgroundColor: "transparent",
};

// 28×28 neutral tile (design `.mrow__logo`, radius 7 ≈ --radius-md). Passed to
// AppIcon so the base 20px circle is overridden and the neutral class owns the
// --panel3 / --tx2 chrome; the custom row's mono `{ }` glyph reuses the size.
const mrowLogoStyle: CSSProperties = {
  width: 28,
  height: 28,
  borderRadius: "var(--radius-md)",
  fontSize: "var(--font-size-2xs)",
};

const customGlyphStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "grid",
  placeItems: "center",
  width: 28,
  height: 28,
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface-elevated)",
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
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

const selectStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  font: "12.5px/1.4 var(--font-mono)",
};

const customFormStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
};
