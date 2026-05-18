// OffboardingWizard — controlled-handoff for U-T5.
//
// Source: team-memory-cmdk-prd.md §1.5 + §7.1 + cross-audit §9.8 Q1.
// Atlas does NOT ship a naive admin-force-transfer endpoint; handoff is
// always controlled — admin picks a new owner per asset before the
// offboard transaction. The wizard surfaces the per-asset choices and
// submits a single `OffboardingRequest`.
//
// Steps (driven by the shared `useStepMachine` — DRY: no new state
// machine):
//   1. Confirm target user.
//   2. Per-asset reassignment — for each owned asset (agent / project /
//      tool / connector), pick a new owner from the supplied
//      `personOptions` autocomplete. Asset kinds the server does NOT
//      cascade in v1 (agent / tool / connector — cross-audit §9.8 Q1)
//      render an inline notice instead of the picker, so the admin sees
//      "Re-assign manually after offboard — <asset.label>" before
//      confirming. Only `kind: "project"` reassignments are included in
//      the submitted request.
//   3. Review + confirm. Submits `OffboardingRequest` via `onOffboard`.
//
// The wizard owns no fetch — `personOptions` is a passed prop; the host
// is responsible for fetching the Person catalogue and filtering out
// the offboardee.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  ItemRef,
  OffboardingReassignment,
  OffboardingRequest,
  Person,
  UserId,
} from "@enterprise-search/api-types";

import { useStepMachine } from "../tools/onboarding/useStepMachine";

const STEP_LABELS: ReadonlyArray<string> = [
  "Confirm",
  "Reassign assets",
  "Review",
];

/**
 * One owned asset the wizard renders. The host pre-computes the label
 * (the wizard does not resolve `ItemRef` itself — it's a modal flow,
 * not a long-lived view, so a snapshot is fine).
 */
export interface OffboardingAsset {
  readonly ref: ItemRef;
  /** Human-readable label rendered to the admin (e.g. "Acme renewal"). */
  readonly label: string;
}

/**
 * Which asset kinds the server actually cascades on offboarding. Per
 * cross-audit §9.8 Q1, only `project` reassignments are wired in v1;
 * the rest surface as "re-assign manually after offboard" notices so
 * the admin knows what they're signing.
 */
const CASCADING_KINDS: ReadonlySet<ItemRef["kind"]> = new Set(["project"]);

export interface OffboardingWizardProps {
  /** The teammate being offboarded. */
  readonly target: Person;
  /** Assets owned by `target` (server-projected). Ordered by host. */
  readonly assets: ReadonlyArray<OffboardingAsset>;
  /**
   * Person autocomplete options (already filtered by the host to exclude
   * the offboardee and any guests, per server invariants). The wizard
   * owns no fetch — this is a static list.
   */
  readonly personOptions: ReadonlyArray<Person>;
  /**
   * Host transport. Receives the assembled OffboardingRequest on confirm.
   * Resolves to `true` on success or `false` on failure. The host owns
   * any post-confirm navigation / toast.
   */
  readonly onOffboard: (req: OffboardingRequest) => Promise<boolean>;
  /** Cancel — host owns the dirty-prompt confirm guard. */
  readonly onCancel?: () => void;
  /** Called after a successful offboard. */
  readonly onDone?: () => void;
}

interface ReassignmentDraft {
  readonly assetKey: string;
  readonly asset: OffboardingAsset;
  /** `null` until the admin picks a new owner. */
  readonly newOwnerUserId: UserId | null;
}

function refKey(ref: ItemRef): string {
  return `${ref.kind}:${ref.id}`;
}

