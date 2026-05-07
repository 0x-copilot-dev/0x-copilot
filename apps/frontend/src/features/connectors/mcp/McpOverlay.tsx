// PR 4.4.6 — Manage MCP servers modal.
//
// Two tabs:
//   - Catalog: server-driven grid (`useMcpCatalog`). Each card cross-
//     references the user's installed servers. Install / Resume install /
//     Installed states. Inline credentials form for vendors that require
//     a pre-registered OAuth client.
//   - Connected: full management (re-auth, skip auth, remove) via the
//     existing `ConnectorRow` for every authorized server.
//
// Replaces the 5-step wizard from PR 4.4. Reuses primitives from the
// design-system; the tabs primitive is feature-local (~30 LOC) since
// it's the only consumer in the app today.

import {
  AppIcon,
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import "./mcp-wizard.css";
import {
  type FormEvent,
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  McpCatalogEntry,
  McpOAuthClientConfigRequest,
  McpServer,
} from "@enterprise-search/api-types";
import { Modal } from "../../settings/Modal";
import { isOAuthSetupRequired } from "../../../api/mcpErrors";
import { ConnectorRow } from "../ConnectorRow";
import { isAuthenticated } from "../authStateDisplay";
import type { ConnectorState } from "../useConnectors";
import { useMcpCatalog } from "../useMcpCatalog";
import { useDiscoverablePref } from "../useDiscoverablePref";

export interface McpOverlayProps {
  open: boolean;
  onClose: () => void;
  connectors: ConnectorState;
  /**
   * PR 4.4.7 Phase 2 (Slice C) — when set, the modal opens on the
   * Catalog tab and scrolls the matching catalog card into view. Used
   * by the chat surface's progressive-discovery flow: the agent
   * suggests Linear via ``suggest_mcp_connector``, the user clicks
   * Connect, the chat opens this modal with ``installSlug='linear'``
   * so the user lands directly on the right install button.
   */
  installSlug?: string | null;
}

type TabKey = "catalog" | "connected";

export function McpOverlay({
  open,
  onClose,
  connectors,
  installSlug,
}: McpOverlayProps): ReactElement {
  const [tab, setTab] = useState<TabKey>("catalog");
  const catalog = useMcpCatalog();

  // Reset to the Catalog tab whenever the modal opens so a re-open
  // doesn't strand the user on an empty Connected tab.
  useEffect(() => {
    if (open) {
      setTab("catalog");
    }
  }, [open]);

  // Tab badge counts every added server so a manually-added URL still
  // pending OAuth (or a seed install the user never finished) is
  // discoverable. The page-level "N active" pill outside the modal
  // continues to count only authenticated servers.
  const connectedCount = connectors.servers.length;

  return (
    <Modal open={open} onClose={onClose} title="Manage MCP servers" size="lg">
      <Tabs
        value={tab}
        onChange={setTab}
        catalogCount={catalog.entries.length}
        connectedCount={connectedCount}
      />
      {tab === "catalog" ? (
        <CatalogTab
          catalog={catalog}
          connectors={connectors}
          installSlug={installSlug ?? null}
        />
      ) : (
        <ConnectedTab connectors={connectors} />
      )}
    </Modal>
  );
}

// --- Tabs primitive --------------------------------------------------------

function Tabs({
  value,
  onChange,
  catalogCount,
  connectedCount,
}: {
  value: TabKey;
  onChange: (value: TabKey) => void;
  catalogCount: number;
  connectedCount: number;
}): ReactElement {
  return (
    <div className="mcp-tabs" role="tablist" aria-label="MCP servers view">
      <Tab
        value="catalog"
        current={value}
        onChange={onChange}
        count={catalogCount}
      >
        Catalog
      </Tab>
      <Tab
        value="connected"
        current={value}
        onChange={onChange}
        count={connectedCount}
      >
        Connected
      </Tab>
    </div>
  );
}

function Tab({
  value,
  current,
  onChange,
  count,
  children,
}: {
  value: TabKey;
  current: TabKey;
  onChange: (value: TabKey) => void;
  count: number;
  children: ReactNode;
}): ReactElement {
  const active = current === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={
        active ? "mcp-tabs__btn mcp-tabs__btn--active" : "mcp-tabs__btn"
      }
      onClick={() => onChange(value)}
    >
      <span>{children}</span>
      <span className="mcp-tabs__count" aria-hidden="true">
        {count}
      </span>
    </button>
  );
}

