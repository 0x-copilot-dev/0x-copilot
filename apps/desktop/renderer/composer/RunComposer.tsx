import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  AssistantComposer,
  ComposerConnectorsButton,
  parseTransportError,
  useTransport,
  type CompleteAttachment,
  type ComposerConnectorsPort,
  type ProviderKeysPort,
  type StartRunError,
} from "@0x-copilot/chat-surface";

import { modelSelectionForId } from "./desktopModelCatalog";
import { useDesktopComposerTools } from "./useDesktopComposerTools";
import { createDesktopAttachmentAdapter } from "./desktopAttachmentAdapter";
import { DesktopComposerFilePicker } from "./DesktopComposerFilePicker";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "./composerPrompts";
import { useRunComposerBindings } from "./useRunComposerBindings";

// Substrate-bound singletons — one hidden-input file picker and one
// single-stage attachment adapter per renderer. Both are stateless, so a module
// singleton is fine (mirrors the web adapter's `composerFilePicker`).
const filePicker = new DesktopComposerFilePicker();
const attachmentAdapter = createDesktopAttachmentAdapter();

export interface RunComposerProps {
  /** Conversation the run dispatch targets. */
  readonly conversationId: string;
  /**
   * Off-live (scrubbed) gate handed down by the Run cockpit through the
   * `renderComposer` seam. When true the composer is read-only — you can't send
   * into a past state (mirrors the base composer's ghost disable).
   */
  readonly disabled: boolean;
  /** Placeholder text (mirrors the cockpit's live/ghost copy). */
  readonly placeholder: string;
  /** Navigate to the Tools (connectors) surface — MCP + non-MCP visibility. */
  readonly onShowConnectors?: () => void;
  /** Navigate to the Skills surface. */
  readonly onOpenSkillsSettings?: () => void;
  /** Open Settings → Provider keys (BYOK model setup). */
  readonly onOpenModelSettings?: () => void;
  /**
   * MCP connector surface for the inline Tools popover. When provided, the
   * composer's connectors trigger becomes the connector-aware Tools popover
   * (web-search toggle + connected rows + 1-click connect + Custom MCP) instead
   * of the flat "open the Tools surface" button. Omitted ⇒ the plain button.
   */
  readonly connectorsPort?: ComposerConnectorsPort;
  /**
   * Provider-keys surface for the model pill's inline "Add a provider key" form.
   * When provided, the model popover opens the inline `KeyForm` sub-view instead
   * of deep-linking to Settings.
   */
  readonly providerKeysPort?: ProviderKeysPort;
}

/**
 * The desktop Run cockpit composer. Mounts the shared `AssistantComposer`
 * (@0x-copilot/chat-surface) — the SAME composer web uses — bound to desktop
 * substrate ports, closing the parity gap with web's Run composer:
 *
 *   - attachments   → `filePicker` + single-stage `attachmentAdapter`
 *   - `/` commands  → skills (`GET /v1/skills`) drive the `/`-menu + skill pills
 *   - connections   → MCP servers (`GET /v1/mcp/servers`) list in the `+` menu;
 *                     the connectors trigger opens the full Tools surface
 *                     (MCP + non-MCP)
 *   - model select  → curated cloud + local models, `depthVisible={false}`
 *
 * The cockpit still owns the ghost/scrub gate (passed in as `disabled`); this
 * component owns run dispatch (`onSubmit` → `POST /v1/agent/runs`, the same
 * endpoint the empty-state goal composer uses, identity derived server-side).
 */
