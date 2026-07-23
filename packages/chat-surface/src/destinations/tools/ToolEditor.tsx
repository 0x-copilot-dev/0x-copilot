// <ToolEditor /> — owner-only tabbed editor for a Tool.
//
// Source:
//   - docs/atlas-new-design/destinations/tools-prd.md §7.2 (editor: Basics
//     / Schema / Transport / Permissions).
//   - tools-prd.md §3.1 — wire shape (Tool / UpdateToolRequest).
//   - Phase 8 `AgentEditor.tsx` — the tabbed-editor pattern (ARIA tabs,
//     arrow / Home / End nav, header status pill, footer Save). Same
//     SP-1 discipline; identical tab keyboard model.
//
// Invariants:
//   - SUBSTITUTION: takes a `Tool` view-model + `onSave(patch)` callback.
//     The editor diffs the draft against the input and emits ONLY the
//     fields that changed (= a valid `UpdateToolRequest`). The host
//     owns the network call.
//   - SINGLE SOURCE OF TRUTH: imports `Tool` / `UpdateToolRequest` from
//     `@0x-copilot/api-types`. Zero brand redeclarations.
//   - JSON Schema editors are textareas with inline JSON.parse validation;
//     invalid input shows the parse-error message inline and the Save
//     button blocks the affected field's contribution to the patch.
//   - ARIA tabs (cross-audit §1.6, master §3.6): `role="tablist"` +
//     `role="tab"` + `role="tabpanel"` + arrow / Home / End nav.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  Tool,
  ToolScope,
  ToolStatus,
  ToolTransport,
  ToolTransportKind,
  UpdateToolRequest,
} from "@0x-copilot/api-types";
import { Badge } from "@0x-copilot/design-system";

// ===========================================================================
// Tab IDs + ARIA wiring.
// ===========================================================================

export type ToolEditorTabId = "basics" | "schema" | "transport" | "permissions";

const TAB_ORDER: ReadonlyArray<ToolEditorTabId> = [
  "basics",
  "schema",
  "transport",
  "permissions",
];

const TAB_LABEL: Readonly<Record<ToolEditorTabId, string>> = {
  basics: "Basics",
  schema: "Schema",
  transport: "Transport",
  permissions: "Permissions",
};

// ===========================================================================
// Public props.
// ===========================================================================

export type ToolEditorSaveState = "idle" | "saving" | "saved" | "error";

export interface ToolEditorProps {
  /** Current canonical row — the diff base for the patch emitted on save. */
  readonly tool: Tool;
  /**
   * Save handler. Receives ONLY the fields that changed (a valid
   * `UpdateToolRequest`). The host owns the
   * `PATCH /v1/tools/{id}` transport.
   */
  readonly onSave: (patch: UpdateToolRequest) => void;
  readonly onCancel?: () => void;
  readonly initialTab?: ToolEditorTabId;
  readonly saveState?: ToolEditorSaveState;
  readonly disabled?: boolean;
}

// ===========================================================================
// Local draft shape — string forms for the JSON-Schema fields so the user
// can type invalid JSON without the editor losing its place. We only
// promote them back to `Record<string, unknown>` at diff time.
// ===========================================================================

interface ToolEditorDraft {
  readonly name: string;
  readonly description: string;
  readonly scope: ToolScope;
  readonly status: ToolStatus;
  readonly status_reason: string;
  readonly tags: string; // comma-separated input
  readonly args_schema_text: string;
  readonly returns_schema_text: string;
  readonly transport: ToolTransport;
}

function draftFromTool(tool: Tool): ToolEditorDraft {
  return {
    name: tool.name,
    description: tool.description,
    scope: tool.scope,
    status: tool.status,
    status_reason: tool.status_reason ?? "",
    tags: tool.tags.join(", "),
    args_schema_text: JSON.stringify(tool.args_schema, null, 2),
    returns_schema_text: JSON.stringify(tool.returns_schema, null, 2),
    transport: tool.transport,
  };
}

interface JsonParseResult {
  readonly ok: boolean;
  readonly value?: Record<string, unknown>;
  readonly error?: string;
}

function parseJsonSchema(text: string): JsonParseResult {
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      return { ok: false, error: "JSON Schema must be a JSON object." };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON.";
    return { ok: false, error: message };
  }
}