export function OffboardingWizard(props: OffboardingWizardProps): ReactElement {
  const { target, assets, personOptions, onOffboard, onCancel, onDone } = props;

  const stepper = useStepMachine({ totalSteps: STEP_LABELS.length });

  // Split assets up-front so the wizard renders the right primitive per
  // step and the review only enumerates cascading kinds.
  const cascadingAssets = useMemo<ReadonlyArray<OffboardingAsset>>(
    () => assets.filter((a) => CASCADING_KINDS.has(a.ref.kind)),
    [assets],
  );
  const nonCascadingAssets = useMemo<ReadonlyArray<OffboardingAsset>>(
    () => assets.filter((a) => !CASCADING_KINDS.has(a.ref.kind)),
    [assets],
  );

  const [drafts, setDrafts] = useState<ReadonlyArray<ReassignmentDraft>>(() =>
    cascadingAssets.map((a) => ({
      assetKey: refKey(a.ref),
      asset: a,
      newOwnerUserId: null,
    })),
  );

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const setDraftOwner = useCallback(
    (assetKey: string, newOwner: UserId | null) => {
      setDrafts((prev) =>
        prev.map((d) =>
          d.assetKey === assetKey ? { ...d, newOwnerUserId: newOwner } : d,
        ),
      );
    },
    [],
  );

  const allCascadingAssigned = drafts.every((d) => d.newOwnerUserId !== null);

  const canAdvance = useMemo(() => {
    switch (stepper.currentStep) {
      case 0:
        return true; // confirm step always advances
      case 1:
        return allCascadingAssigned;
      default:
        return false;
    }
  }, [stepper.currentStep, allCascadingAssigned]);

  const handleSubmit = useCallback(async () => {
    if (submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const reassignments: ReadonlyArray<OffboardingReassignment> = drafts
        .filter(
          (d): d is ReassignmentDraft & { newOwnerUserId: UserId } =>
            d.newOwnerUserId !== null,
        )
        .map((d) => ({
          asset: d.asset.ref,
          new_owner_user_id: d.newOwnerUserId,
        }));
      const req: OffboardingRequest = {
        target_user_id: target.id,
        reassignments,
      };
      const ok = await onOffboard(req);
      if (!ok) {
        setSubmitError("Offboarding failed. Try again.");
      } else {
        setSubmitted(true);
      }
    } finally {
      setSubmitting(false);
    }
  }, [submitting, drafts, target.id, onOffboard]);

  const handleDone = useCallback(() => {
    onDone?.();
  }, [onDone]);

  return (
    <section
      aria-labelledby="offboarding-heading"
      data-testid="offboarding-wizard"
      data-current-step={stepper.currentStep}
      style={containerStyle}
    >
      <header style={headerStyle}>
        <h2 id="offboarding-heading" style={titleStyle}>
          Offboard {target.display_name}
        </h2>
        <p style={subtitleStyle}>
          Controlled handoff. Atlas does <strong>not</strong> force-transfer —
          you pick a new owner for each asset before confirming.
        </p>
      </header>

      <nav aria-label="Offboarding wizard steps" style={stepperStyle}>
        <ol style={stepperListStyle}>
          {STEP_LABELS.map((label, idx) => {
            const isCurrent = idx === stepper.currentStep;
            const done = idx < stepper.currentStep;
            return (
              <li
                key={label}
                aria-current={isCurrent ? "step" : undefined}
                style={stepperItemStyle(isCurrent, done)}
                data-testid={`offboarding-step-${idx}`}
              >
                <span
                  aria-hidden="true"
                  style={stepperIndexStyle(isCurrent, done)}
                >
                  {idx + 1}
                </span>
                <span>{label}</span>
              </li>
            );
          })}
        </ol>
      </nav>

      <div data-testid="offboarding-body" style={bodyStyle}>
        {stepper.currentStep === 0 ? (
          <ConfirmStep target={target} assetCount={assets.length} />
        ) : null}
        {stepper.currentStep === 1 ? (
          <ReassignStep
            drafts={drafts}
            personOptions={personOptions}
            onPickOwner={setDraftOwner}
            nonCascadingAssets={nonCascadingAssets}
          />
        ) : null}
        {stepper.currentStep === 2 ? (
          <ReviewStep
            target={target}
            drafts={drafts}
            nonCascadingAssets={nonCascadingAssets}
            personOptions={personOptions}
          />
        ) : null}
      </div>

      {submitError !== null ? (
        <p role="alert" style={errorStyle} data-testid="offboarding-error">
          {submitError}
        </p>
      ) : null}

      <footer style={footerStyle}>
        <div>
          {onCancel !== undefined && !submitted ? (
            <button
              type="button"
              onClick={onCancel}
              style={ghostButtonStyle}
              data-testid="offboarding-cancel"
            >
              Cancel
            </button>
          ) : null}
        </div>
        <div style={footerRightStyle}>
          {!stepper.isFirst && !submitted ? (
            <button
              type="button"
              onClick={stepper.back}
              style={secondaryButtonStyle}
              data-testid="offboarding-back"
            >
              Back
            </button>
          ) : null}
          {submitted ? (
            <button
              type="button"
              onClick={handleDone}
              style={primaryButtonStyle}
              data-testid="offboarding-done"
            >
              Done
            </button>
          ) : !stepper.isLast ? (
            <button
              type="button"
              onClick={stepper.next}
              disabled={!canAdvance}
              style={primaryButtonStyle}
              data-testid="offboarding-next"
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting}
              style={primaryButtonStyle}
              data-testid="offboarding-submit"
            >
              {submitting ? "Offboarding…" : "Confirm offboard"}
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Confirm target
// ---------------------------------------------------------------------------

interface ConfirmStepProps {
  readonly target: Person;
  readonly assetCount: number;
}

function ConfirmStep({ target, assetCount }: ConfirmStepProps): ReactElement {
  return (
    <section
      aria-labelledby="step-0"
      data-testid="offboarding-confirm-step"
      style={stepSectionStyle}
    >
      <h3 id="step-0" style={stepHeadingStyle}>
        Confirm
      </h3>
      <p style={paragraphStyle}>
        You're about to offboard{" "}
        <strong data-testid="offboarding-target-name">
          {target.display_name}
        </strong>{" "}
        ({target.email}). This is irreversible — the teammate loses access and
        their assets are reassigned according to your picks on the next step.
      </p>
      <ul style={metaListStyle}>
        <li>
          <strong>{assetCount}</strong> owned asset{assetCount === 1 ? "" : "s"}
        </li>
        <li>
          Role: <strong>{target.role}</strong>
        </li>
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Reassign per asset
// ---------------------------------------------------------------------------

interface ReassignStepProps {
  readonly drafts: ReadonlyArray<ReassignmentDraft>;
  readonly nonCascadingAssets: ReadonlyArray<OffboardingAsset>;
  readonly personOptions: ReadonlyArray<Person>;
  readonly onPickOwner: (assetKey: string, owner: UserId | null) => void;
}

function ReassignStep({
  drafts,
  nonCascadingAssets,
  personOptions,
  onPickOwner,
}: ReassignStepProps): ReactElement {
  return (
    <section
      aria-labelledby="step-1"
      data-testid="offboarding-reassign-step"
      style={stepSectionStyle}
    >
      <h3 id="step-1" style={stepHeadingStyle}>
        Reassign assets
      </h3>
      {drafts.length === 0 && nonCascadingAssets.length === 0 ? (
        <p style={paragraphStyle} data-testid="offboarding-no-assets">
          This teammate owns no assets. You can offboard them directly.
        </p>
      ) : null}

      {drafts.length > 0 ? (
        <ul style={assetListStyle} data-testid="offboarding-cascading-list">
          {drafts.map((d) => (
            <li
              key={d.assetKey}
              style={assetRowStyle}
              data-testid="offboarding-asset-row"
              data-asset-kind={d.asset.ref.kind}
            >
              <div style={assetLabelBlockStyle}>
                <div style={assetLabelStyle}>{d.asset.label}</div>
                <div style={assetKindStyle}>{d.asset.ref.kind}</div>
              </div>
              <PersonPicker
                value={d.newOwnerUserId}
                options={personOptions}
                onChange={(owner) => onPickOwner(d.assetKey, owner)}
                ariaLabel={`New owner for ${d.asset.label}`}
                testId={`offboarding-picker-${d.assetKey}`}
              />
            </li>
          ))}
        </ul>
      ) : null}

      {nonCascadingAssets.length > 0 ? (
        <div
          style={nonCascadeBlockStyle}
          data-testid="offboarding-non-cascading-block"
        >
          <div style={nonCascadeHeadingStyle}>
            Not supported in v1 — re-assign manually after offboard
          </div>
          <p style={nonCascadeHintStyle}>
            Atlas only cascades project ownership in v1 (cross-audit §9.8 Q1).
            The assets below stay assigned to the offboarded teammate's
            tombstone until you re-assign them by hand from each asset's
            settings.
          </p>
          <ul
            style={assetListStyle}
            data-testid="offboarding-non-cascading-list"
          >
            {nonCascadingAssets.map((a) => (
              <li
                key={refKey(a.ref)}
                style={nonCascadeRowStyle}
                data-testid="offboarding-non-cascading-row"
                data-asset-kind={a.ref.kind}
              >
                <span style={assetLabelStyle}>{a.label}</span>
                <span style={assetKindStyle}>{a.ref.kind}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

interface PersonPickerProps {
  readonly value: UserId | null;
  readonly options: ReadonlyArray<Person>;
  readonly onChange: (next: UserId | null) => void;
  readonly ariaLabel: string;
  readonly testId: string;
}

function PersonPicker({
  value,
  options,
  onChange,
  ariaLabel,
  testId,
}: PersonPickerProps): ReactElement {
  const [query, setQuery] = useState("");
  const filtered = useMemo<ReadonlyArray<Person>>(() => {
    const q = query.trim().toLowerCase();
    if (q.length === 0) return options;
    return options.filter(
      (p) =>
        p.display_name.toLowerCase().includes(q) ||
        p.email.toLowerCase().includes(q),
    );
  }, [options, query]);

  return (
    <div style={pickerStyle} data-testid={testId}>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search teammates"
        aria-label={`${ariaLabel} — search`}
        style={pickerSearchStyle}
        data-testid={`${testId}-search`}
      />
      <select
        value={value ?? ""}
        onChange={(e) => {
          const next = e.target.value;
          onChange(next.length === 0 ? null : (next as UserId));
        }}
        aria-label={ariaLabel}
        style={pickerSelectStyle}
        data-testid={`${testId}-select`}
      >
        <option value="">— Select new owner —</option>
        {filtered.map((p) => (
          <option key={p.id} value={p.id}>
            {p.display_name} ({p.email})
          </option>
        ))}
      </select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Review
// ---------------------------------------------------------------------------

interface ReviewStepProps {
  readonly target: Person;
  readonly drafts: ReadonlyArray<ReassignmentDraft>;
  readonly nonCascadingAssets: ReadonlyArray<OffboardingAsset>;
  readonly personOptions: ReadonlyArray<Person>;
}

function ReviewStep({
  target,
  drafts,
  nonCascadingAssets,
  personOptions,
}: ReviewStepProps): ReactElement {
  const ownerName = (id: UserId | null): string => {
    if (id === null) return "—";
    const match = personOptions.find((p) => p.id === id);
    return match?.display_name ?? String(id);
  };
  return (
    <section
      aria-labelledby="step-2"
      data-testid="offboarding-review-step"
      style={stepSectionStyle}
    >
      <h3 id="step-2" style={stepHeadingStyle}>
        Review
      </h3>
      <p style={paragraphStyle}>
        Offboarding{" "}
        <strong data-testid="offboarding-review-target">
          {target.display_name}
        </strong>
        . Atlas will revoke access and apply the reassignments below in a single
        transaction.
      </p>

      {drafts.length > 0 ? (
        <div data-testid="offboarding-review-cascading">
          <div style={reviewSectionTitleStyle}>Cascading reassignments</div>
          <ul style={assetListStyle}>
            {drafts.map((d) => (
              <li
                key={d.assetKey}
                style={assetRowStyle}
                data-testid="offboarding-review-row"
              >
                <div style={assetLabelBlockStyle}>
                  <div style={assetLabelStyle}>{d.asset.label}</div>
                  <div style={assetKindStyle}>{d.asset.ref.kind}</div>
                </div>
                <div style={assetOwnerStyle}>
                  → {ownerName(d.newOwnerUserId)}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {nonCascadingAssets.length > 0 ? (
        <div
          style={nonCascadeBlockStyle}
          data-testid="offboarding-review-non-cascading"
        >
          <div style={nonCascadeHeadingStyle}>
            Manual follow-up — re-assign after offboard
          </div>
          <ul style={assetListStyle}>
            {nonCascadingAssets.map((a) => (
              <li
                key={refKey(a.ref)}
                style={nonCascadeRowStyle}
                data-testid="offboarding-review-non-cascading-row"
              >
                <span style={assetLabelStyle}>{a.label}</span>
                <span style={assetKindStyle}>{a.ref.kind}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: 10,
  boxSizing: "border-box",
  width: "100%",
  maxWidth: 640,
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 18px)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const stepperStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border, #232325)",
  borderBottom: "1px solid var(--color-border, #232325)",
  padding: "10px 0",
};

const stepperListStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const stepperItemStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 10px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: isCurrent ? 600 : 400,
  color:
    isCurrent || done
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
  border: `1px solid ${
    isCurrent ? "var(--color-accent, #d97757)" : "var(--color-border, #232325)"
  }`,
  borderRadius: 999,
  background: isCurrent ? "var(--color-bg-elevated, #18181b)" : "transparent",
});

const stepperIndexStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  borderRadius: 999,
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  background:
    isCurrent || done
      ? "var(--color-accent, #d97757)"
      : "var(--color-bg-elevated, #18181b)",
  color:
    isCurrent || done
      ? "var(--color-accent-contrast, #1a0f0a)"
      : "var(--color-text-muted, #b4b4b8)",
});

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: "4px 0",
};

const stepSectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const stepHeadingStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-md, 14px)",
  fontWeight: 600,
};

const paragraphStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  lineHeight: 1.55,
};

const metaListStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.7,
};

const assetListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const assetRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
};

const assetLabelBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  flex: 1,
  minWidth: 0,
};

const assetLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const assetKindStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 500,
  color: "var(--color-text-subtle, #7e7e84)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const assetOwnerStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  flexShrink: 0,
};

const pickerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexShrink: 0,
};

const pickerSearchStyle: CSSProperties = {
  height: 30,
  width: 140,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-xs, 12px)",
};

const pickerSelectStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-xs, 12px)",
};

const nonCascadeBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 10,
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-warning, #d9a857)",
  background: "var(--color-warning-bg, #322615)",
};

const nonCascadeHeadingStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const nonCascadeHintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const nonCascadeRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-bg, #131316)",
};

const reviewSectionTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  marginBottom: 6,
};

const errorStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  background: "var(--color-danger-bg, #321a1a)",
  border: "1px solid var(--color-danger, #d97777)",
  borderRadius: "var(--radius-sm, 6px)",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  paddingTop: 10,
  borderTop: "1px solid var(--color-border, #232325)",
};

const footerRightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const primaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const ghostButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid transparent",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};
