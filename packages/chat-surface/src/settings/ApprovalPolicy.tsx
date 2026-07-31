// <ApprovalPolicy /> — the approval-policy block inside Model & behavior
// (DESIGN-SPEC §4 · PRD PR-5.6, FR-5.17). It is the spec relocation of the web
// `sections/ToolUsePolicyPanel.tsx` (read/write/destructive × auto/ask/require/
// block), re-labelled to the three spec axes and moved under Model & behavior:
//
//   Read-only actions              Auto-approve · Ask first
//   Write actions                  Require approval · Ask first · Auto-approve · Block
//   On-chain, spend & destructive  Require approval · Block
//
// with the note that *which* tools each axis covers is chosen per-connector on
// the Connectors page (DESIGN-SPEC §4).
//
// SUBSTRATE-AGNOSTIC + CONTROLLED. Presentation only: it reflects `value` and
// reports an edit through `onChange` with the whole next {@link
// ApprovalPolicyValue} (shallow-merge-safe for the host). It never fetches,
// persists, or touches browser globals — mapping to the `/v1/me/policies/
// tool-use` contract is a host concern injected by the wiring PR. Each axis is a
// `SegmentedControl` (role="radiogroup"/"radio" + aria-checked, DESIGN-SPEC §9).
//
// Colors resolve ONLY to design-system v2 tokens (via the chrome primitives +
// SegmentedControl).

import { Toggle } from "@0x-copilot/design-system";
import { type ReactElement } from "react";

import { SegmentedControl, type SegmentedOption } from "./controls";
import { Frow, SetCard, SetNote } from "./SettingsChrome";

// ---------------------------------------------------------------------------
// Vocabulary — the three spec axes and their allowed modes. Each axis carries a
// DIFFERENT mode set (DESIGN-SPEC §4), so the modes are distinct unions rather
// than one shared enum: a read-only action can never be "block"-only-gated the
// way a write can, and a destructive action offers only require/block.
// ---------------------------------------------------------------------------

/** Read-only tools (search / fetch / summarize) — never mutate state. */
export type ReadOnlyApprovalMode = "auto" | "ask";
/** Write tools (send / post / edit) — change something. */
export type WriteApprovalMode = "require" | "ask" | "auto" | "block";
/** On-chain, spend & destructive tools — irreversible or costly. */
export type DangerApprovalMode = "require" | "block";

export interface ApprovalPolicyValue {
  readonly readOnly: ReadOnlyApprovalMode;
  readonly write: WriteApprovalMode;
  readonly danger: DangerApprovalMode;
  /**
   * Filesystem bypass MASTER switch (PRD-FS-10 §4.3 tier 1). Off by default,
   * and optional so every existing caller keeps compiling and keeps the
   * off posture — a host that has not wired it cannot accidentally advertise
   * bypass.
   *
   * It lives on THIS value rather than in a new settings page because it is
   * the same question the three axes above answer — how autonomously the
   * agent may act — asked about the one capability whose approvals are staged
   * rather than interrupted. Persisted through
   * `WorkspaceBehaviorOverrides.filesystem_bypass_enabled`, so it needs no
   * migration and no new endpoint.
   */
  readonly filesystemBypassEnabled?: boolean;
}

/**
 * The three risk AXES, as distinct from the whole value.
 *
 * They are the fields backed by `/v1/me/policies/tool-use`;
 * `filesystemBypassEnabled` rides `WorkspaceBehaviorOverrides` instead. Naming
 * the axis set keeps the wire mapping in `data/toolUsePolicy.ts` exhaustive
 * over exactly what that endpoint carries, so a future field added to this
 * value cannot be silently posted to a route that has no column for it — the
 * compiler asks which lane it belongs to.
 */
export type ApprovalPolicyAxis = "readOnly" | "write" | "danger";

export interface ApprovalPolicyProps {
  readonly value: ApprovalPolicyValue;
  /** Report an edit — receives the whole next value (host shallow-merges). */
  readonly onChange: (next: ApprovalPolicyValue) => void;
  /** Disable every control (e.g. while the section is loading its snapshot). */
  readonly disabled?: boolean;
}

// ---------------------------------------------------------------------------
// Option sets (DESIGN-SPEC §4 order + labels).
// ---------------------------------------------------------------------------