export function RunComposer(props: RunComposerProps): ReactElement {
  const {
    conversationId,
    disabled,
    placeholder,
    onShowConnectors,
    onOpenSkillsSettings,
    onOpenModelSettings,
    connectorsPort,
    providerKeysPort,
  } = props;

  const transport = useTransport();

  // The last run-create failure, surfaced inline above the composer. The
  // in-chat composer's `POST /v1/agent/runs` rejection used to be SWALLOWED
  // (AssistantComposer's `onSubmit` promise was never `.catch`ed); that swallow
  // is now fixed at the chat-surface SSOT, which routes a rejected submit to the
  // composer's `onSubmitError` channel. We consume that channel (`handleSubmitError`
  // below) instead of wrapping our own try/catch — one mechanism. Structured
  // (safe_message / code) so a missing provider key shows an actionable "Add a
  // provider key" CTA (onboarding), not nothing. Cleared on the next successful
  // send or an explicit dismiss.
  const [startError, setStartError] = useState<StartRunError | null>(null);

  // Shared Run-cockpit composer data (skills, MCP servers, model catalog +
  // selection, `+`-menu) — the SAME source the empty-state `RunEmptyComposer`
  // reads, so the two never drift.
  const {
    skills,
    skillsLoading,
    selectedSkills,
    onAttachSkill: handleAttachSkill,
    onRemoveSkill: handleRemoveSkill,
    onClearSkills: handleClearSkills,
    servers,
    serversLoading,
    activeConnectorCount,
    models,
    selectedModel,
    onModelChange: handleModelChange,
    onAddCustomModel: handleAddCustomModel,
    renderPlusMenu,
  } = useRunComposerBindings();

  // Inline Tools popover (when a connectors port is injected): owns the per-run
  // web-search toggle + active connector ids, and yields the trigger node + the
  // run-body values (`webSearchEnabled` / `connectorScopes`) threaded on submit.
  // The popover's "Custom MCP" / pre-registered rows route to the Tools surface.
  const { toolsTrigger, webSearchEnabled, connectorScopes } =
    useDesktopComposerTools({
      connectorsPort,
      disabled,
      onAddCustom: onShowConnectors,
    });

  // With a connectors port → the connector-aware Tools popover; without one →
  // the historical flat button that opens the Tools destination.
  const connectorsTrigger = toolsTrigger ?? (
    <ComposerConnectorsButton
      activeCount={activeConnectorCount}
      open={false}
      onClick={() => onShowConnectors?.()}
      disabled={disabled}
    />
  );

  const handleSubmit = useCallback(
    async ({
      text,
      attachments,
    }: {
      text: string;
      attachments: ReadonlyArray<unknown>;
    }): Promise<void> => {
      if (disabled) return;
      const trimmed = text.trim();
      const atts = attachments as ReadonlyArray<CompleteAttachment>;
      if (trimmed === "" && atts.length === 0) return;
      const model = modelSelectionForId(models, selectedModel);
      const body: Record<string, unknown> = {
        conversation_id: conversationId,
        user_input: text,
      };
      if (model !== null) {
        body.model = model;
      }
      if (atts.length > 0) {
        body.attachments = atts.map(toRunAttachment);
      }
      // Tools popover selections (mirrors `buildRunCreateBody`): web_search
      // defaults on at the runtime, so only an explicit opt-OUT is worth
      // sending; active connector ids become `request_context.connector_scopes`.
      if (webSearchEnabled === false) {
        body.web_search_enabled = false;
      }
      if (
        connectorScopes !== undefined &&
        Object.keys(connectorScopes).length > 0
      ) {
        body.request_context = { connector_scopes: connectorScopes };
      }
      // Let a rejection propagate to `onSubmitError` (handleSubmitError) — the
      // single chat-surface error channel — rather than catching it here. On
      // success, clear any prior failure notice so it can't linger.
      await transport.request({
        method: "POST",
        path: "/v1/agent/runs",
        body,
      });
      setStartError(null);
    },
    [
      conversationId,
      disabled,
      models,
      selectedModel,
      transport,
      webSearchEnabled,
      connectorScopes,
    ],
  );

  // The composer's error channel: a rejected run-create surfaces here instead
  // of vanishing. Never dump the raw transport/IPC envelope — parse the
  // actionable `safe_message` + `code` so a missing provider key shows the one
  // useful line + an "Add a provider key" CTA (matches the empty-state
  // composer, Issue 2).
  const handleSubmitError = useCallback((err: unknown): void => {
    const parsed = parseTransportError(err);
    setStartError({
      message:
        parsed.safeMessage ??
        "Couldn't start the run. Is the backend running and a model configured?",
      code: parsed.code,
      correlationId: parsed.correlationId,
      raw: parsed.raw !== "" ? parsed.raw : undefined,
    });
  }, []);

  return (
    <div data-testid="run-composer" style={runComposerRootStyle}>
      {/* Onboarding backstop: a keyless (or otherwise failed) run-create used to
          be swallowed here. Surface it above the composer with an actionable
          "Add a provider key" CTA that deep-links into Settings → Provider keys
          (the same path the empty-state composer offers). */}
      {startError !== null ? (
        <RunComposerErrorNotice
          error={startError}
          onOpenModelSettings={onOpenModelSettings}
          onDismiss={() => setStartError(null)}
        />
      ) : null}
      <AssistantComposer
        connectors={{ servers: [...servers], loading: serversLoading }}
        skills={{ skills: [...skills], loading: skillsLoading }}
        attachmentAdapter={attachmentAdapter}
        filePicker={filePicker}
        renderPlusMenu={renderPlusMenu}
        skillInstructionPrompt={skillInstructionPrompt}
        mcpServerInstructionPrompt={mcpServerInstructionPrompt}
        onOpenMcpSettings={() => onShowConnectors?.()}
        onOpenSkillsSettings={() => onOpenSkillsSettings?.()}
        onShowConnectors={() => onShowConnectors?.()}
        selectedSkills={selectedSkills}
        onAttachSkill={handleAttachSkill}
        onRemoveSkill={handleRemoveSkill}
        onClearSkills={handleClearSkills}
        connectorsTrigger={connectorsTrigger}
        // Inline "Add a provider key" form inside the model popover (host-owned
        // provider-keys surface); unset ⇒ the pill keeps its deep-link.
        providerKeysPort={providerKeysPort}
        models={models}
        selectedModel={selectedModel}
        onModelChange={handleModelChange}
        onAddCustomModel={handleAddCustomModel}
        // The Fast/Balanced/Deep depth grid is intentionally hidden — the picker
        // is a model list (Cursor/Claude shape), not a depth toggle.
        depthVisible={false}
        // Compact v3 "quiet" composer for the narrow Run rail — start at 2 rows
        // instead of web's roomy 3 (paired with composer.css's auto-height shell).
        minRows={2}
        onSubmit={handleSubmit}
        onSubmitError={handleSubmitError}
        disabled={disabled}
        onOpenSkillsPanel={() => onOpenSkillsSettings?.()}
        // Surfaced for the "Set up your model" path; harmless if unused here.
        onOpenDetailsPanel={onOpenModelSettings ? () => undefined : undefined}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline run-create error notice (onboarding backstop).
//
// The empty-state composer (RunEmptyState) already surfaces a start failure
// with an "Add a provider key" CTA; the in-chat composer had no equivalent, so
// a keyless send here was a silent dead end. This mirrors that treatment: the
// actionable `safe_message` as the primary line, plus the config-error CTA that
// routes into Settings → Provider keys. Presentation only — the host owns the
// navigation via `onOpenModelSettings`.
// ---------------------------------------------------------------------------

interface RunComposerErrorNoticeProps {
  readonly error: StartRunError;
  /** Open Settings → Provider keys. When absent the CTA is hidden. */
  readonly onOpenModelSettings?: () => void;
  readonly onDismiss: () => void;
}

function RunComposerErrorNotice({
  error,
  onOpenModelSettings,
  onDismiss,
}: RunComposerErrorNoticeProps): ReactElement {
  const isConfigError = error.code === "configuration_error";
  return (
    <div role="alert" data-testid="run-composer-error" style={noticeStyle}>
      <div style={noticeRowStyle}>
        <span
          data-testid="run-composer-error-message"
          style={noticeMessageStyle}
        >
          {error.message}
        </span>
        <button
          type="button"
          aria-label="Dismiss"
          data-testid="run-composer-error-dismiss"
          onClick={onDismiss}
          style={noticeDismissStyle}
        >
          ×
        </button>
      </div>
      {isConfigError && onOpenModelSettings !== undefined ? (
        <button
          type="button"
          data-testid="run-composer-error-cta"
          onClick={onOpenModelSettings}
          style={noticeCtaStyle}
        >
          Add a provider key
        </button>
      ) : null}
    </div>
  );
}

const runComposerRootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 0,
};

const noticeStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 8,
  padding: "8px 12px",
  borderRadius: 10,
  background: "var(--color-danger-soft, rgba(229,103,138,.12))",
  border: "1px solid var(--color-danger, #e5678a)",
};

const noticeRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 8,
  width: "100%",
};

const noticeMessageStyle: CSSProperties = {
  minWidth: 0,
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.45,
  color: "var(--color-danger, #e5678a)",
};

const noticeDismissStyle: CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  border: "none",
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1,
  cursor: "pointer",
  fontFamily: "inherit",
  padding: 0,
};

const noticeCtaStyle: CSSProperties = {
  alignSelf: "flex-start",
  background: "var(--color-accent, #5fb2ec)",
  color: "var(--color-accent-contrast, #08131d)",
  border: "1px solid var(--color-accent, #5fb2ec)",
  borderRadius: 8,
  padding: "6px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

// --- Run-create wire mappers (CompleteAttachment → RunAttachmentRequest) ---

interface RunContentPartFile {
  readonly type: "file";
  readonly filename: string;
  readonly data: string;
  readonly mime_type: string;
}
type RunContentPartWire = RunContentPartFile | Record<string, unknown>;

function toRunContentPart(part: Record<string, unknown>): RunContentPartWire {
  if (part.type === "file") {
    return {
      type: "file",
      filename: String(part.name ?? ""),
      data: String(part.data ?? ""),
      mime_type: String(part.mime ?? ""),
    };
  }
  // Image parts (`{ type: "image", image }`) pass through unchanged.
  return { ...part };
}

function toRunAttachment(att: CompleteAttachment): Record<string, unknown> {
  return {
    id: att.id,
    type: att.type,
    name: att.name,
    content_type: att.type !== "" ? att.type : null,
    size: att.size ?? null,
    content: (att.content ?? []).map((part) =>
      toRunContentPart(part as Record<string, unknown>),
    ),
  };
}
