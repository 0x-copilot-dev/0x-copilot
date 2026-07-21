// OnboardingComposer — State B of the FTUE (PRD-P3 §3.3, SPEC §Copy strings).
//
// The "What should we run first?" surface. Mounts the EXISTING shared
// `AssistantComposer` (model pill · attach · send · hint row) under the first-run
// H1 + the three starter chips — no re-authoring of the composer. Owns the
// `ComposerHandle` ref so a chip pick can `setText` the verbatim prompt and,
// for the CSV chip, `addAttachment` the host-resolved File (routed through the
// host's TEXT-adapter path so the rows are model-visible).
//
// Substrate-clean: all I/O is host-injected (attachmentAdapter / filePicker /
// resolveAttachment / onSubmit). The tools pill is a P4 slot (`toolsTrigger`).

import {
  forwardRef,
  useCallback,
  useRef,
  type ForwardedRef,
  type ReactElement,
  type ReactNode,
} from "react";

import type { McpServer, ModelCatalogModel, Skill } from "@0x-copilot/api-types";

import {
  AssistantComposer,
  type AssistantComposerPlusMenuSlotArgs,
  type AttachmentAdapter,
  type ComposerHandle,
} from "../composer";
import type { FilePickerPort } from "../ports/FilePickerPort";
import type { StartRunError } from "../destinations/run";
import {
  FIRST_RUN_SUGGESTIONS,
  SuggestionChips,
  type FirstRunSuggestion,
} from "./SuggestionChips";

/** Verbatim SPEC copy — pinned by `OnboardingComposer.test.tsx`. */
export const ONBOARDING_COMPOSER_COPY = {
  h1: "What should we run first?",
  placeholder:
    'Tell it what you want in plain words — "watch my wallet", "draft the thread"…',
} as const;

export interface OnboardingComposerProps {
  // --- host substrate wiring (identical shapes to RunComposer → AssistantComposer) ---
  readonly connectors: { readonly servers: readonly McpServer[]; readonly loading: boolean };
  readonly skills: { readonly skills: readonly Skill[]; readonly loading: boolean };
  readonly attachmentAdapter?: AttachmentAdapter;
  readonly filePicker: FilePickerPort;
  readonly renderPlusMenu: (a: AssistantComposerPlusMenuSlotArgs) => ReactNode;
  readonly skillInstructionPrompt: (displayName: string) => string;
  readonly mcpServerInstructionPrompt: (displayName: string) => string;
  readonly onShowConnectors: () => void;
  readonly onOpenSkillsSettings: () => void;
  readonly onOpenMcpSettings: () => void;
  readonly selectedSkills?: readonly Skill[];
  readonly onAttachSkill?: (skill: Skill) => void;
  readonly onRemoveSkill?: (skillId: string) => void;
  readonly onClearSkills?: () => void;

  // --- model controls (same as RunComposer; label may carry "· N%" from P2) ---
  readonly models: Array<ModelCatalogModel & { disabled?: boolean }>;
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
  readonly onAddCustomModel?: (slug: string) => void;

  // --- first-run specifics ---
  readonly suggestions?: readonly FirstRunSuggestion[];
  /** Host resolves a chip's attachmentId to a File (fetch/IPC lives in the host). */
  readonly resolveAttachment?: (attachmentId: string) => Promise<File | null>;
  /**
   * Raised on send; the host binder maps CompleteAttachment[] →
   * RunAttachmentRequest[] and drives `useFirstRunLaunch.launch()`.
   */
  readonly onSubmit: (payload: {
    text: string;
    attachments: ReadonlyArray<unknown>;
  }) => void | Promise<void>;
  /** Inline error above the composer (keyless send etc.) — reuses StartRunError. */
  readonly startError?: StartRunError | null;
  /** Route to the gate's KeyForm on a configuration_error CTA (not Settings). */
  readonly onAddKey?: () => void;
  readonly onDismissError?: () => void;
  /** P4 tools pill slot; omitted until P4 wires the tools popover. */
  readonly toolsTrigger?: ReactNode;
  readonly disabled?: boolean;
}

