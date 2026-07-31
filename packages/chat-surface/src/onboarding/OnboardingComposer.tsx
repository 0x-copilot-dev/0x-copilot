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
// resolveAttachment / onSubmit). The per-run Tools pill is supplied as a
// host-bound slot beside the model selector.

import {
  forwardRef,
  useCallback,
  useRef,
  type ForwardedRef,
  type ReactElement,
} from "react";

import type { McpServer, Skill } from "@0x-copilot/api-types";

import {
  AssistantComposer,
  type AssistantComposerProps,
  type ComposerHandle,
} from "../composer";
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

/**
 * The composer props this surface simply hands on.
 *
 * DELIBERATELY a derivation of {@link AssistantComposerProps} rather than a
 * hand-written copy of it. The copy is how the FTUE lost features one at a
 * time: a prop added to the composer had to be re-declared here, destructured
 * here, and forwarded here, and any of the three being missed produced a
 * control that worked in the Run rail and was simply absent on first run. It
 * happened to `workspaceGrantPort` (PRD-FS-10 §7) and then, identically, to
 * `bypassTrigger`. Deriving means a new composer prop reaches first run by
 * doing nothing at all.
 *
 * Three props are NOT forwardable because this surface fixes them: `minRows`
 * (the hero's roomy 3 rows, not the rail's 2), `placeholder` (the SPEC hero
 * copy), and `depthVisible` (off — first run is not where you tune reasoning
 * depth).
 *
 * `connectors` / `skills` are re-declared for their READONLY element types.
 * Hosts hold these lists in state and pass them straight through; requiring a
 * mutable array would push a defensive copy onto every call site instead of the
 * one here.
 */
type ForwardedComposerProps = Omit<
  AssistantComposerProps,
  "connectors" | "skills" | "depthVisible" | "minRows" | "placeholder"
>;

export interface OnboardingComposerProps extends ForwardedComposerProps {
  readonly connectors: {
    readonly servers: readonly McpServer[];
    readonly loading: boolean;
  };
  readonly skills: {
    readonly skills: readonly Skill[];
    readonly loading: boolean;
  };
  /**
   * Required here though optional on the composer: a first-run surface with no
   * model list has nothing to send with, and silently rendering an empty model
   * pill is how that goes unnoticed.
   */
  readonly models: NonNullable<AssistantComposerProps["models"]>;
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;

  // --- first-run specifics (nothing below exists on the composer) ---
  readonly suggestions?: readonly FirstRunSuggestion[];
  /** Host resolves a chip's attachmentId to a File (fetch/IPC lives in the host). */
  readonly resolveAttachment?: (attachmentId: string) => Promise<File | null>;
  /** Inline error above the composer (keyless send etc.) — reuses StartRunError. */
  readonly startError?: StartRunError | null;
  /** Route to the gate's KeyForm on a configuration_error CTA (not Settings). */
  readonly onAddKey?: () => void;
  readonly onDismissError?: () => void;
}

function OnboardingComposerInner(
  props: OnboardingComposerProps,
  ref: ForwardedRef<ComposerHandle>,
): ReactElement {
  // Only what THIS surface reads or reshapes is named; everything else rides
  // `composerProps` to the composer untouched. Do not re-add a prop here just
  // to pass it on — that is the drift this shape exists to prevent.
  const {
    connectors,
    skills,
    suggestions = FIRST_RUN_SUGGESTIONS,
    resolveAttachment,
    startError = null,
    onAddKey,
    onDismissError,
    // Read here (the chips + error strip disable with the composer) and still
    // forwarded below.
    disabled = false,
    // Overridable, but false is the structural truth of this surface: it IS the
    // pre-first-message composer (FTUE state B / the cockpit's empty state) and
    // the host swaps it away the moment a run starts. Nothing is inferred from
    // a transcript — that is the guess PRD-FS-10 §6.3 forbids.
    hasSentFirstMessage = false,
    ...composerProps
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
        <div
          className="fr-cerr"
          role="alert"
          data-testid="first-run-composer-error"
        >
          <div className="fr-cerr__row">
            <span
              className="fr-cerr__msg"
              data-testid="first-run-composer-error-message"
            >
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
        {...composerProps}
        connectors={{
          servers: [...connectors.servers],
          loading: connectors.loading,
        }}
        skills={{ skills: [...skills.skills], loading: skills.loading }}
        hasSentFirstMessage={hasSentFirstMessage}
        disabled={disabled}
        depthVisible={false}
        // Hero surface — web's roomy 3 rows (not the narrow Run rail's 2).
        minRows={3}
        placeholder={ONBOARDING_COMPOSER_COPY.placeholder}
      />
    </div>
  );
}

export const OnboardingComposer = forwardRef<
  ComposerHandle,
  OnboardingComposerProps
>(OnboardingComposerInner);
OnboardingComposer.displayName = "OnboardingComposer";
