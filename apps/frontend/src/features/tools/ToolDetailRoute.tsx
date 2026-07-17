// ToolDetailRoute — detail pane for the Phase 10 Tools destination.
//
// Mounted inside `ToolsRoute` when a tool is selected. Owns:
//   1. Fetching the tool detail (`GET /v1/tools/{id}`) including the
//      "Used by" rollup (tools-prd §4.2).
//   2. Fetching paginated invocations (`GET /v1/tools/{id}/invocations`)
//      for the audit-lens block (tools-prd §4.6).
//   3. Fetching the windowed usage projection (`GET /v1/tools/{id}/usage`)
//      for the usage block (tools-prd §4.7).
//   4. Editor — minimal name/description/tags edit surface that PATCHes
//      via `toolsApi.patchTool`. The full editor (§7 ToolEditor) lands
//      from the chat-surface side in P10-B3.
//   5. Test-call seam — wires the test button to `testToolCall`; the
//      sandbox executor (P10-A3) returns the result.
//
// Same shape as `AgentsRoute`'s `AgentDetailPanel`, but lifted into its
// own file so the orchestrator's "3 route files" guidance maps cleanly
// to disk and so the detail pane can be unit-tested in isolation.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import type {
  TestToolCallResponse,
  Tool,
  ToolDetailResponse,
  ToolId,
  ToolInvocationListResponse,
  ToolUsageResponse,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  fetchInvocations,
  fetchTool,
  fetchUsage,
  patchTool,
  testToolCall,
} from "../../api/toolsApi";
import { errorMessage } from "../../utils/errors";
import {
  detailToHeaderProps,
  invocationToRow,
  type InvocationRowProps,
  type ToolDetailHeaderProps,
} from "./adapters";

interface ToolDetailRouteProps {
  readonly identity: RequestIdentity;
  readonly toolId: ToolId;
  readonly onClose: () => void;
  /** Merged-back into the parent list when a PATCH lands. */
  readonly onUpdated: (tool: Tool) => void;
  readonly onError: (message: string) => void;
}

type DetailState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly response: ToolDetailResponse;
      readonly header: ToolDetailHeaderProps;
    };