function OnboardingComposerInner(
  props: OnboardingComposerProps,
  ref: ForwardedRef<ComposerHandle>,
): ReactElement {
  const {
    connectors,
    skills,
    attachmentAdapter,
    filePicker,
    renderPlusMenu,
    skillInstructionPrompt,
    mcpServerInstructionPrompt,
    onShowConnectors,
    onOpenSkillsSettings,
    onOpenMcpSettings,
    selectedSkills,
    onAttachSkill,
    onRemoveSkill,
    onClearSkills,
    models,
    selectedModel,
    onModelChange,
    onAddCustomModel,
    suggestions = FIRST_RUN_SUGGESTIONS,
    resolveAttachment,
    onSubmit,
    startError = null,
    onAddKey,
    onDismissError,
    toolsTrigger,
    disabled = false,
  } = props;

  // Local handle for chip picks, bridged to the forwarded ref (AssistantComposer
  // pattern: one handle drives both setText/addAttachment here and any external
  // consumer).
  const composerRef = useRef<ComposerHandle | null>(null);
  const setComposerRef = useCallback(
    (handle: ComposerHandle | null): void => {
      composerRef.current = handle;
      if (typeof ref === "function") {
        ref(handle);
      } else if (ref) {
        ref.current = handle;
      }
    },
    [ref],
  );

  const handlePick = useCallback(
    async (suggestion: FirstRunSuggestion): Promise<void> => {
      composerRef.current?.setText(suggestion.prompt);
      if (suggestion.attachmentId && resolveAttachment) {
        const file = await resolveAttachment(suggestion.attachmentId);
        if (file) {
          await composerRef.current?.addAttachment(file);
        }
      }
      composerRef.current?.focus();
    },
    [resolveAttachment],
  );

  return (
    <div className="fr-compose" data-testid="first-run-composer">
      <div className="fr-hero">
        <h1 className="fr-hero__title" data-testid="first-run-composer-h1">
          {ONBOARDING_COMPOSER_COPY.h1}
        </h1>
      </div>

      <SuggestionChips
        suggestions={suggestions}
        onPick={(s) => void handlePick(s)}
        disabled={disabled}
      />

      {startError !== null ? (
        <div className="fr-cerr" role="alert" data-testid="first-run-composer-error">
          <div className="fr-cerr__row">
            <span className="fr-cerr__msg" data-testid="first-run-composer-error-message">
              {startError.message}
            </span>
            {onDismissError ? (
              <button
                type="button"
                className="fr-cerr__dismiss"
                aria-label="Dismiss"
                data-testid="first-run-composer-error-dismiss"
                onClick={onDismissError}
              >
                ×
              </button>
            ) : null}
          </div>
          {startError.code === "configuration_error" && onAddKey ? (
            <button
              type="button"
              className="fr-cerr__cta"
              data-testid="first-run-composer-error-cta"
              onClick={onAddKey}
            >
              Add a key
            </button>
          ) : null}
        </div>
      ) : null}

      <AssistantComposer
        ref={setComposerRef}
        connectors={{ servers: [...connectors.servers], loading: connectors.loading }}
        skills={{ skills: [...skills.skills], loading: skills.loading }}
        attachmentAdapter={attachmentAdapter}
        filePicker={filePicker}
        renderPlusMenu={renderPlusMenu}
        skillInstructionPrompt={skillInstructionPrompt}
        mcpServerInstructionPrompt={mcpServerInstructionPrompt}
        onOpenMcpSettings={onOpenMcpSettings}
        onOpenSkillsSettings={onOpenSkillsSettings}
        onShowConnectors={onShowConnectors}
        selectedSkills={selectedSkills}
        onAttachSkill={onAttachSkill}
        onRemoveSkill={onRemoveSkill}
        onClearSkills={onClearSkills}
        connectorsTrigger={toolsTrigger}
        models={models}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        onAddCustomModel={onAddCustomModel}
        depthVisible={false}
        // Hero surface — web's roomy 3 rows (not the narrow Run rail's 2).
        minRows={3}
        placeholder={ONBOARDING_COMPOSER_COPY.placeholder}
        onSubmit={onSubmit}
        disabled={disabled}
      />
    </div>
  );
}

export const OnboardingComposer = forwardRef<
  ComposerHandle,
  OnboardingComposerProps
>(OnboardingComposerInner);
OnboardingComposer.displayName = "OnboardingComposer";
