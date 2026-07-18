// Skills — destination shell (PR-4.9).
//
// Pure-presentation card catalog of SAVED MULTI-STEP WORKFLOWS per
// DESIGN-SPEC §3 (Skills) + phase-4 PRD FR-4.26 / FR-4.27 / FR-4.28 /
// FR-4.29:
//
//   1. PageHeader (cross-audit §1.6 shape) — title "Skills", the
//      DESIGN-SPEC §3 subtitle copy, and a "New skill" primary action.
//   2. CardGrid body — one `SkillCard` per skill: name, description sub,
//      `N runs` badge, mono relative time, and Run / Edit actions.
//
// This is the redesigned Skills slug: a card grid of `/v1/skills`
// workflows, NOT the MCP tool-integration catalog (`tools/` destination)
// which the PRD §11 supersedes for this slug. The old connectors/tools
// destinations are untouched by this PR.
//
// 4-state machine (FR-4.2), driven by a `SectionResult<SkillSummary[]>
// | null` prop — mirrors ProjectsDestination / ConnectorsDestination:
//   - `null`                    → loading skeleton (`data-state="loading"`)
//   - `status === "error"`      → EmptyState + Retry  (`data-state="error"`)
//   - `status === "unavailable"`→ "not enabled" empty  (unavailable)
//   - `status === "ok"`, 0 rows → "No skills yet" + New skill   (ready)
//   - `status === "ok"`, rows   → SkillCard grid               (ready)
//
// Hard correctness rules (same as sibling destinations):
//   - Shared primitives only (PageHeader / CardGrid / EmptyState). No
//     bespoke buttons or hardcoded px outside tokens.
//   - Pure presentation: no fetch, no router calls, no SSE. Run / Edit /
//     New flow through host callbacks; the host (PR-4.10 binder) wires
//     `onRunSkill → start run + navigate(run)`, `onEditSkill`/`onNewSkill
//     → open the editor route.
//   - Framework-agnostic: props/callbacks only, so both apps/frontend and
//     apps/desktop render one copy.

import type { CSSProperties, ReactElement } from "react";

import type {
  SectionResult,
  SkillId,
  SkillSummary,
} from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";

import { SkillCard } from "./SkillCard";

// ===========================================================================
// Copy (DESIGN-SPEC §3 — exported so host + tests share one string)
// ===========================================================================

/** Subtitle rendered under the "Skills" title (FR-4.29). */
export const SKILLS_SUBTITLE_COPY =
  "Saved multi-step workflows you can re-run in one click — their own place, not a settings tab.";

/** Per-view empty copy (DESIGN-SPEC §9 UI checklist). */
export const SKILLS_EMPTY_TITLE = "No skills yet";
const SKILLS_EMPTY_BODY =
  "Save a multi-step workflow once, then re-run it in one click. Create your first skill to get started.";

// ===========================================================================
// Public props
// ===========================================================================

export interface SkillsDestinationProps {
  /**
   * Server-projected skill list. `null` = loading skeleton; `error`
   * shows the error empty-state with Retry; `unavailable` shows the
   * "not enabled" empty-state; `ok` renders the grid (or the per-view
   * empty copy when there are zero rows).
   */
  readonly items?: SectionResult<ReadonlyArray<SkillSummary>> | null;

  /** "Run" on a card — host starts a run + navigates to Run (FR-4.27). */
  readonly onRunSkill?: (id: SkillId) => void;
  /** "Edit" on a card — host opens the skill editor route (FR-4.27). */
  readonly onEditSkill?: (id: SkillId) => void;
  /** "New skill" in the header — host opens the new-skill editor (FR-4.28). */
  readonly onNewSkill?: () => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Shared styles
// ===========================================================================

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  backgroundColor: "var(--color-bg)",
  color: "var(--color-text)",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
};

// `.pg` surface — content column max-width 960 (DESIGN-SPEC §0 / FR-4.1).
const containerStyle: CSSProperties = {
  width: "100%",
  maxWidth: 960,
  margin: "0 auto",
  padding: "24px 28px 48px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

// ===========================================================================
// Top-level shell
// ===========================================================================

export function SkillsDestination(
  props: SkillsDestinationProps = {},
): ReactElement {
  const {
    items = null,
    onRunSkill,
    onEditSkill,
    onNewSkill,
    onRetry,
    now,
  } = props;

  const newSkillAction =
    onNewSkill !== undefined
      ? { label: "New skill", onClick: onNewSkill }
      : undefined;

  // === Loading state ====================================================
  if (items === null) {
    return (
      <section
        aria-label="Skills destination"
        data-testid="skills-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Skills" subtitle={SKILLS_SUBTITLE_COPY} />
          <CardGrid ariaLabel="Skills loading skeleton">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} index={i} />
            ))}
          </CardGrid>
        </div>
      </section>
    );
  }

  // === Error state ======================================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Skills destination"
        data-testid="skills-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Skills" subtitle={SKILLS_SUBTITLE_COPY} />
          <div role="alert">
            <EmptyState
              title="Could not load skills"
              body={items.error ?? "Network error — try again."}
              action={
                onRetry !== undefined
                  ? { label: "Retry", onClick: onRetry }
                  : undefined
              }
            />
          </div>
        </div>
      </section>
    );
  }

  // === Unavailable state ================================================
  if (items.status === "unavailable") {
    return (
      <section
        aria-label="Skills destination"
        data-testid="skills-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Skills" subtitle={SKILLS_SUBTITLE_COPY} />
          <EmptyState
            title="Skills unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Ready state ======================================================
  const rows = items.data ?? [];
  const nowMs = now ?? Date.now();

  return (
    <section
      aria-label="Skills destination"
      data-testid="skills-destination"
      data-state="ready"
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Skills"
          subtitle={SKILLS_SUBTITLE_COPY}
          primaryAction={newSkillAction}
        />

        {rows.length === 0 ? (
          <EmptyState
            title={SKILLS_EMPTY_TITLE}
            body={SKILLS_EMPTY_BODY}
            action={newSkillAction}
          />
        ) : (
          <CardGrid ariaLabel="Skills">
            {rows.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                onRun={onRunSkill}
                onEdit={onEditSkill}
                now={nowMs}
              />
            ))}
          </CardGrid>
        )}
      </div>
    </section>
  );
}

// ===========================================================================
// CardSkeleton — loading placeholder
// ===========================================================================

function CardSkeleton({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 128,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div
      style={style}
      data-testid="skills-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    />
  );
}