// --- Catalog tab -----------------------------------------------------------

function CatalogTab({
  catalog,
  connectors,
  installSlug,
}: {
  catalog: ReturnType<typeof useMcpCatalog>;
  connectors: ConnectorState;
  /** Slug to scroll-and-highlight on first render (PR 4.4.7 deep-link). */
  installSlug: string | null;
}): ReactElement {
  const [search, setSearch] = useState("");

  // Cross-reference catalog entries with installed servers by stable
  // ``server_id == "seed:" + slug``. The same map drives the
  // Install / Resume install / Installed CTA on each card.
  const serversBySlug = useMemo(() => {
    const map = new Map<string, McpServer>();
    for (const server of connectors.servers) {
      if (server.server_id.startsWith("seed:")) {
        map.set(server.server_id.slice("seed:".length), server);
      }
    }
    return map;
  }, [connectors.servers]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return catalog.entries;
    }
    return catalog.entries.filter((entry) =>
      entry.display_name.toLowerCase().includes(needle),
    );
  }, [catalog.entries, search]);

  return (
    <div className="mcp-catalog">
      <div className="mcp-catalog__head">
        <TextInput
          className="mcp-catalog__search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search Linear, Notion, Sentry, …"
          aria-label="Search catalog"
        />
        <button
          type="button"
          className="mcp-catalog__refresh"
          aria-label="Refresh catalog"
          onClick={() => void catalog.refresh()}
        >
          Refresh
        </button>
      </div>

      {catalog.loading && catalog.entries.length === 0 ? (
        <p className="mcp-catalog__hint">Loading catalog…</p>
      ) : null}
      {catalog.error ? (
        <Card className="mcp-catalog__error">
          <h4>Catalog unavailable</h4>
          <p>
            We couldn&apos;t load the curated MCP server list. The Custom URL
            card below still works — paste a server URL to install.
          </p>
          <p className="mcp-catalog__error-detail">{catalog.error}</p>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void catalog.refresh()}
          >
            Try again
          </Button>
        </Card>
      ) : null}

      <div className="mcp-catalog__grid">
        {filtered.map((entry) => (
          <CatalogCard
            key={entry.slug}
            entry={entry}
            installed={serversBySlug.get(entry.slug) ?? null}
            connectors={connectors}
            highlight={installSlug !== null && entry.slug === installSlug}
          />
        ))}
        <CustomUrlCard connectors={connectors} />
      </div>
    </div>
  );
}

// --- Catalog card ----------------------------------------------------------

type InstallStatus =
  | { kind: "install" }
  | { kind: "resume"; serverId: string }
  | { kind: "installed"; serverId: string };

function statusFor(installed: McpServer | null): InstallStatus {
  if (!installed) {
    return { kind: "install" };
  }
  if (isAuthenticated(installed.auth_state)) {
    return { kind: "installed", serverId: installed.server_id };
  }
  return { kind: "resume", serverId: installed.server_id };
}

