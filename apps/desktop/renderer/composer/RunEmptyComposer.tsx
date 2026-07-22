// RunEmptyComposer — the desktop Run cockpit's empty-state composer.
//
// When there is no active run, the cockpit renders the design's "What should we
// run first?" surface (0xCopilot First Run) instead of the plain goal card. This
// binder mounts the shared `OnboardingComposer` (hero + starter chips +
// AssistantComposer: model pill · Tools · attach · send) bound to the SAME
// desktop composer data the in-chat `RunComposer` uses (`useRunComposerBindings`
// — real skills, MCP servers, model catalog), so the empty→live transition never
// swaps the model/tools out from under the user.
//
// The cockpit owns the empty→live seam: on send this calls `ctx.onStartRun` with
// the full selection (goal + model + attachments + web-search), and the cockpit
// binds the fresh run via the `runId` seam WITHOUT remounting the shell
// (FR-3.25). Submitting / error / readiness come down through `ctx` so the
// composer disables, surfaces the actionable start error, and defers to the
// "Set up your model" notice when no model is configured.

import { useCallback, type ReactElement } from "react";

import {
  ComposerConnectorsButton,
  FIRST_RUN_SUGGESTIONS,
  OnboardingComposer,
  type ComposerConnectorsPort,
  type ProviderKeysPort,
  type RunEmptyComposerCtx,
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
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  resolveAirdropClaimsCsv,
} from "../onboarding/airdropClaimsFixture";
import { toReadableRunAttachments } from "../onboarding/firstRunAttachments";

// Substrate-bound singletons — one hidden-input file picker + one single-stage
// attachment adapter for this composer (mirrors RunComposer / FirstRunGate;
// both are stateless). Kept local so we don't reach into another component's
// module-private instances.
const filePicker = new DesktopComposerFilePicker();
const attachmentAdapter = createDesktopAttachmentAdapter();

export interface RunEmptyComposerProps {
  /** The cockpit empty-composer context (start-run seam + readiness/error). */
  readonly ctx: RunEmptyComposerCtx;
  /** Navigate to the Tools (connectors) surface — MCP + non-MCP visibility. */
  readonly onShowConnectors?: () => void;
  /** Navigate to the Skills surface. */
  readonly onOpenSkills?: () => void;
  /**
   * MCP connector surface for the inline Tools popover. When provided, the
   * composer's Tools trigger becomes the connector-aware popover (web-search
   * toggle + connected rows + 1-click connect + Custom MCP). Omitted ⇒ the plain
   * "open the Tools surface" button.
   */
  readonly connectorsPort?: ComposerConnectorsPort;
  /**
   * Provider-keys surface for the model pill's inline "Add a provider key" form.
   * When provided, the model popover opens the inline `KeyForm` sub-view instead
   * of the `onAddKey` deep-link.
   */
  readonly providerKeysPort?: ProviderKeysPort;
}

export function RunEmptyComposer(props: RunEmptyComposerProps): ReactElement {
  const {
    ctx,
    onShowConnectors,
    onOpenSkills,
    connectorsPort,
    providerKeysPort,
  } = props;

  const {
    skills,
    skillsLoading,
    selectedSkills,
    onAttachSkill,
    onRemoveSkill,
    onClearSkills,
    servers,
    serversLoading,
    activeConnectorCount,
    models,
    selectedModel,
    onModelChange,
    onAddCustomModel,
    renderPlusMenu,
  } = useRunComposerBindings();

  // The CSV starter chip resolves to the bundled `airdrop-claims.csv` fixture
  // (rows read as model-visible text via the readable-attachment mapper).
  const resolveAttachment = useCallback(
    (attachmentId: string): Promise<File | null> =>
      attachmentId === AIRDROP_CLAIMS_CSV_ATTACHMENT_ID
        ? resolveAirdropClaimsCsv()
        : Promise.resolve(null),
    [],
  );

  // Inline Tools popover (when a connectors port is injected): owns the per-run
  // web-search toggle + active connector ids, and yields the trigger node + the
  // values threaded into the start-run payload. Disabled while a run is starting.
  const { toolsTrigger, webSearchEnabled, connectorScopes } =
    useDesktopComposerTools({
      connectorsPort,
      disabled: ctx.submitting,
      onAddCustom: onShowConnectors,
    });

  // Send → start the first run through the cockpit seam. The model pill's
  // selection and the composer attachments become the run body; the Tools
  // popover threads the per-run web-search toggle + active connector scopes
  // (RunStartRequest already carries them). The cockpit owns the empty→live
  // binding + the submitting/error state (surfaced back through `ctx`).
  const { onStartRun } = ctx;
  const handleSubmit = useCallback(
    ({
      text,
      attachments,
    }: {
      readonly text: string;
      readonly attachments: ReadonlyArray<unknown>;
    }): void => {
      const model = modelSelectionForId(models, selectedModel);
      const runAttachments = toReadableRunAttachments(attachments);
      onStartRun({
        goal: text,
        model,
        attachments: runAttachments.length > 0 ? runAttachments : undefined,
        webSearchEnabled,
        connectorScopes,
      });
    },
    [models, selectedModel, onStartRun, webSearchEnabled, connectorScopes],
  );

  // With a connectors port → the connector-aware Tools popover; without one →
  // the historical flat button that opens the Tools destination.
  const connectorsTrigger = toolsTrigger ?? (
    <ComposerConnectorsButton
      activeCount={activeConnectorCount}
      open={false}
      onClick={() => onShowConnectors?.()}
      disabled={ctx.submitting}
    />
  );

  return (
    <OnboardingComposer
      connectors={{ servers: [...servers], loading: serversLoading }}
      skills={{ skills: [...skills], loading: skillsLoading }}
      attachmentAdapter={attachmentAdapter}
      filePicker={filePicker}
      renderPlusMenu={renderPlusMenu}
      skillInstructionPrompt={skillInstructionPrompt}
      mcpServerInstructionPrompt={mcpServerInstructionPrompt}
      onShowConnectors={() => onShowConnectors?.()}
      onOpenSkillsSettings={() => onOpenSkills?.()}
      onOpenMcpSettings={() => onShowConnectors?.()}
      selectedSkills={selectedSkills}
      onAttachSkill={onAttachSkill}
      onRemoveSkill={onRemoveSkill}
      onClearSkills={onClearSkills}
      toolsTrigger={connectorsTrigger}
      // Inline "Add a provider key" form inside the model popover (host-owned
      // provider-keys surface); unset ⇒ the pill keeps its `onAddKey` deep-link.
      providerKeysPort={providerKeysPort}
      models={models}
      selectedModel={selectedModel}
      onModelChange={onModelChange}
      onAddCustomModel={onAddCustomModel}
      suggestions={FIRST_RUN_SUGGESTIONS}
      resolveAttachment={resolveAttachment}
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
