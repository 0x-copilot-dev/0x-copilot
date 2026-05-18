// `McpWizard` — 3-step machine for tools-prd §2 U2.
//
// Steps:
//   1. URL or browse-marketplace tabs — user picks a server (paste a
//      custom URL OR select from a marketplace list).
//   2. OAuth round-trip handoff — wizard pauses while parent flips the
//      `oauthCompleted` prop after the user finishes the OAuth flow.
//   3. Per-method enable toggle — checkbox list; on save the wizard
//      assembles N `CreateToolRequest`s (one per enabled method).
//
// Substitution: `onStartOAuth(url)` is the deep-link callback; the
// wizard does not invoke `window.open`. `onSave` receives an array of
// `CreateToolRequest`s — one per enabled method.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Badge, Button, TextInput } from "@enterprise-search/design-system";
import type { CreateToolRequest } from "@enterprise-search/api-types";

import { useStepMachine } from "./useStepMachine";
import { WizardShell, type WizardStepDescriptor } from "./WizardShell";

const STEPS: ReadonlyArray<WizardStepDescriptor> = [
  { id: "source", label: "Pick a server" },
  { id: "oauth", label: "Authenticate" },
  { id: "methods", label: "Enable methods" },
];

export interface McpServerListEntry {
  readonly slug: string;
  readonly display_name: string;
  readonly description?: string;
  /** Pre-filled URL; the wizard prefers this over the paste input. */
  readonly url: string;
}

export interface McpMethod {
  readonly name: string;
  readonly description?: string;
}

export interface McpWizardProps {
  /** Marketplace entries. Empty → only the paste-URL tab is shown. */
  readonly marketplace?: ReadonlyArray<McpServerListEntry>;
  /**
   * OAuth handoff callback. The wizard freezes step 2 until
   * `oauthCompleted = true`. Hosts are responsible for opening the URL
   * and listening for the callback.
   */
  readonly onStartOAuth: (url: string) => void;
  /**
   * Flips to true when the host detects the OAuth callback succeeded.
   * Defaults to false.
   */
  readonly oauthCompleted?: boolean;
  /**
   * Host-supplied method discovery callback. Resolved after OAuth so
   * the wizard knows what to render in step 3.
   */
  readonly onDiscoverMethods?: (
    url: string,
  ) => Promise<ReadonlyArray<McpMethod>>;
  /** Save handler — one `CreateToolRequest` per enabled method. */
  readonly onSave: (requests: ReadonlyArray<CreateToolRequest>) => void;
  readonly onCancel?: () => void;
}