function CatalogCard({
  entry,
  installed,
  connectors,
  highlight,
}: {
  entry: McpCatalogEntry;
  installed: McpServer | null;
  connectors: ConnectorState;
  /** PR 4.4.7 Phase 2 (Slice C) — chat deep-linked this slug; scroll +
   *  pulse so the user lands on the right card. */
  highlight: boolean;
}): ReactElement {
  const status = statusFor(installed);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Inline credentials form — opens automatically for vendors that
  // require a pre-registered OAuth client, or after an auth-start
  // attempt fails with ``OAuthSetupRequiredError``.
  const [setupOpen, setSetupOpen] = useState(false);
  const cardRef = useRef<HTMLElement | null>(null);
  // Scroll the highlighted card into view on first paint after the
  // catalog grid mounts. The catalog endpoint resolves before mount
  // when the cache is warm, so a single ``useEffect`` keyed on
  // ``highlight`` is enough — no observer wiring required.
  useEffect(() => {
    if (!highlight || cardRef.current === null) return;
    cardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlight]);
  // PR 4.4.7 — Phase 1 toggle for progressive discovery. Reads the
  // catalog default and overlays a per-user override from local
  // storage. No runtime effect yet (Phase 2 wires it).
  const discoverable = useDiscoverablePref(
    entry.slug,
    entry.discoverable ?? true,
  );

  async function handlePrimary(): Promise<void> {
    if (pending) {
      return;
    }
    if (status.kind === "install" && entry.requires_pre_registered_client) {
      // Force the credentials form for known-pre-registered vendors.
      setSetupOpen(true);
      return;
    }
    try {
      setPending(true);
      setError(null);
      if (status.kind === "install") {
        const server = await connectors.installFromCatalog(entry.slug);
        await connectors.authenticate(server.server_id);
      } else if (status.kind === "resume") {
        await connectors.authenticate(status.serverId);
      }
      // ``installed`` branch is greyed; nothing to do here.
    } catch (err) {
      if (isOAuthSetupRequired(err)) {
        // Auth-server doesn't advertise discovery — open the form.
        setSetupOpen(true);
      } else {
        setError(err instanceof Error ? err.message : "Could not install.");
      }
    } finally {
      setPending(false);
    }
  }

  async function handleSetupSubmit(
    oauthClient: McpOAuthClientConfigRequest,
  ): Promise<void> {
    try {
      setPending(true);
      setError(null);
      const server = await connectors.installFromCatalog(
        entry.slug,
        oauthClient,
      );
      await connectors.authenticate(server.server_id);
      setSetupOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not install.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <article
        ref={cardRef}
        className={highlight ? "mcp-card mcp-card--highlight" : "mcp-card"}
        data-status={status.kind}
        aria-label={`${entry.display_name} catalog card`}
      >
        {/* Pass slug only — BRAND_GLYPHS in the design-system maps every
            catalog vendor to its surface + symbol. Passing `color` would
            short-circuit the map and force a generic first-letter chip. */}
        <AppIcon
          name={entry.slug}
          logoUrl={entry.logo_url ?? null}
          size="lg"
          className="mcp-card__icon"
        />
        <div className="mcp-card__main">
          <div className="mcp-card__title-row">
            <h4 className="mcp-card__title">{entry.display_name}</h4>
            {entry.verified ? (
              <Badge tone="success" className="mcp-card__pill">
                Verified
              </Badge>
            ) : null}
            {entry.requires_pre_registered_client ? (
              <span
                className="mcp-card__setup-note"
                title="Requires a pre-registered OAuth client"
              >
                · Setup required
              </span>
            ) : null}
          </div>
          <p className="mcp-card__desc">
            {entry.scopes_summary ?? entry.description}
          </p>
          {error ? <p className="app-error mcp-card__error">{error}</p> : null}
          <DiscoverableToggle
            slug={entry.slug}
            displayName={entry.display_name}
            enabled={discoverable.enabled}
            onChange={discoverable.setEnabled}
          />
        </div>
        <CatalogCardCta
          status={status}
          pending={pending}
          entry={entry}
          onPrimary={() => void handlePrimary()}
        />
      </article>

      <SetupModal
        open={setupOpen}
        entry={entry}
        submitting={pending}
        onSubmit={(payload) => void handleSetupSubmit(payload)}
        onClose={() => setSetupOpen(false)}
      />
    </>
  );
}

// PR 4.4.7 — small in-card toggle that lets the user mute or unmute a
// catalog entry's progressive-discovery suggestions. Phase 1: state
// persists in localStorage and has no runtime effect yet. Phase 2 will
// move the persistence to the backend and have the agent's "what could
// I help with?" path consult it. Rendering as a tiny inline pair
// (label + native checkbox styled as a switch) keeps the card height
// uniform across rows.
function DiscoverableToggle({
  slug,
  displayName,
  enabled,
  onChange,
}: {
  slug: string;
  displayName: string;
  enabled: boolean;
  onChange: (next: boolean) => void;
}): ReactElement {
  const id = `mcp-discoverable-${slug}`;
  return (
    <label className="mcp-card__discoverable" htmlFor={id}>
      <input
        id={id}
        type="checkbox"
        checked={enabled}
        onChange={(event) => onChange(event.target.checked)}
        aria-label={`Discoverable: ${displayName}`}
      />
      <span className="mcp-card__discoverable-label">
        Discoverable
        <span
          className="mcp-card__discoverable-hint"
          title="When on, the agent may suggest this connector even before you sign in. Coming soon — toggle now to set your preference."
        >
          {" "}
          · suggest in chat
        </span>
      </span>
    </label>
  );
}

function CatalogCardCta({
  status,
  pending,
  entry,
  onPrimary,
}: {
  status: InstallStatus;
  pending: boolean;
  entry: McpCatalogEntry;
  onPrimary: () => void;
}): ReactElement {
  if (status.kind === "installed") {
    return (
      <Badge tone="neutral" className="mcp-card__cta-badge">
        Installed
      </Badge>
    );
  }
  return (
    <button
      type="button"
      className="mcp-card__cta"
      disabled={pending}
      aria-label={
        status.kind === "resume"
          ? `Resume ${entry.display_name} install`
          : `Install ${entry.display_name}`
      }
      onClick={onPrimary}
    >
      {pending ? "Working…" : status.kind === "resume" ? "Resume" : "Install"}
    </button>
  );
}

// --- Setup modal (pre-registered OAuth client) ----------------------------
//
// Lifted out of CatalogCard in PR 4.4.6.1 — expanding the form inline made
// one card 3× taller than the others, breaking the grid and pushing the
// install button below the fold. A separate dialog keeps every catalog
// card the same height.

function SetupModal({
  open,
  entry,
  submitting,
  onSubmit,
  onClose,
}: {
  open: boolean;
  entry: McpCatalogEntry;
  submitting: boolean;
  onSubmit: (oauthClient: McpOAuthClientConfigRequest) => void;
  onClose: () => void;
}): ReactElement {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scope, setScope] = useState("");
  const [authEndpoint, setAuthEndpoint] = useState("");
  const [tokenEndpoint, setTokenEndpoint] = useState("");

  function handle(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!clientId.trim()) {
      return;
    }
    const payload: McpOAuthClientConfigRequest = {
      client_id: clientId.trim(),
    };
    if (clientSecret.trim()) {
      payload.client_secret = clientSecret.trim();
      payload.token_endpoint_auth_method = "client_secret_post";
    } else {
      payload.token_endpoint_auth_method = "none";
    }
    if (scope.trim()) payload.scope = scope.trim();
    if (authEndpoint.trim())
      payload.authorization_endpoint = authEndpoint.trim();
    if (tokenEndpoint.trim()) payload.token_endpoint = tokenEndpoint.trim();
    onSubmit(payload);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Connect ${entry.display_name}`}
      description={`${entry.display_name} doesn't expose OAuth metadata. Paste a pre-registered client from your ${entry.display_name} developer console.`}
    >
      <form
        className="mcp-setup"
        onSubmit={handle}
        aria-label={`OAuth credentials for ${entry.display_name}`}
      >
        <Field label="Client ID">
          <TextInput
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            autoComplete="off"
            required
          />
        </Field>
        <Field label="Client secret">
          <TextInput
            type="password"
            autoComplete="new-password"
            value={clientSecret}
            onChange={(event) => setClientSecret(event.target.value)}
          />
        </Field>
        <Field label="Scope">
          <TextInput
            value={scope}
            onChange={(event) => setScope(event.target.value)}
            autoComplete="off"
            placeholder="e.g. read:issues"
          />
        </Field>
        <Field label="Authorization endpoint" hint="Optional override.">
          <TextInput
            type="url"
            autoComplete="off"
            value={authEndpoint}
            onChange={(event) => setAuthEndpoint(event.target.value)}
          />
        </Field>
        <Field label="Token endpoint" hint="Optional override.">
          <TextInput
            type="url"
            autoComplete="off"
            value={tokenEndpoint}
            onChange={(event) => setTokenEndpoint(event.target.value)}
          />
        </Field>
        <div className="mcp-setup__actions">
          <Button
            type="button"
            variant="secondary"
            disabled={submitting}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={submitting}>
            {submitting ? "Installing…" : "Install with credentials"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// --- Custom URL card -------------------------------------------------------

function CustomUrlCard({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!url.trim() || pending) {
      return;
    }
    try {
      setPending(true);
      setError(null);
      const server = await connectors.addServer(url.trim());
      setUrl("");
      setOpen(false);
      // Mirror the catalog Install path: a new server lands in
      // ``auth_pending`` and would otherwise be invisible (Catalog only
      // cross-references seeds; Connected used to filter on
      // ``isAuthenticated``). Kick off OAuth immediately so the user
      // ends the flow connected, not stranded.
      if (
        server.auth_mode !== "none" &&
        server.auth_state !== "auth_unsupported" &&
        server.auth_state !== "authenticated"
      ) {
        await connectors.authenticate(server.server_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add server.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <article className="mcp-card mcp-card--custom">
        <AppIcon name="custom" size="lg" className="mcp-card__icon" />
        <div className="mcp-card__main">
          <div className="mcp-card__title-row">
            <h4 className="mcp-card__title">Add custom URL</h4>
          </div>
          <p className="mcp-card__desc">Self-hosted or unlisted MCP server.</p>
        </div>
        <button
          type="button"
          className="mcp-card__cta"
          onClick={() => setOpen(true)}
        >
          Add
        </button>
      </article>

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setError(null);
          setUrl("");
        }}
        title="Add custom MCP server"
        description="Paste the URL of a self-hosted or unlisted MCP server."
      >
        <form className="mcp-setup" onSubmit={(e) => void handleSubmit(e)}>
          <Field label="Server URL">
            <TextInput
              type="url"
              autoComplete="off"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://mcp.example.com/mcp"
              required
            />
          </Field>
          {error ? <p className="app-error">{error}</p> : null}
          <div className="mcp-setup__actions">
            <Button
              type="button"
              variant="secondary"
              disabled={pending}
              onClick={() => {
                setOpen(false);
                setError(null);
                setUrl("");
              }}
            >
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {pending ? "Adding…" : "Add"}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

// --- Connected tab ---------------------------------------------------------

function ConnectedTab({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  // Show every server the user has added, including ones that haven't
  // completed OAuth yet (``auth_pending`` / ``auth_failed`` /
  // ``unauthenticated``). Filtering on ``isAuthenticated`` here used to
  // hide manually-added URLs entirely — the Catalog tab also doesn't
  // show non-seed installs, so a custom server in ``auth_pending``
  // could become invisible. ``ConnectorRow`` renders the right status
  // and exposes Re-auth / Remove for non-authed rows.
  const installed = connectors.servers;

  if (installed.length === 0) {
    return (
      <Card className="mcp-empty">
        <p>
          No connectors added yet. Switch to the <strong>Catalog</strong> tab to
          install one, or use <strong>Add custom URL</strong>.
        </p>
      </Card>
    );
  }

  return (
    <div className="mcp-connected-list">
      {installed.map((server) => (
        <ConnectorRow
          key={server.server_id}
          server={server}
          connectors={connectors}
        />
      ))}
    </div>
  );
}