/**
 * Compute the patch (diff) between the editor draft and the canonical
 * Tool. Returns ONLY the keys whose values changed. Invalid JSON Schema
 * fields are omitted from the patch (the inline error renders alongside;
 * the host shouldn't issue a request with invalid bodies anyway).
 */
function computePatch(tool: Tool, draft: ToolEditorDraft): UpdateToolRequest {
  const patch: Record<string, unknown> = {};

  if (draft.name !== tool.name) patch.name = draft.name;
  if (draft.description !== tool.description)
    patch.description = draft.description;
  if (draft.scope !== tool.scope) patch.scope = draft.scope;
  if (draft.status !== tool.status) patch.status = draft.status;
  const reasonOriginal = tool.status_reason ?? "";
  if (draft.status_reason !== reasonOriginal && draft.status_reason !== "")
    patch.status_reason = draft.status_reason;

  const draftTags = draft.tags
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  const tagsChanged =
    draftTags.length !== tool.tags.length ||
    draftTags.some((t, i) => t !== tool.tags[i]);
  if (tagsChanged) patch.tags = draftTags;

  const argsParse = parseJsonSchema(draft.args_schema_text);
  if (argsParse.ok && argsParse.value !== undefined) {
    if (JSON.stringify(argsParse.value) !== JSON.stringify(tool.args_schema))
      patch.args_schema = argsParse.value;
  }
  const returnsParse = parseJsonSchema(draft.returns_schema_text);
  if (returnsParse.ok && returnsParse.value !== undefined) {
    if (
      JSON.stringify(returnsParse.value) !== JSON.stringify(tool.returns_schema)
    )
      patch.returns_schema = returnsParse.value;
  }

  if (!transportEqual(draft.transport, tool.transport)) {
    patch.transport = draft.transport;
  }

  return patch as UpdateToolRequest;
}

function transportEqual(a: ToolTransport, b: ToolTransport): boolean {
  if (a.kind !== b.kind) return false;
  if ((a.url_template ?? "") !== (b.url_template ?? "")) return false;
  if ((a.executor ?? "") !== (b.executor ?? "")) return false;
  const aConn = a.connector_ref?.id ?? "";
  const bConn = b.connector_ref?.id ?? "";
  if (aConn !== bConn) return false;
  return true;
}

// ===========================================================================
// Component.
// ===========================================================================

