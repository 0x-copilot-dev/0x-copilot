// RunEmptyComposer — the web Run cockpit's empty-state composer (PRD-05).
//
// When there is no active run, the cockpit renders the design's "What should we
// run first?" surface instead of the plain goal card. This web binder mounts the
// shared `OnboardingComposer` (hero + starter chips + AssistantComposer: model
// pill · attach · Tools popover · send). Model catalog / Tools popover /
// connectors / attachments all come from `useWebRunComposerTools` — the ONE
// source of truth shared with the in-chat `RunComposer` (PRD web-convergence
// AD-3), so the two web composers never diverge. Mirrors the desktop
// `RunEmptyComposer`; the two hosts can't share code (`apps/* → apps/*` banned).
//
// The cockpit owns the empty→live seam: on send this calls `ctx.onStartRun` with
// the full selection (goal + model + attachments + web-search + connector
// scopes), and the cockpit binds the fresh run via the `runId` seam WITHOUT
// remounting the shell (FR-3.25). Submitting / error / readiness come down
// through `ctx`.

import { useCallback, type ReactElement } from "react";

import {
  FIRST_RUN_SUGGESTIONS,
  OnboardingComposer,
  type RunEmptyComposerCtx,
} from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  resolveAirdropClaimsCsv,
} from "../onboarding/airdropClaimsAttachment";
import {
  createOnboardingChatSurfaceAttachmentAdapter,
  mcpServerInstructionPrompt,
  onboardingFilePicker,
  renderOnboardingPlusMenu,
  skillInstructionPrompt,
} from "../onboarding/onboardingComposerAdapter";
import { useWebRunComposerTools } from "./useWebRunComposerTools";

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
  const {
    models,
    selectedModel,
    onModelChange,
    providerKeysPort,
    toolsTrigger,
    buildRunStartRequest,
  } = useWebRunComposerTools(identity);

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
    (input: {
      readonly text: string;
      readonly attachments: ReadonlyArray<unknown>;
    }): void => {
      onStartRun(buildRunStartRequest(input));
    },
    [onStartRun, buildRunStartRequest],
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
      models={models}
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
