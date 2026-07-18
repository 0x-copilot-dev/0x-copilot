// `OpenApiWizard` — 5-step machine for tools-prd §2 U1.
//
// Steps:
//   1. URL — paste the OpenAPI document URL; parent fetches.
//   2. Operations — pick which paths × methods to expose. Defaults to
//      GETs only (sub-PRD §2 U1.2).
//   3. Auth — pick how the tool authenticates (connector / api_key /
//      none).
//   4. Scope review — read-only summary of what each picked op requires.
//   5. Test — fire a single test call and show the result. Includes
//      "Save anyway with status=error" per sub-PRD §7.4.
//
// Substitution: the wizard never fetches or calls the network. Hosts
// supply `onFetchOpenApi`, `onTestCall`, `onSave` callbacks.
//
// SP-1: types map onto `CreateToolRequest` and `TestToolCallRequest` from
// `@0x-copilot/api-types/tools`. We don't redeclare them.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Badge, Button, TextInput } from "@0x-copilot/design-system";
import type {
  ConnectorId,
  CreateToolRequest,
  TestToolCallRequest,
  TestToolCallResponse,
  ToolKind,
  ToolScope,
} from "@0x-copilot/api-types";

import { useStepMachine } from "./useStepMachine";
import { WizardShell, type WizardStepDescriptor } from "./WizardShell";

// ---------------------------------------------------------------------------
// Local types — OpenAPI document shape is intentionally minimal here. The
// real validation happens server-side per sub-PRD §2 U1.1.
// ---------------------------------------------------------------------------

export type OpenApiAuthKind = "connector" | "api_key" | "none";

export interface OpenApiOperation {
  /** "GET", "POST", "PATCH", … */
  readonly method: string;
  /** "/v1/accounts/{id}" */
  readonly path: string;
  readonly summary?: string;
  /** OpenAPI security scopes requested for the op (for §4 scope review). */
  readonly required_scopes?: ReadonlyArray<string>;
}

export interface OpenApiDoc {
  readonly title: string;
  readonly version?: string;
  readonly operations: ReadonlyArray<OpenApiOperation>;
}

export interface OpenApiAuthChoice {
  readonly kind: OpenApiAuthKind;
  /** Set when `kind = "connector"`. */
  readonly connector_id?: string;
  /** Set when `kind = "api_key"`. Vault is server-side; this is the raw
   *  paste the host will forward. */
  readonly api_key?: string;
}

export interface OpenApiWizardConnector {
  readonly id: string;
  readonly slug: string;
  readonly display_name: string;
}

export interface OpenApiWizardProps {
  /** Existing connectors the user can authenticate against. */
  readonly connectors?: ReadonlyArray<OpenApiWizardConnector>;
  /**
   * Host-supplied fetcher. Resolves to the doc or an error message.
   * The wizard never reads URLs directly.
   */
  readonly onFetchOpenApi: (
    url: string,
  ) => Promise<{ doc: OpenApiDoc } | { error: string }>;
  /**
   * Host-supplied test caller. The wizard passes a `TestToolCallRequest`
   * (matching `@0x-copilot/api-types/tools`); host owns the POST.
   */
  readonly onTestCall: (
    req: TestToolCallRequest,
  ) => Promise<TestToolCallResponse>;
  /**
   * Save handler. Receives the `CreateToolRequest` payload assembled
   * from the wizard's state PLUS a `status_override` to support
   * "Save anyway with status=error" (sub-PRD §7.4).
   */
  readonly onSave: (
    req: CreateToolRequest,
    opts: { readonly status_override?: "error" },
  ) => void;
  /** Cancel handler — host owns the dirty-prompt confirm guard. */
  readonly onCancel?: () => void;
}

const STEPS: ReadonlyArray<WizardStepDescriptor> = [
  { id: "url", label: "OpenAPI URL" },
  { id: "operations", label: "Operations" },
  { id: "auth", label: "Authentication" },
  { id: "scope", label: "Scope review" },
  { id: "test", label: "Test call" },
];

/** "GET /v1/accounts" stable key used in checkbox state. */
function opKey(op: OpenApiOperation): string {
  return `${op.method.toUpperCase()} ${op.path}`;
}