export function ToolEditor(props: ToolEditorProps): ReactElement {
  const {
    tool,
    onSave,
    onCancel,
    initialTab = "basics",
    saveState = "idle",
    disabled = false,
  } = props;

  const [draft, setDraft] = useState<ToolEditorDraft>(() =>
    draftFromTool(tool),
  );
  const [activeTab, setActiveTab] = useState<ToolEditorTabId>(initialTab);
  const tabRefs = useRef<Record<ToolEditorTabId, HTMLButtonElement | null>>({
    basics: null,
    schema: null,
    transport: null,
    permissions: null,
  });

  const argsParse = useMemo(
    () => parseJsonSchema(draft.args_schema_text),
    [draft.args_schema_text],
  );
  const returnsParse = useMemo(
    () => parseJsonSchema(draft.returns_schema_text),
    [draft.returns_schema_text],
  );

  const update = useCallback(
    <K extends keyof ToolEditorDraft>(key: K, next: ToolEditorDraft[K]) => {
      setDraft((prev) => ({ ...prev, [key]: next }));
    },
    [],
  );

  const updateTransport = useCallback(
    <K extends keyof ToolTransport>(key: K, next: ToolTransport[K]) => {
      setDraft((prev) => ({
        ...prev,
        transport: { ...prev.transport, [key]: next },
      }));
    },
    [],
  );

  const focusTab = useCallback((tab: ToolEditorTabId) => {
    setActiveTab(tab);
    tabRefs.current[tab]?.focus();
  }, []);

  const onTabKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const idx = TAB_ORDER.indexOf(activeTab);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length]!);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]!);
      } else if (event.key === "Home") {
        event.preventDefault();
        focusTab(TAB_ORDER[0]!);
      } else if (event.key === "End") {
        event.preventDefault();
        focusTab(TAB_ORDER[TAB_ORDER.length - 1]!);
      }
    },
    [activeTab, focusTab],
  );

  const handleSave = useCallback(() => {
    const patch = computePatch(tool, draft);
    onSave(patch);
  }, [tool, draft, onSave]);

  const saveLabel = useMemo(() => {
    if (saveState === "saving") return "Saving…";
    if (saveState === "saved") return "Saved";
    if (saveState === "error") return "Retry save";
    return "Save";
  }, [saveState]);

  const saveDisabled =
    disabled || saveState === "saving" || !argsParse.ok || !returnsParse.ok;

  return (
    <div
      style={containerStyle}
      data-testid="tool-editor"
      data-active-tab={activeTab}
      data-save-state={saveState}
    >
      <div style={headerStyle}>
        <Badge
          tone={tool.status === "enabled" ? "success" : "neutral"}
          data-testid="tool-editor-status-pill"
        >
          {tool.status}
        </Badge>
        <input
          type="text"
          value={draft.name}
          maxLength={120}
          onChange={(e) => update("name", e.target.value)}
          placeholder="Tool name"
          aria-label="Tool name (header)"
          data-testid="tool-editor-name-header"
          style={nameHeaderStyle}
          disabled={disabled}
        />
        {onCancel !== undefined ? (
          <button
            type="button"
            onClick={onCancel}
            data-testid="tool-editor-cancel"
            style={secondaryButtonStyle}
            disabled={disabled}
          >
            Cancel
          </button>
        ) : null}
        <button
          type="button"
          onClick={handleSave}
          data-testid="tool-editor-save"
          data-save-state={saveState}
          style={primaryButtonStyle(saveState)}
          disabled={saveDisabled}
          aria-live="polite"
        >
          {saveLabel}
        </button>
      </div>

      <div
        role="tablist"
        aria-label="Tool editor"
        style={tabStripStyle}
        onKeyDown={onTabKeyDown}
      >
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            type="button"
            role="tab"
            id={`tool-editor-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`tool-editor-tabpanel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => setActiveTab(tab)}
            data-testid={`tool-editor-tab-${tab}`}
            style={tabButtonStyle(activeTab === tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`tool-editor-tabpanel-${activeTab}`}
        aria-labelledby={`tool-editor-tab-${activeTab}`}
        data-testid={`tool-editor-tabpanel-${activeTab}`}
        style={panelStyle}
      >
        {activeTab === "basics" ? (
          <BasicsTab draft={draft} disabled={disabled} onChange={update} />
        ) : null}
        {activeTab === "schema" ? (
          <SchemaTab
            draft={draft}
            disabled={disabled}
            onChange={update}
            argsError={argsParse.ok ? null : (argsParse.error ?? null)}
            returnsError={returnsParse.ok ? null : (returnsParse.error ?? null)}
          />
        ) : null}
        {activeTab === "transport" ? (
          <TransportTab
            transport={draft.transport}
            disabled={disabled}
            onChange={updateTransport}
          />
        ) : null}
        {activeTab === "permissions" ? (
          <PermissionsTab draft={draft} disabled={disabled} onChange={update} />
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// Basics tab.
// ===========================================================================

interface TabProps {
  readonly draft: ToolEditorDraft;
  readonly disabled: boolean;
  readonly onChange: <K extends keyof ToolEditorDraft>(
    key: K,
    next: ToolEditorDraft[K],
  ) => void;
}

function BasicsTab({ draft, disabled, onChange }: TabProps): ReactElement {
  return (
    <FieldGroup>
      <Field label="Name" hint="Required, up to 120 characters.">
        <input
          type="text"
          value={draft.name}
          maxLength={120}
          onChange={(e) => onChange("name", e.target.value)}
          aria-label="Name"
          data-testid="tool-editor-name-input"
          style={textInputStyle}
          disabled={disabled}
        />
      </Field>
      <Field label="Description" hint="One sentence. Shown on the card.">
        <textarea
          value={draft.description}
          onChange={(e) => onChange("description", e.target.value)}
          maxLength={400}
          rows={3}
          aria-label="Description"
          data-testid="tool-editor-description-input"
          style={textAreaStyle}
          disabled={disabled}
        />
      </Field>
      <Field
        label="Tags"
        hint="Comma-separated. Used by the catalog filter chips."
      >
        <input
          type="text"
          value={draft.tags}
          onChange={(e) => onChange("tags", e.target.value)}
          aria-label="Tags"
          data-testid="tool-editor-tags-input"
          placeholder="reporting, internal, slack"
          style={textInputStyle}
          disabled={disabled}
        />
      </Field>
    </FieldGroup>
  );
}

// ===========================================================================
// Schema tab.
// ===========================================================================

interface SchemaTabProps extends TabProps {
  readonly argsError: string | null;
  readonly returnsError: string | null;
}

function SchemaTab(props: SchemaTabProps): ReactElement {
  const { draft, disabled, onChange, argsError, returnsError } = props;
  return (
    <FieldGroup>
      <Field
        label="Args schema"
        hint="JSON Schema 2020-12 object. Validated at call time."
      >
        <textarea
          value={draft.args_schema_text}
          onChange={(e) => onChange("args_schema_text", e.target.value)}
          rows={10}
          aria-label="Args schema"
          aria-invalid={argsError !== null}
          data-testid="tool-editor-args-schema-input"
          style={{ ...textAreaStyle, fontFamily: "var(--font-mono)" }}
          disabled={disabled}
          spellCheck={false}
        />
        {argsError !== null ? (
          <p
            data-testid="tool-editor-args-schema-error"
            role="alert"
            style={errorTextStyle}
          >
            {argsError}
          </p>
        ) : null}
      </Field>
      <Field
        label="Returns schema"
        hint="JSON Schema 2020-12 object. Validated server-side after the call."
      >
        <textarea
          value={draft.returns_schema_text}
          onChange={(e) => onChange("returns_schema_text", e.target.value)}
          rows={10}
          aria-label="Returns schema"
          aria-invalid={returnsError !== null}
          data-testid="tool-editor-returns-schema-input"
          style={{ ...textAreaStyle, fontFamily: "var(--font-mono)" }}
          disabled={disabled}
          spellCheck={false}
        />
        {returnsError !== null ? (
          <p
            data-testid="tool-editor-returns-schema-error"
            role="alert"
            style={errorTextStyle}
          >
            {returnsError}
          </p>
        ) : null}
      </Field>
    </FieldGroup>
  );
}

// ===========================================================================
// Transport tab.
// ===========================================================================

interface TransportTabProps {
  readonly transport: ToolTransport;
  readonly disabled: boolean;
  readonly onChange: <K extends keyof ToolTransport>(
    key: K,
    next: ToolTransport[K],
  ) => void;
}

const TRANSPORT_KINDS: ReadonlyArray<ToolTransportKind> = [
  "mcp",
  "http",
  "in_process",
  "sandbox",
];

function TransportTab(props: TransportTabProps): ReactElement {
  const { transport, disabled, onChange } = props;
  return (
    <FieldGroup>
      <Field
        label="Kind"
        hint="Dispatch mechanism. Determines which fields below apply."
      >
        <div
          role="radiogroup"
          aria-label="Transport kind"
          style={radioGroupStyle}
          data-testid="tool-editor-transport-kind"
        >
          {TRANSPORT_KINDS.map((kind) => (
            <label
              key={kind}
              style={radioLabelStyle}
              data-testid={`tool-editor-transport-kind-${kind}`}
            >
              <input
                type="radio"
                name="tool-transport-kind"
                value={kind}
                checked={transport.kind === kind}
                onChange={() => onChange("kind", kind)}
                disabled={disabled}
              />
              <span>{kind}</span>
            </label>
          ))}
        </div>
      </Field>
      {transport.kind === "http" ? (
        <Field label="URL template" hint="Variables substituted at call time.">
          <input
            type="text"
            value={transport.url_template ?? ""}
            onChange={(e) => onChange("url_template", e.target.value)}
            aria-label="URL template"
            data-testid="tool-editor-url-template-input"
            placeholder="https://api.example.com/v1/{path}"
            style={textInputStyle}
            disabled={disabled}
          />
        </Field>
      ) : null}
      {transport.kind === "in_process" || transport.kind === "sandbox" ? (
        <Field
          label="Executor"
          hint="Registered runtime executor name (e.g. web_search, library_save)."
        >
          <input
            type="text"
            value={transport.executor ?? ""}
            onChange={(e) => onChange("executor", e.target.value)}
            aria-label="Executor"
            data-testid="tool-editor-executor-input"
            style={textInputStyle}
            disabled={disabled}
          />
        </Field>
      ) : null}
    </FieldGroup>
  );
}

// ===========================================================================
// Permissions tab — scope + status (lifecycle).
// ===========================================================================

function PermissionsTab({ draft, disabled, onChange }: TabProps): ReactElement {
  return (
    <FieldGroup>
      <Field
        label="Scope"
        hint="Read-only contexts can grant read or both; never bare write."
      >
        <div
          role="radiogroup"
          aria-label="Scope"
          style={radioGroupStyle}
          data-testid="tool-editor-scope"
        >
          {(["read", "write", "both"] as ReadonlyArray<ToolScope>).map((s) => (
            <label
              key={s}
              style={radioLabelStyle}
              data-testid={`tool-editor-scope-${s}`}
            >
              <input
                type="radio"
                name="tool-scope"
                value={s}
                checked={draft.scope === s}
                onChange={() => onChange("scope", s)}
                disabled={disabled}
              />
              <span>{s}</span>
            </label>
          ))}
        </div>
      </Field>
      <Field
        label="Status"
        hint="Lifecycle (tools-prd §1.6). Disabled tools preserve grants + audit."
      >
        <div
          role="radiogroup"
          aria-label="Status"
          style={radioGroupStyle}
          data-testid="tool-editor-status"
        >
          {(
            [
              "enabled",
              "disabled",
              "error",
              "pending_review",
            ] as ReadonlyArray<ToolStatus>
          ).map((s) => (
            <label
              key={s}
              style={radioLabelStyle}
              data-testid={`tool-editor-status-${s}`}
            >
              <input
                type="radio"
                name="tool-status"
                value={s}
                checked={draft.status === s}
                onChange={() => onChange("status", s)}
                disabled={disabled}
              />
              <span>{s}</span>
            </label>
          ))}
        </div>
      </Field>
      <Field
        label="Status reason"
        hint="Optional. Required when status moves to disabled or error per audit policy."
      >
        <textarea
          value={draft.status_reason}
          onChange={(e) => onChange("status_reason", e.target.value)}
          rows={2}
          aria-label="Status reason"
          data-testid="tool-editor-status-reason-input"
          style={textAreaStyle}
          disabled={disabled}
        />
      </Field>
    </FieldGroup>
  );
}

// ===========================================================================
// Layout primitives.
// ===========================================================================

function FieldGroup(props: { readonly children: ReactNode }): ReactElement {
  return <div style={fieldGroupStyle}>{props.children}</div>;
}

function Field(props: {
  readonly label: string;
  readonly hint?: string;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div style={fieldStyle}>
      <label style={labelStyle}>{props.label}</label>
      {props.children}
      {props.hint !== undefined ? <p style={hintStyle}>{props.hint}</p> : null}
    </div>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const nameHeaderStyle: CSSProperties = {
  flex: 1,
  minWidth: 200,
  height: 32,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
};

const tabStripStyle: CSSProperties = {
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
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  cursor: "pointer",
});

const panelStyle: CSSProperties = {
  padding: "8px 0",
};

const fieldGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const fieldStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const hintStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
};

const textInputStyle: CSSProperties = {
  height: 30,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

const textAreaStyle: CSSProperties = {
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  resize: "vertical",
  boxSizing: "border-box",
};

const errorTextStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-danger, #d97777)",
  fontFamily: "var(--font-mono)",
};

const radioGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const radioLabelStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
};

const primaryButtonStyle = (saveState: ToolEditorSaveState): CSSProperties => ({
  background:
    saveState === "saved"
      ? "var(--color-success, #16a34a)"
      : saveState === "error"
        ? "var(--color-danger, #dc2626)"
        : "var(--color-accent)",
  color: "var(--color-bg)",
  border: "none",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  cursor: saveState === "saving" ? "wait" : "pointer",
});

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "5px 12px",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};
