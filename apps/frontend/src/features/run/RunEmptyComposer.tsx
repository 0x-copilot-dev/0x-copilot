// RunEmptyComposer — the web Run cockpit's empty-state composer (PRD-05).
//
// When there is no active run, the cockpit renders the design's "What should we
// run first?" surface instead of the plain goal card. This web binder mounts the
// shared `OnboardingComposer` (hero + starter chips + AssistantComposer: model
// pill · attach · Tools popover · send) bound to the SAME web substrate
// touchpoints the FTUE composer uses (`useOnboardingComposerModels` — the live
// `/v1/agent/models` catalog — plus the reused ChatScreen attachment adapter /
// file picker / `+` menu). Mirrors the desktop `RunEmptyComposer`; the two hosts
// cannot share code (`apps/* → apps/*` is banned), so they duplicate the same
// wiring over the shared component contract.
//
// The cockpit owns the empty→live seam: on send this calls `ctx.onStartRun` with
// the full selection (goal + model + attachments + web-search + connector
// scopes), and the cockpit binds the fresh run via the `runId` seam WITHOUT
// remounting the shell (FR-3.25). Submitting / error / readiness come down
// through `ctx`.

import { useCallback, useMemo, useState, type ReactElement } from "react";

import {
  FIRST_RUN_SUGGESTIONS,
  OnboardingComposer,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
  type ProviderKeysPort,
  type RunEmptyComposerCtx,
} from "@0x-copilot/chat-surface";
import type { ConversationConnectorScopes } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { ChatToolsTrigger } from "../chat/components/composer/ChatToolsTrigger";
import { createComposerConnectorsPort } from "../connectors/composerConnectorsPort";
import { createFirstRunProviderKeysPort } from "../onboarding/firstRunProviderKeysPort";
import { resolveAirdropClaimsCsv } from "../onboarding/airdropClaimsAttachment";
import { AIRDROP_CLAIMS_CSV_ATTACHMENT_ID } from "../onboarding/airdropClaimsAttachment";
import { toReadableRunAttachments } from "../onboarding/firstRunAttachments";
import {
  createOnboardingChatSurfaceAttachmentAdapter,
  mcpServerInstructionPrompt,
  onboardingFilePicker,
  renderOnboardingPlusMenu,
  skillInstructionPrompt,
} from "../onboarding/onboardingComposerAdapter";
import {
  modelSelectionForId,
  useOnboardingComposerModels,
} from "../onboarding/useOnboardingComposerModels";

// Substrate-bound singleton — one bridged onboarding attachment adapter for the
// composer (mirrors the FTUE mount's module singleton).
const attachmentAdapter = createOnboardingChatSurfaceAttachmentAdapter();

/** No-op for the composer's connector/skill Settings deep-links — the web run
 *  cockpit surfaces those elsewhere; the empty composer stays minimal. */
function noop(): void {
  /* intentional no-op */
}

export interface RunEmptyComposerProps {
  /** The cockpit empty-composer context (start-run seam + readiness/error). */
  readonly ctx: RunEmptyComposerCtx;
  /** Signed-in identity — threaded to the live model catalog. */
  readonly identity: RequestIdentity;
}