export function ToolDetailRoute({
  identity,
  toolId,
  onClose,
  onUpdated,
  onError,
}: ToolDetailRouteProps): ReactElement {
  const [state, setState] = useState<DetailState>({ kind: "loading" });

  // Detail fetch — keyed by tool id so switching the selection resets.
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchTool(identity, toolId)
      .then((response) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          response,
          header: detailToHeaderProps(response),
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load tool."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, toolId]);

  if (state.kind === "loading") {
    return (
      <div
        data-testid="tools-route-detail"
        data-tool-id={toolId}
        data-state="loading"
        style={{
          flex: "1 1 auto",
          padding: 24,
          fontSize: 13,
          boxSizing: "border-box",
        }}
      >
        Loading tool…
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div
        data-testid="tools-route-detail"
        data-tool-id={toolId}
        data-state="error"
        role="alert"
        style={{
          flex: "1 1 auto",
          padding: 24,
          fontSize: 13,
          boxSizing: "border-box",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div data-testid="tools-route-detail-error">{state.message}</div>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }

  return (
    <ReadyDetail
      identity={identity}
      response={state.response}
      header={state.header}
      onClose={onClose}
      onUpdated={(tool) => {
        onUpdated(tool);
        // Refresh local view so the header / rollups match the new row.
        setState({
          kind: "ready",
          response: { ...state.response, tool },
          header: detailToHeaderProps({ ...state.response, tool }),
        });
      }}
      onError={onError}
    />
  );
}

// ===========================================================================
// Ready state — split out so the loaded path is its own (testable) shell.
// ===========================================================================

interface ReadyDetailProps {
  readonly identity: RequestIdentity;
  readonly response: ToolDetailResponse;
  readonly header: ToolDetailHeaderProps;
  readonly onClose: () => void;
  readonly onUpdated: (tool: Tool) => void;
  readonly onError: (message: string) => void;
}

function ReadyDetail({
  identity,
  response,
  header,
  onClose,
  onUpdated,
  onError,
}: ReadyDetailProps): ReactElement {
  const tool = response.tool;
  // Editor draft state — keyed by tool id so switching resets the form.
  const [name, setName] = useState(tool.name);
  const [description, setDescription] = useState(tool.description);
  const [tagsText, setTagsText] = useState(tool.tags.join(", "));

  useEffect(() => {
    setName(tool.name);
    setDescription(tool.description);
    setTagsText(tool.tags.join(", "));
  }, [tool.id, tool.name, tool.description, tool.tags]);

  const handleSave = useCallback(async (): Promise<void> => {
    try {
      const updated = await patchTool(identity, tool.id, {
        name,
        description,
        tags: tagsText
          .split(",")
          .map((t) => t.trim())
          .filter((t) => t.length > 0),
      });
      onUpdated(updated);
    } catch (error: unknown) {
      onError(errorMessage(error, "Could not save tool."));
    }
  }, [identity, tool.id, name, description, tagsText, onUpdated, onError]);

  return (
    <div
      data-testid="tools-route-detail"
      data-tool-id={tool.id}
      data-state="ready"
      style={{
        flex: "1 1 auto",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <ToolDetailHeader header={header} onClose={onClose} />

      {/* Editor */}
      <div>
        <label
          htmlFor={`tools-name-${tool.id}`}
          style={{ fontSize: 12, fontWeight: 600 }}
        >
          Name
        </label>
        <input
          id={`tools-name-${tool.id}`}
          data-testid="tools-route-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: "100%", marginTop: 4, boxSizing: "border-box" }}
        />
        <label
          htmlFor={`tools-desc-${tool.id}`}
          style={{
            fontSize: 12,
            fontWeight: 600,
            marginTop: 8,
            display: "block",
          }}
        >
          Description
        </label>
        <textarea
          id={`tools-desc-${tool.id}`}
          data-testid="tools-route-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          style={{
            width: "100%",
            marginTop: 4,
            boxSizing: "border-box",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
          }}
        />
        <label
          htmlFor={`tools-tags-${tool.id}`}
          style={{
            fontSize: 12,
            fontWeight: 600,
            marginTop: 8,
            display: "block",
          }}
        >
          Tags (comma-separated)
        </label>
        <input
          id={`tools-tags-${tool.id}`}
          data-testid="tools-route-tags"
          value={tagsText}
          onChange={(e) => setTagsText(e.target.value)}
          style={{ width: "100%", marginTop: 4, boxSizing: "border-box" }}
        />
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            data-testid="tools-route-save"
            onClick={() => {
              void handleSave();
            }}
          >
            Save
          </button>
        </div>
      </div>

      {/* Test call seam */}
      <TestCallBlock identity={identity} toolId={tool.id} onError={onError} />

      {/* Invocation audit */}
      <InvocationsBlock identity={identity} toolId={tool.id} />

      {/* Usage block */}
      <UsageBlock identity={identity} toolId={tool.id} />
    </div>
  );
}

// ===========================================================================
// Header — pure presentation from `ToolDetailHeaderProps`
// ===========================================================================

function ToolDetailHeader({
  header,
  onClose,
}: {
  readonly header: ToolDetailHeaderProps;
  readonly onClose: () => void;
}): ReactElement {
  return (
    <header
      style={{
        display: "flex",
        gap: 12,
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <div>
        <div style={{ fontSize: 16, fontWeight: 700 }}>{header.name}</div>
        <div
          data-testid="tools-route-detail-meta"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          {header.kind} · {header.scope} · {header.status} ·{" "}
          {header.transport_summary}
        </div>
        {header.status_reason !== null && (
          <div
            data-testid="tools-route-detail-status-reason"
            style={{ fontSize: 12, color: "var(--color-text-muted)" }}
          >
            {header.status_reason}
          </div>
        )}
      </div>
      <button
        type="button"
        data-testid="tools-route-detail-close"
        onClick={onClose}
      >
        Close
      </button>
    </header>
  );
}

// ===========================================================================
// Test call block — runs `POST /v1/tools/{id}/test` with raw-JSON args.
// ===========================================================================

function TestCallBlock({
  identity,
  toolId,
  onError,
}: {
  readonly identity: RequestIdentity;
  readonly toolId: ToolId;
  readonly onError: (message: string) => void;
}): ReactElement {
  const [argsText, setArgsText] = useState("{}");
  const [result, setResult] = useState<TestToolCallResponse | null>(null);
  const [running, setRunning] = useState(false);

  const handleRun = useCallback(async (): Promise<void> => {
    setRunning(true);
    setResult(null);
    try {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(argsText) as Record<string, unknown>;
      } catch {
        onError("Test args must be valid JSON.");
        setRunning(false);
        return;
      }
      const response = await testToolCall(identity, toolId, { args: parsed });
      setResult(response);
    } catch (error: unknown) {
      onError(errorMessage(error, "Could not run test call."));
    } finally {
      setRunning(false);
    }
  }, [identity, toolId, argsText, onError]);

  return (
    <section data-testid="tools-route-test-block">
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0" }}>
        Test call
      </h3>
      <textarea
        data-testid="tools-route-test-args"
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
        rows={3}
        style={{
          width: "100%",
          boxSizing: "border-box",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
        }}
      />
      <div style={{ marginTop: 4 }}>
        <button
          type="button"
          data-testid="tools-route-test-run"
          disabled={running}
          onClick={() => {
            void handleRun();
          }}
        >
          {running ? "Running…" : "Run test"}
        </button>
      </div>
      {result !== null && (
        <pre
          data-testid="tools-route-test-result"
          data-status={result.status}
          style={{
            marginTop: 8,
            padding: 8,
            backgroundColor: "var(--color-surface)",
            fontSize: 12,
            overflow: "auto",
          }}
        >
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </section>
  );
}

// ===========================================================================
// Invocations block
// ===========================================================================

function InvocationsBlock({
  identity,
  toolId,
}: {
  readonly identity: RequestIdentity;
  readonly toolId: ToolId;
}): ReactElement {
  const [state, setState] = useState<
    | { readonly kind: "loading" }
    | { readonly kind: "error"; readonly message: string }
    | {
        readonly kind: "ready";
        readonly rows: ReadonlyArray<InvocationRowProps>;
      }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchInvocations(identity, toolId, { limit: 20 })
      .then((res: ToolInvocationListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          rows: res.invocations.map(invocationToRow),
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load invocations."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, toolId]);

  return (
    <section
      data-testid="tools-route-invocations"
      data-state={state.kind}
      data-item-count={state.kind === "ready" ? state.rows.length : 0}
    >
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0" }}>
        Recent invocations
      </h3>
      {state.kind === "loading" ? (
        <div style={{ fontSize: 12 }}>Loading invocations…</div>
      ) : state.kind === "error" ? (
        <div
          role="alert"
          data-testid="tools-route-invocations-error"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          {state.message}
        </div>
      ) : state.rows.length === 0 ? (
        <div
          data-testid="tools-route-invocations-empty"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          No invocations yet.
        </div>
      ) : (
        <ul
          data-testid="tools-route-invocations-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {state.rows.map((row) => (
            <li
              key={row.id}
              data-testid="tools-route-invocation-row"
              data-invocation-id={row.id}
              data-invocation-status={row.status}
              style={{
                padding: "6px 0",
                borderBottom: "1px solid var(--color-border)",
                fontSize: 12,
              }}
            >
              {row.caller_kind} · {row.status} · {row.latency_label}
              {row.error_kind !== null ? ` · ${row.error_kind}` : ""}
              <div style={{ color: "var(--color-text-muted)" }}>
                {row.args_preview}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// Usage block — fetches `GET /v1/tools/{id}/usage`
// ===========================================================================

function UsageBlock({
  identity,
  toolId,
}: {
  readonly identity: RequestIdentity;
  readonly toolId: ToolId;
}): ReactElement {
  const [state, setState] = useState<
    | { readonly kind: "loading" }
    | { readonly kind: "error"; readonly message: string }
    | { readonly kind: "ready"; readonly response: ToolUsageResponse }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchUsage(identity, toolId)
      .then((response) => {
        if (cancelled) return;
        setState({ kind: "ready", response });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load usage."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, toolId]);

  return (
    <section data-testid="tools-route-usage" data-state={state.kind}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0" }}>Usage</h3>
      {state.kind === "loading" ? (
        <div style={{ fontSize: 12 }}>Loading usage…</div>
      ) : state.kind === "error" ? (
        <div
          role="alert"
          data-testid="tools-route-usage-error"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          {state.message}
        </div>
      ) : (
        <div
          data-testid="tools-route-usage-totals"
          data-calls-30d={state.response.windows.window_30d.calls_30d}
          style={{
            fontSize: 12,
            color: "var(--color-text-muted)",
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          <span>
            24h:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.windows.window_24h.calls_24h}
            </strong>
          </span>
          <span>
            7d:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.windows.window_7d.calls_30d}
            </strong>
          </span>
          <span>
            30d:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.windows.window_30d.calls_30d}
            </strong>
          </span>
        </div>
      )}
    </section>
  );
}