export const READ_ONLY_APPROVAL_OPTIONS: ReadonlyArray<
  SegmentedOption<ReadOnlyApprovalMode>
> = [
  { value: "auto", label: "Auto-approve" },
  { value: "ask", label: "Ask first" },
];

export const WRITE_APPROVAL_OPTIONS: ReadonlyArray<
  SegmentedOption<WriteApprovalMode>
> = [
  { value: "require", label: "Require approval" },
  { value: "ask", label: "Ask first" },
  { value: "auto", label: "Auto-approve" },
  { value: "block", label: "Block" },
];

export const DANGER_APPROVAL_OPTIONS: ReadonlyArray<
  SegmentedOption<DangerApprovalMode>
> = [
  { value: "require", label: "Require approval" },
  { value: "block", label: "Block" },
];

/** The DESIGN-SPEC §4 note: scope is per-connector, not global. */
export const APPROVAL_POLICY_CONNECTOR_NOTE =
  "Which tools each policy covers is chosen per-connector on the Connectors page.";

/**
 * The bypass row's hint. States the bound in the same words the composer pill
 * uses, because a user who reads only one of the two surfaces must come away
 * with the same rule: bypass suspends the pause inside folders you attached
 * with write permission, and changes nothing anywhere else.
 */
export const FILESYSTEM_BYPASS_HINT =
  "Let the agent apply file changes without asking, inside folders you attached " +
  "with write permission. Anything ungranted still asks, and every change is " +
  "still recorded.";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ApprovalPolicy({
  value,
  onChange,
  disabled = false,
}: ApprovalPolicyProps): ReactElement {
  return (
    <SetCard
      title="Approval policy"
      meta="How autonomously the agent may act, by risk level."
      data-testid="approval-policy"
    >
      <SetNote data-testid="approval-policy-connector-note">
        {APPROVAL_POLICY_CONNECTOR_NOTE}
      </SetNote>

      <Frow
        label="Read-only actions"
        hint="Search, fetch, summarize — anything that doesn't change state."
      >
        <SegmentedControl<ReadOnlyApprovalMode>
          ariaLabel="Read-only actions approval"
          options={withDisabled(READ_ONLY_APPROVAL_OPTIONS, disabled)}
          value={value.readOnly}
          onChange={(readOnly) => onChange({ ...value, readOnly })}
        />
      </Frow>

      <Frow
        label="Write actions"
        hint="Send, post, edit — actions that change something."
      >
        <SegmentedControl<WriteApprovalMode>
          ariaLabel="Write actions approval"
          options={withDisabled(WRITE_APPROVAL_OPTIONS, disabled)}
          value={value.write}
          onChange={(write) => onChange({ ...value, write })}
        />
      </Frow>

      <Frow
        label="On-chain, spend & destructive"
        hint="Transfers, purchases, deletes — irreversible or costly actions."
      >
        <SegmentedControl<DangerApprovalMode>
          ariaLabel="On-chain, spend and destructive actions approval"
          options={withDisabled(DANGER_APPROVAL_OPTIONS, disabled)}
          value={value.danger}
          onChange={(danger) => onChange({ ...value, danger })}
        />
      </Frow>

      {/* Tier 1 of the filesystem bypass. Until this is on, the composer's
          run/message controls are not offered — the pill renders disabled
          Manual and its menu is not mounted at all. */}
      <Frow label="File changes without asking" hint={FILESYSTEM_BYPASS_HINT}>
        <Toggle
          data-testid="filesystem-bypass-toggle"
          aria-label="Allow file changes without asking"
          checked={value.filesystemBypassEnabled === true}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              ...value,
              filesystemBypassEnabled: event.currentTarget.checked,
            })
          }
        />
      </Frow>
    </SetCard>
  );
}

// The SegmentedControl disables per-option, so fold the axis-wide `disabled`
// flag onto each option rather than gating the group (which has no group-level
// disabled prop). Keeps every pill's disabled state honest for assistive tech.
function withDisabled<V extends string>(
  options: ReadonlyArray<SegmentedOption<V>>,
  disabled: boolean,
): ReadonlyArray<SegmentedOption<V>> {
  if (!disabled) return options;
  return options.map((opt) => ({ ...opt, disabled: true }));
}
