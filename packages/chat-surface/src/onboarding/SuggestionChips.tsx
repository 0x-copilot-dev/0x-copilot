// SuggestionChips — the three FTUE starter chips (PRD-P3 §3.2, SPEC §Copy).
//
// Presentational + host-injected data only. Each chip carries a verbatim
// `title` (the pill label) and `prompt` (inserted into the composer on pick).
// The "Explain a CSV" chip additionally carries an `attachmentId`; the host's
// `resolveAttachment(id)` turns it into a File that the composer attaches via
// `ComposerHandle.addAttachment` (routed through the host's TEXT-adapter path so
// the CSV rows are model-visible — a base64 `file` part is summarised by
// name/size only). This component never resolves the attachment itself; it only
// emits the picked suggestion.

import type { ReactElement } from "react";

import { Icon } from "../icons/Icon";
import type { IconName } from "../icons/paths";

export interface FirstRunSuggestion {
  readonly id: string;
  readonly icon: IconName;
  /** Chip label — verbatim SPEC copy. */
  readonly title: string;
  /** Inserted into the composer on pick — verbatim SPEC copy. */
  readonly prompt: string;
  /**
   * Present only on the CSV chip → the host resolves it to a File via
   * `resolveAttachment`. The value doubles as the bundled fixture id
   * (`airdrop-claims.csv`) both hosts key on.
   */
  readonly attachmentId?: string;
}

/**
 * The three v1 starter chips (README §1 / SPEC §Copy strings). Verbatim —
 * pinned by `SuggestionChips.test.tsx` so a paraphrase fails CI.
 */
export const FIRST_RUN_SUGGESTIONS: readonly FirstRunSuggestion[] = [
  {
    id: "watch-wallet",
    icon: "eye",
    title: "Watch a wallet",
    prompt:
      "Watch 0x7f3C…a92C and alert me on any transfer over $500. Keep running in the background.",
  },
  {
    id: "draft-thread",
    icon: "send",
    title: "Draft a launch thread",
    prompt:
      "Draft a 6-post launch thread… Ask me 3 questions first, then write it.",
  },
  {
    id: "explain-csv",
    icon: "doc",
    title: "Explain a CSV",
    prompt: "Explain this CSV… chart the top movers.",
    attachmentId: "airdrop-claims.csv",
  },
];

export interface SuggestionChipsProps {
  /** Defaults to {@link FIRST_RUN_SUGGESTIONS}. */
  readonly suggestions?: readonly FirstRunSuggestion[];
  readonly onPick: (suggestion: FirstRunSuggestion) => void;
  readonly disabled?: boolean;
}

export function SuggestionChips({
  suggestions = FIRST_RUN_SUGGESTIONS,
  onPick,
  disabled = false,
}: SuggestionChipsProps): ReactElement {
  return (
    <div className="fr-chips" data-testid="first-run-chips">
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.id}
          type="button"
          className="fr-chip"
          disabled={disabled}
          onClick={() => onPick(suggestion)}
          data-testid={`first-run-chip-${suggestion.id}`}
        >
          <span className="fr-chip__ic" aria-hidden="true">
            <Icon name={suggestion.icon} size={14} />
          </span>
          <span className="fr-chip__label">{suggestion.title}</span>
        </button>
      ))}
    </div>
  );
}