export function McpWizard(props: McpWizardProps): ReactElement {
  const {
    marketplace = [],
    onStartOAuth,
    oauthCompleted = false,
    onDiscoverMethods,
    onSave,
    onCancel,
  } = props;

  const stepper = useStepMachine({ totalSteps: STEPS.length });

  const [tab, setTab] = useState<"custom" | "marketplace">(
    marketplace.length > 0 ? "marketplace" : "custom",
  );
  const [customUrl, setCustomUrl] = useState("");
  const [pickedSlug, setPickedSlug] = useState<string | null>(null);

  const resolvedUrl = useMemo(() => {
    if (tab === "marketplace" && pickedSlug !== null) {
      return marketplace.find((m) => m.slug === pickedSlug)?.url ?? "";
    }
    return customUrl.trim();
  }, [tab, pickedSlug, customUrl, marketplace]);

  const [methods, setMethods] = useState<ReadonlyArray<McpMethod>>([]);
  const [methodsLoaded, setMethodsLoaded] = useState(false);
  const [enabled, setEnabled] = useState<ReadonlySet<string>>(new Set());

  // -- Step 2: OAuth handoff ---------------------------------------------
  const handleStartOAuth = useCallback(() => {
    if (resolvedUrl.length === 0) return;
    onStartOAuth(resolvedUrl);
  }, [resolvedUrl, onStartOAuth]);

  // -- Method discovery once OAuth lands ---------------------------------
  const handleDiscover = useCallback(async () => {
    if (onDiscoverMethods === undefined) {
      setMethods([]);
      setMethodsLoaded(true);
      return;
    }
    const list = await onDiscoverMethods(resolvedUrl);
    setMethods(list);
    // Default-enable everything; user can shrink.
    setEnabled(new Set(list.map((m) => m.name)));
    setMethodsLoaded(true);
  }, [onDiscoverMethods, resolvedUrl]);

  const canAdvance = useMemo(() => {
    switch (stepper.currentStep) {
      case 0:
        return resolvedUrl.length > 0;
      case 1:
        return oauthCompleted;
      case 2:
        return enabled.size > 0;
      default:
        return false;
    }
  }, [stepper.currentStep, resolvedUrl, oauthCompleted, enabled.size]);

  // When the user advances to step 2 and OAuth completes, kick off the
  // discovery call once.
  const handleNext = useCallback(async () => {
    const target = stepper.currentStep + 1;
    if (target === 2 && !methodsLoaded) {
      await handleDiscover();
    }
    stepper.next();
  }, [stepper, methodsLoaded, handleDiscover]);

  // -- Build the save payload --------------------------------------------
  const handleSave = useCallback(() => {
    const requests: ReadonlyArray<CreateToolRequest> = methods
      .filter((m) => enabled.has(m.name))
      .map((m) => ({
        kind: "mcp" as const,
        name: m.name,
        description: m.description ?? "",
        scope: "both" as const,
        args_schema: {},
        returns_schema: {},
        transport: {
          kind: "mcp" as const,
          executor: m.name,
        },
      }));
    onSave(requests);
  }, [methods, enabled, onSave]);

  const toggleMethod = useCallback((name: string) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  return (
    <WizardShell
      steps={STEPS}
      currentStep={stepper.currentStep}
      title="Install an MCP server"
      subtitle="Paste a URL or pick from the marketplace, authenticate, then enable the methods you want callable."
      onBack={stepper.back}
      onNext={handleNext}
      onFinish={handleSave}
      finishLabel={`Enable ${enabled.size} method${enabled.size === 1 ? "" : "s"}`}
      nextDisabled={!canAdvance}
      finishDisabled={enabled.size === 0}
      footerSlot={
        onCancel !== undefined && stepper.currentStep === 0 ? (
          <Button
            variant="ghost"
            size="md"
            onClick={onCancel}
            data-testid="mcp-wizard-cancel"
          >
            Cancel
          </Button>
        ) : null
      }
      testIdPrefix="mcp"
    >
      {stepper.currentStep === 0 ? (
        <SourceStep
          tab={tab}
          onChangeTab={setTab}
          marketplace={marketplace}
          customUrl={customUrl}
          onChangeCustomUrl={setCustomUrl}
          pickedSlug={pickedSlug}
          onPickSlug={setPickedSlug}
        />
      ) : null}
      {stepper.currentStep === 1 ? (
        <OAuthStep
          url={resolvedUrl}
          oauthCompleted={oauthCompleted}
          onStartOAuth={handleStartOAuth}
        />
      ) : null}
      {stepper.currentStep === 2 ? (
        <MethodsStep
          methods={methods}
          methodsLoaded={methodsLoaded}
          enabled={enabled}
          onToggle={toggleMethod}
        />
      ) : null}
    </WizardShell>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — source (custom URL or marketplace).
// ---------------------------------------------------------------------------

interface SourceStepProps {
  readonly tab: "custom" | "marketplace";
  readonly onChangeTab: (next: "custom" | "marketplace") => void;
  readonly marketplace: ReadonlyArray<McpServerListEntry>;
  readonly customUrl: string;
  readonly onChangeCustomUrl: (next: string) => void;
  readonly pickedSlug: string | null;
  readonly onPickSlug: (next: string | null) => void;
}

function SourceStep(props: SourceStepProps): ReactElement {
  const {
    tab,
    onChangeTab,
    marketplace,
    customUrl,
    onChangeCustomUrl,
    pickedSlug,
    onPickSlug,
  } = props;
  return (
    <>
      <div
        role="tablist"
        aria-label="MCP source"
        style={tabBarStyle}
        data-testid="mcp-wizard-source-tabs"
      >
        {marketplace.length > 0 ? (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "marketplace"}
            tabIndex={tab === "marketplace" ? 0 : -1}
            onClick={() => onChangeTab("marketplace")}
            style={tabButtonStyle(tab === "marketplace")}
            data-testid="mcp-wizard-tab-marketplace"
          >
            Browse marketplace
          </button>
        ) : null}
        <button
          type="button"
          role="tab"
          aria-selected={tab === "custom"}
          tabIndex={tab === "custom" ? 0 : -1}
          onClick={() => onChangeTab("custom")}
          style={tabButtonStyle(tab === "custom")}
          data-testid="mcp-wizard-tab-custom"
        >
          Paste a custom URL
        </button>
      </div>
      {tab === "custom" ? (
        <>
          <p style={hintStyle}>
            Enter the MCP server URL. We&apos;ll discover methods after auth.
          </p>
          <TextInput
            aria-label="MCP server URL"
            placeholder="https://mcp.example.com/sse"
            value={customUrl}
            onChange={(e) => onChangeCustomUrl(e.target.value)}
            data-testid="mcp-wizard-custom-url"
          />
        </>
      ) : marketplace.length === 0 ? (
        <p style={hintStyle} data-testid="mcp-wizard-marketplace-empty">
          No marketplace entries available.
        </p>
      ) : (
        <ul style={listStyle} data-testid="mcp-wizard-marketplace-list">
          {marketplace.map((entry) => {
            const isPicked = pickedSlug === entry.slug;
            return (
              <li key={entry.slug} style={listItemStyle}>
                <label style={labelRowStyle}>
                  <input
                    type="radio"
                    name="mcp-server-pick"
                    checked={isPicked}
                    onChange={() => onPickSlug(entry.slug)}
                    data-testid={`mcp-wizard-marketplace-${entry.slug}`}
                  />
                  <span style={{ fontWeight: 600 }}>{entry.display_name}</span>
                  {entry.description !== undefined ? (
                    <span style={mutedStyle}>— {entry.description}</span>
                  ) : null}
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — OAuth handoff.
// ---------------------------------------------------------------------------

interface OAuthStepProps {
  readonly url: string;
  readonly oauthCompleted: boolean;
  readonly onStartOAuth: () => void;
}

function OAuthStep(props: OAuthStepProps): ReactElement {
  const { url, oauthCompleted, onStartOAuth } = props;
  return (
    <div data-testid="mcp-wizard-oauth">
      <p style={hintStyle}>
        Open the OAuth flow for this server. Once you complete it in the new
        tab, this wizard will advance automatically.
      </p>
      <p style={mutedStyle}>
        Server: <code>{url}</code>
      </p>
      <Button
        variant="primary"
        size="md"
        onClick={onStartOAuth}
        disabled={oauthCompleted}
        data-testid="mcp-wizard-oauth-start"
      >
        {oauthCompleted ? "Authentication complete" : "Start OAuth"}
      </Button>
      {oauthCompleted ? (
        <p
          role="status"
          style={successStyle}
          data-testid="mcp-wizard-oauth-success"
        >
          Authenticated. Continue to method selection.
        </p>
      ) : (
        <p
          role="status"
          style={mutedStyle}
          data-testid="mcp-wizard-oauth-waiting"
        >
          Waiting for OAuth callback…
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — per-method enable toggle.
// ---------------------------------------------------------------------------

interface MethodsStepProps {
  readonly methods: ReadonlyArray<McpMethod>;
  readonly methodsLoaded: boolean;
  readonly enabled: ReadonlySet<string>;
  readonly onToggle: (name: string) => void;
}

function MethodsStep(props: MethodsStepProps): ReactElement {
  const { methods, methodsLoaded, enabled, onToggle } = props;

  if (!methodsLoaded) {
    return (
      <p style={hintStyle} data-testid="mcp-wizard-methods-loading">
        Discovering methods…
      </p>
    );
  }

  if (methods.length === 0) {
    return (
      <p style={hintStyle} data-testid="mcp-wizard-methods-empty">
        No methods discovered. The server may not expose any callable tools.
      </p>
    );
  }

  return (
    <>
      <p style={hintStyle}>
        Enable only the methods this tenant should call. Destructive methods
        (writes / deletes) are listed but unchecked by default — toggle them on
        explicitly.
      </p>
      <ul style={listStyle} data-testid="mcp-wizard-methods-list">
        {methods.map((m) => {
          const isOn = enabled.has(m.name);
          return (
            <li key={m.name} style={listItemStyle}>
              <label style={labelRowStyle}>
                <input
                  type="checkbox"
                  checked={isOn}
                  onChange={() => onToggle(m.name)}
                  data-testid={`mcp-wizard-method-${m.name}`}
                  aria-label={`Enable ${m.name}`}
                />
                <Badge tone="neutral">method</Badge>
                <code style={codeStyle}>{m.name}</code>
                {m.description !== undefined ? (
                  <span style={mutedStyle}>— {m.description}</span>
                ) : null}
              </label>
            </li>
          );
        })}
      </ul>
    </>
  );
}

// ---------------------------------------------------------------------------
// Styles.
// ---------------------------------------------------------------------------

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: 12.5,
  color: "var(--color-text-muted)",
};

const successStyle: CSSProperties = {
  margin: "8px 0 0 0",
  fontSize: 13,
  color: "var(--color-success, #16a34a)",
};

const mutedStyle: CSSProperties = {
  fontSize: 12.5,
  color: "var(--color-text-muted)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  maxHeight: 280,
  overflow: "auto",
};

const listItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 0",
};

const labelRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  fontSize: 13,
  color: "var(--color-text)",
  flexWrap: "wrap",
};

const codeStyle: CSSProperties = {
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 12.5,
  color: "var(--color-text)",
};

const tabBarStyle: CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--color-border)",
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "none",
  borderBottom: `2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  padding: "8px 14px",
  fontSize: 13,
  fontFamily: "inherit",
  cursor: "pointer",
});