export function RunEmptyComposer({
  ctx,
  identity,
}: RunEmptyComposerProps): ReactElement {
  // Live `/v1/agent/models` catalog (never a hardcoded list); no local download
  // here, so `localModelPct`/`modelName` stay null (a BYOK/cloud engine).
  const {
    models: composerModels,
    selectedModel,
    onModelChange,
  } = useOnboardingComposerModels({
    identity,
    localModelPct: null,
    modelName: null,
  });

  // Composer-chrome parity: the inline Tools popover state (web-search default on
  // + per-run active connectors), owned here and threaded into the run body.
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [activeConnectorIds, setActiveConnectorIds] = useState<
    readonly string[]
  >([]);
  const [toolsOpen, setToolsOpen] = useState(false);

  const connectorsPort = useMemo<ComposerConnectorsPort>(
    () => createComposerConnectorsPort(identity),
    [identity],
  );
  const providerKeysPort = useMemo<ProviderKeysPort>(
    () => createFirstRunProviderKeysPort(),
    [],
  );

  const onToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setActiveConnectorIds((prev) =>
        active
          ? prev.includes(serverId)
            ? prev
            : [...prev, serverId]
          : prev.filter((id) => id !== serverId),
      );
    },
    [],
  );

  const onConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (entry.requiresPreRegisteredClient) {
        // The run cockpit has no custom-config overlay; connect flows for
        // pre-registered vendors live in Settings → Tools.
        return;
      }
      void connectorsPort
        .installFromCatalog(entry.slug)
        .then((server) => connectorsPort.beginAuth(server.server_id))
        .catch(() => {
          // Workspace-authorize only; a failed install surfaces later via the
          // run-time `mcp_auth_required` card.
        });
    },
    [connectorsPort],
  );

  // Active connector ids → `request_context.connector_scopes` (active → `[]`).
  const connectorScopes = useMemo<
    ConversationConnectorScopes | undefined
  >(() => {
    if (activeConnectorIds.length === 0) {
      return undefined;
    }
    const scopes: Record<string, readonly string[] | null> = {};
    for (const id of activeConnectorIds) {
      scopes[id] = [];
    }
    return scopes;
  }, [activeConnectorIds]);

  const toolsTrigger = useMemo(
    () => (
      <ChatToolsTrigger
        port={connectorsPort}
        open={toolsOpen}
        onOpenChange={setToolsOpen}
        webSearchEnabled={webSearchEnabled}
        onToggleWebSearch={setWebSearchEnabled}
        activeConnectorIds={activeConnectorIds}
        onToggleConnector={onToggleConnector}
        onConnectCatalog={onConnectCatalog}
        onAddCustom={noop}
      />
    ),
    [
      connectorsPort,
      toolsOpen,
      webSearchEnabled,
      activeConnectorIds,
      onToggleConnector,
      onConnectCatalog,
    ],
  );

  // The CSV starter chip resolves to the bundled `airdrop-claims.csv` fixture.
  const resolveAttachment = useCallback(
    (attachmentId: string): Promise<File | null> =>
      attachmentId === AIRDROP_CLAIMS_CSV_ATTACHMENT_ID
        ? resolveAirdropClaimsCsv()
        : Promise.resolve(null),
    [],
  );

  const { onStartRun } = ctx;
  const handleSubmit = useCallback(
    ({
      text,
      attachments,
    }: {
      readonly text: string;
      readonly attachments: ReadonlyArray<unknown>;
    }): void => {
      const model = modelSelectionForId(composerModels, selectedModel);
      const runAttachments = toReadableRunAttachments(attachments);
      onStartRun({
        goal: text,
        model,
        attachments: runAttachments.length > 0 ? runAttachments : undefined,
        webSearchEnabled,
        connectorScopes,
      });
    },
    [
      composerModels,
      selectedModel,
      onStartRun,
      webSearchEnabled,
      connectorScopes,
    ],
  );

  return (
    <OnboardingComposer
      connectors={{ servers: [], loading: false }}
      skills={{ skills: [], loading: false }}
      attachmentAdapter={attachmentAdapter}
      filePicker={onboardingFilePicker}
      renderPlusMenu={renderOnboardingPlusMenu}
      skillInstructionPrompt={skillInstructionPrompt}
      mcpServerInstructionPrompt={mcpServerInstructionPrompt}
      onShowConnectors={noop}
      onOpenSkillsSettings={noop}
      onOpenMcpSettings={noop}
      models={composerModels}
      selectedModel={selectedModel}
      onModelChange={onModelChange}
      suggestions={FIRST_RUN_SUGGESTIONS}
      resolveAttachment={resolveAttachment}
      toolsTrigger={toolsTrigger}
      providerKeysPort={providerKeysPort}
      onSubmit={handleSubmit}
      startError={ctx.startError}
      onDismissError={ctx.dismissError}
      // A configuration_error's "Add a key" CTA deep-links to Provider keys.
      onAddKey={ctx.onOpenModelSettings}
      // Inert while a run is starting OR no model is configured yet — the
      // cockpit's "Set up your model" notice below carries the setup CTA.
      disabled={ctx.submitting || !ctx.modelReady}
    />
  );
}