/** GETs are read; POST/PUT/PATCH/DELETE → "both" (issues + reads back). */
function scopeFor(ops: ReadonlyArray<OpenApiOperation>): ToolScope {
  const hasWrite = ops.some((op) => op.method.toUpperCase() !== "GET");
  return hasWrite ? "both" : "read";
}

export function OpenApiWizard(props: OpenApiWizardProps): ReactElement {
  const {
    connectors = [],
    onFetchOpenApi,
    onTestCall,
    onSave,
    onCancel,
  } = props;

  const stepper = useStepMachine({ totalSteps: STEPS.length });

  // -- Step 1 state --------------------------------------------------------
  const [url, setUrl] = useState("");
  const [fetchState, setFetchState] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ready"; doc: OpenApiDoc }
    | { kind: "error"; message: string }
  >({ kind: "idle" });

  // -- Step 2 state --------------------------------------------------------
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  // -- Step 3 state --------------------------------------------------------
  const [auth, setAuth] = useState<OpenApiAuthChoice>({ kind: "none" });

  // -- Step 5 state --------------------------------------------------------
  const [testState, setTestState] = useState<
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "result"; response: TestToolCallResponse }
  >({ kind: "idle" });

  // -- Step 1: fetch ------------------------------------------------------
  const handleFetch = useCallback(async () => {
    if (url.trim().length === 0) return;
    setFetchState({ kind: "loading" });
    try {
      const out = await onFetchOpenApi(url.trim());
      if ("error" in out) {
        setFetchState({ kind: "error", message: out.error });
        return;
      }
      setFetchState({ kind: "ready", doc: out.doc });
      // Default-select GET operations per §2 U1.2.
      const defaults = new Set(
        out.doc.operations
          .filter((op) => op.method.toUpperCase() === "GET")
          .map(opKey),
      );
      setSelected(defaults);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Fetch failed.";
      setFetchState({ kind: "error", message });
    }
  }, [url, onFetchOpenApi]);

  // -- Resolved operations (only present once the doc is ready) ----------
  const operations = useMemo<ReadonlyArray<OpenApiOperation>>(
    () => (fetchState.kind === "ready" ? fetchState.doc.operations : []),
    [fetchState],
  );
  const selectedOps = useMemo(
    () => operations.filter((op) => selected.has(opKey(op))),
    [operations, selected],
  );

  // -- Step transitions ---------------------------------------------------
  const canAdvance = useMemo(() => {
    switch (stepper.currentStep) {
      case 0:
        return fetchState.kind === "ready";
      case 1:
        return selectedOps.length > 0;
      case 2:
        if (auth.kind === "connector") return auth.connector_id !== undefined;
        if (auth.kind === "api_key")
          return (auth.api_key ?? "").trim().length > 0;
        return true;
      case 3:
        return true;
      default:
        return false;
    }
  }, [stepper.currentStep, fetchState.kind, selectedOps.length, auth]);

  // -- Step 5: test -------------------------------------------------------
  const handleTest = useCallback(async () => {
    setTestState({ kind: "running" });
    try {
      const response = await onTestCall({ args: {} });
      setTestState({ kind: "result", response });
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Test call failed.";
      setTestState({
        kind: "result",
        response: {
          status: "error",
          latency_ms: 0,
          error: { kind: "unknown", message },
        },
      });
    }
  }, [onTestCall]);

  // -- Build a CreateToolRequest from the wizard's state ------------------
  const buildRequest = useCallback((): CreateToolRequest => {
    const kind: ToolKind = "openapi";
    const title =
      fetchState.kind === "ready" ? fetchState.doc.title : "OpenAPI tool";
    return {
      kind,
      name: title,
      description: `Imported from ${url}.`,
      scope: scopeFor(selectedOps),
      args_schema: {},
      returns_schema: {},
      transport: {
        kind: "http",
        url_template: url,
        ...(auth.kind === "connector" && auth.connector_id !== undefined
          ? {
              connector_ref: {
                kind: "connector" as const,
                // Cast: hosts hand us connector ids as raw strings here;
                // the runtime resolves the brand once it lands in the
                // facade. The wizard is pure presentation — see SP-1 in
                // the file header.
                id: auth.connector_id as unknown as ConnectorId,
              },
            }
          : {}),
      },
    };
  }, [auth, fetchState, selectedOps, url]);

  // -- Save handlers -------------------------------------------------------
  const handleSave = useCallback(() => {
    onSave(buildRequest(), {});
  }, [buildRequest, onSave]);

  const handleSaveWithError = useCallback(() => {
    onSave(buildRequest(), { status_override: "error" });
  }, [buildRequest, onSave]);

  // -- Render --------------------------------------------------------------

  const testFailed =
    testState.kind === "result" && testState.response.status === "error";

  const footerSlot =
    stepper.isLast && testState.kind === "result" && testFailed ? (
      <Button
        variant="secondary"
        size="md"
        onClick={handleSaveWithError}
        data-testid="openapi-wizard-save-with-error"
      >
        Save anyway with status=error
      </Button>
    ) : null;

  return (
    <WizardShell
      steps={STEPS}
      currentStep={stepper.currentStep}
      title="Add an OpenAPI tool"
      subtitle="Paste a document URL, pick operations, review scopes, test the call."
      onBack={stepper.back}
      onNext={stepper.next}
      onFinish={handleSave}
      finishLabel="Save"
      nextDisabled={!canAdvance}
      finishDisabled={testState.kind !== "result"}
      footerSlot={
        onCancel !== undefined && stepper.currentStep === 0 ? (
          <Button
            variant="ghost"
            size="md"
            onClick={onCancel}
            data-testid="openapi-wizard-cancel"
          >
            Cancel
          </Button>
        ) : (
          footerSlot
        )
      }
      testIdPrefix="openapi"
    >
      {stepper.currentStep === 0 ? (
        <UrlStep
          url={url}
          onChangeUrl={setUrl}
          fetchState={fetchState}
          onFetch={handleFetch}
        />
      ) : null}
      {stepper.currentStep === 1 ? (
        <OperationsStep
          operations={operations}
          selected={selected}
          onChange={setSelected}
        />
      ) : null}
      {stepper.currentStep === 2 ? (
        <AuthStep auth={auth} connectors={connectors} onChange={setAuth} />
      ) : null}
      {stepper.currentStep === 3 ? (
        <ScopeReviewStep
          operations={selectedOps}
          auth={auth}
          connectors={connectors}
        />
      ) : null}
      {stepper.currentStep === 4 ? (
        <TestStep testState={testState} onTest={handleTest} />
      ) : null}
    </WizardShell>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — URL.
// ---------------------------------------------------------------------------

interface UrlStepProps {
  readonly url: string;
  readonly onChangeUrl: (next: string) => void;
  readonly fetchState:
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ready"; doc: OpenApiDoc }
    | { kind: "error"; message: string };
  readonly onFetch: () => void;
}

function UrlStep(props: UrlStepProps): ReactElement {
  const { url, onChangeUrl, fetchState, onFetch } = props;
  return (
    <>
      <p style={hintStyle}>
        We&apos;ll fetch the document and validate it server-side. URL scheme is
        allowlisted (https + tenant-internal hosts).
      </p>
      <div style={rowStyle}>
        <TextInput
          aria-label="OpenAPI document URL"
          placeholder="https://api.example.com/openapi.json"
          value={url}
          onChange={(e) => onChangeUrl(e.target.value)}
          data-testid="openapi-wizard-url-input"
        />
        <Button
          variant="primary"
          size="md"
          onClick={onFetch}
          disabled={url.trim().length === 0 || fetchState.kind === "loading"}
          data-testid="openapi-wizard-fetch"
        >
          {fetchState.kind === "loading" ? "Fetching…" : "Fetch"}
        </Button>
      </div>
      {fetchState.kind === "error" ? (
        <p
          role="alert"
          style={errorStyle}
          data-testid="openapi-wizard-fetch-error"
        >
          {fetchState.message}
        </p>
      ) : null}
      {fetchState.kind === "ready" ? (
        <p
          role="status"
          style={successStyle}
          data-testid="openapi-wizard-fetch-success"
        >
          Loaded {fetchState.doc.title}
          {fetchState.doc.version !== undefined
            ? ` (${fetchState.doc.version})`
            : ""}{" "}
          — {fetchState.doc.operations.length} operations.
        </p>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — operation picker.
// ---------------------------------------------------------------------------

interface OperationsStepProps {
  readonly operations: ReadonlyArray<OpenApiOperation>;
  readonly selected: ReadonlySet<string>;
  readonly onChange: (next: ReadonlySet<string>) => void;
}

function OperationsStep(props: OperationsStepProps): ReactElement {
  const { operations, selected, onChange } = props;

  const toggle = (op: OpenApiOperation): void => {
    const key = opKey(op);
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  };

  if (operations.length === 0) {
    return (
      <p style={hintStyle} data-testid="openapi-wizard-operations-empty">
        Fetch the document first to see operations.
      </p>
    );
  }

  return (
    <>
      <p style={hintStyle}>
        Selected operations become individual tool rows. Default: GETs only —
        toggle on write methods (POST / PATCH / DELETE) only when needed.
      </p>
      <ul style={listStyle} data-testid="openapi-wizard-operations-list">
        {operations.map((op) => {
          const key = opKey(op);
          const isOn = selected.has(key);
          return (
            <li key={key} style={listItemStyle}>
              <label style={labelRowStyle}>
                <input
                  type="checkbox"
                  checked={isOn}
                  onChange={() => toggle(op)}
                  data-testid={`openapi-wizard-op-${op.method}-${op.path}`}
                  aria-label={`${op.method} ${op.path}`}
                />
                <Badge
                  tone={
                    op.method.toUpperCase() === "GET" ? "neutral" : "warning"
                  }
                >
                  {op.method.toUpperCase()}
                </Badge>
                <code style={codeStyle}>{op.path}</code>
                {op.summary !== undefined ? (
                  <span style={mutedStyle}>— {op.summary}</span>
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
// Step 3 — auth picker.
// ---------------------------------------------------------------------------

interface AuthStepProps {
  readonly auth: OpenApiAuthChoice;
  readonly connectors: ReadonlyArray<OpenApiWizardConnector>;
  readonly onChange: (next: OpenApiAuthChoice) => void;
}

function AuthStep(props: AuthStepProps): ReactElement {
  const { auth, connectors, onChange } = props;

  return (
    <div
      role="radiogroup"
      aria-label="Authentication"
      style={radioGroupStyle}
      data-testid="openapi-wizard-auth"
    >
      <label style={labelRowStyle}>
        <input
          type="radio"
          name="openapi-auth-kind"
          checked={auth.kind === "connector"}
          onChange={() => onChange({ kind: "connector" })}
          data-testid="openapi-wizard-auth-connector"
        />
        <span>Use an existing connector</span>
      </label>
      {auth.kind === "connector" ? (
        connectors.length === 0 ? (
          <p
            role="status"
            style={hintStyle}
            data-testid="openapi-wizard-auth-no-connectors"
          >
            No connectors installed. Install one from the Connectors
            destination, or pick a different auth option.
          </p>
        ) : (
          <select
            aria-label="Connector"
            data-testid="openapi-wizard-auth-connector-select"
            style={selectStyle}
            value={auth.connector_id ?? ""}
            onChange={(e) =>
              onChange({ kind: "connector", connector_id: e.target.value })
            }
          >
            <option value="">— pick a connector —</option>
            {connectors.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name} ({c.slug})
              </option>
            ))}
          </select>
        )
      ) : null}

      <label style={labelRowStyle}>
        <input
          type="radio"
          name="openapi-auth-kind"
          checked={auth.kind === "api_key"}
          onChange={() => onChange({ kind: "api_key", api_key: "" })}
          data-testid="openapi-wizard-auth-apikey"
        />
        <span>Paste an API key (stored encrypted in the vault)</span>
      </label>
      {auth.kind === "api_key" ? (
        <TextInput
          aria-label="API key"
          type="password"
          placeholder="paste API key"
          value={auth.api_key ?? ""}
          onChange={(e) =>
            onChange({ kind: "api_key", api_key: e.target.value })
          }
          data-testid="openapi-wizard-auth-apikey-input"
        />
      ) : null}

      <label style={labelRowStyle}>
        <input
          type="radio"
          name="openapi-auth-kind"
          checked={auth.kind === "none"}
          onChange={() => onChange({ kind: "none" })}
          data-testid="openapi-wizard-auth-none"
        />
        <span>No authentication (public API)</span>
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — scope review.
// ---------------------------------------------------------------------------

interface ScopeReviewStepProps {
  readonly operations: ReadonlyArray<OpenApiOperation>;
  readonly auth: OpenApiAuthChoice;
  readonly connectors: ReadonlyArray<OpenApiWizardConnector>;
}

function ScopeReviewStep(props: ScopeReviewStepProps): ReactElement {
  const { operations, auth, connectors } = props;
  const connectorLabel = useMemo(() => {
    if (auth.kind !== "connector" || auth.connector_id === undefined) return "";
    const match = connectors.find((c) => c.id === auth.connector_id);
    return match?.display_name ?? auth.connector_id;
  }, [auth, connectors]);

  return (
    <div data-testid="openapi-wizard-scope-review">
      <p style={hintStyle}>
        Review the requested scopes. Each row below becomes a Tool. You can
        adjust auth or operations by going back.
      </p>
      <dl style={defListStyle}>
        <dt style={dtStyle}>Authentication</dt>
        <dd style={ddStyle}>
          {auth.kind === "connector"
            ? `Connector: ${connectorLabel}`
            : auth.kind === "api_key"
              ? "API key (vault)"
              : "None (public)"}
        </dd>
        <dt style={dtStyle}>Operations</dt>
        <dd style={ddStyle}>
          {operations.length === 0 ? (
            <span>None selected.</span>
          ) : (
            <ul style={listStyle}>
              {operations.map((op) => (
                <li key={opKey(op)} style={listItemStyle}>
                  <Badge
                    tone={
                      op.method.toUpperCase() === "GET" ? "neutral" : "warning"
                    }
                  >
                    {op.method.toUpperCase()}
                  </Badge>
                  <code style={codeStyle}>{op.path}</code>
                  {op.required_scopes !== undefined &&
                  op.required_scopes.length > 0 ? (
                    <span style={mutedStyle}>
                      requires {op.required_scopes.join(", ")}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 5 — test call.
// ---------------------------------------------------------------------------

interface TestStepProps {
  readonly testState:
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "result"; response: TestToolCallResponse };
  readonly onTest: () => void;
}

function TestStep(props: TestStepProps): ReactElement {
  const { testState, onTest } = props;
  return (
    <div data-testid="openapi-wizard-test">
      <p style={hintStyle}>
        Sends a single test call using default args. The result is audit-
        logged. You can save with status=error if the call fails — useful when
        the API is temporarily down but you still want the catalog row.
      </p>
      <Button
        variant="primary"
        size="md"
        onClick={onTest}
        disabled={testState.kind === "running"}
        data-testid="openapi-wizard-test-run"
      >
        {testState.kind === "running" ? "Calling…" : "Run test call"}
      </Button>
      {testState.kind === "result" ? (
        <div
          role="status"
          style={testResultStyle(testState.response.status)}
          data-testid="openapi-wizard-test-result"
          data-status={testState.response.status}
        >
          <strong>{testState.response.status === "ok" ? "OK" : "Error"}</strong>{" "}
          ({testState.response.latency_ms} ms)
          {testState.response.error !== undefined ? (
            <p style={mutedStyle}>
              {testState.response.error.kind}:{" "}
              {testState.response.error.message}
            </p>
          ) : null}
          {testState.response.result !== undefined ? (
            <pre style={preStyle}>
              {JSON.stringify(testState.response.result, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles — all token-only, no bespoke hex / px outside the small spacing
// numbers that the rest of chat-surface already uses.
// ---------------------------------------------------------------------------

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const errorStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-danger, #dc2626)",
};

const successStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-success, #16a34a)",
};

const rowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
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
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
};

const codeStyle: CSSProperties = {
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text)",
};

const mutedStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const radioGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const selectStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
};

const defListStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(120px, max-content) 1fr",
  gap: "8px 16px",
  margin: 0,
};

const dtStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const ddStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
};

const testResultStyle = (status: "ok" | "error"): CSSProperties => ({
  marginTop: 12,
  padding: 10,
  borderRadius: 6,
  border: `1px solid ${
    status === "ok"
      ? "var(--color-success, #16a34a)"
      : "var(--color-danger, #dc2626)"
  }`,
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
});

const preStyle: CSSProperties = {
  margin: "8px 0 0 0",
  padding: 8,
  borderRadius: 4,
  background: "var(--color-bg)",
  fontSize: "var(--font-size-xs)",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  maxHeight: 200,
  overflow: "auto",
  whiteSpace: "pre-wrap",
};
