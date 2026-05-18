// TeamPanel — left rail for the Team destination.
//
// Source: team-memory-cmdk-prd.md §7.1. Carries:
//   1. Role filter chips (mirrors the destination FilterTabs vocabulary)
//   2. Presence filter chips (active / away / in_meeting / offline)
//   3. Search input (panel-level — host can decide to push into wire q)
//   4. Invite CTA — primary action; mirrors destination header
//
// Pure presentation; no fetch, no router.

import { type CSSProperties, type ReactElement } from "react";

import type { Presence } from "@enterprise-search/api-types";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";

import type { TeamFilterCounts, TeamFilterSlug } from "./TeamDestination";

const FILTER_ORDER: ReadonlyArray<TeamFilterSlug> = [
  "all",
  "admins",
  "members",
  "guests",
];

const FILTER_LABEL: Readonly<Record<TeamFilterSlug, string>> = {
  all: "All",
  admins: "Admins",
  members: "Members",
  guests: "Guests",
};

export type PresenceFilterSlug = "any" | Presence;

export interface PresenceFilterCounts {
  readonly any?: number;
  readonly active?: number;
  readonly away?: number;
  readonly in_meeting?: number;
  readonly offline?: number;
}

const PRESENCE_ORDER: ReadonlyArray<PresenceFilterSlug> = [
  "any",
  "active",
  "away",
  "in_meeting",
  "offline",
];

const PRESENCE_LABEL: Readonly<Record<PresenceFilterSlug, string>> = {
  any: "Any",
  active: "Active",
  away: "Away",
  in_meeting: "In meeting",
  offline: "Offline",
};

export interface TeamPanelProps {
  /** Role filter — drives the destination CardGrid. */
  readonly roleFilter?: TeamFilterSlug;
  readonly onRoleFilterChange?: (next: TeamFilterSlug) => void;
  readonly roleCounts?: TeamFilterCounts;

  /** Presence filter — host applies before passing `people` to destination. */
  readonly presenceFilter?: PresenceFilterSlug;
  readonly onPresenceFilterChange?: (next: PresenceFilterSlug) => void;
  readonly presenceCounts?: PresenceFilterCounts;

  /** Panel search; host decides whether to mirror destination search. */
  readonly search?: string;
  readonly onSearchChange?: (next: string) => void;

  /** Invite CTA — admin-only on the wire side (sub-PRD §6.1). */
  readonly canInvite?: boolean;
  readonly onInvite?: () => void;
}

export function TeamPanel({
  roleFilter = "all",
  onRoleFilterChange,
  roleCounts,
  presenceFilter = "any",
  onPresenceFilterChange,
  presenceCounts,
  search = "",
  onSearchChange,
  canInvite = true,
  onInvite,
}: TeamPanelProps): ReactElement {
  const roleOptions: ReadonlyArray<FilterTabOption<TeamFilterSlug>> =
    FILTER_ORDER.map((slug) => ({
      slug,
      label: FILTER_LABEL[slug],
      count: roleCounts?.[slug],
    }));

  const presenceOptions: ReadonlyArray<FilterTabOption<PresenceFilterSlug>> =
    PRESENCE_ORDER.map((slug) => ({
      slug,
      label: PRESENCE_LABEL[slug],
      count: presenceCounts?.[slug],
    }));

  return (
    <ContextPanel
      title="Team"
      destination="team"
      search={
        onSearchChange !== undefined
          ? {
              value: search,
              onChange: onSearchChange,
              placeholder: "Search teammates",
            }
          : undefined
      }
      primaryAction={
        canInvite && onInvite !== undefined
          ? { label: "Invite teammate", onClick: onInvite }
          : undefined
      }
    >
      <div data-testid="team-panel" style={bodyStyle}>
        <section style={sectionStyle} aria-labelledby="team-panel-role-heading">
          <div id="team-panel-role-heading" style={sectionTitleStyle}>
            Role
          </div>
          <FilterTabs<TeamFilterSlug>
            value={roleFilter}
            onChange={(next) => onRoleFilterChange?.(next)}
            options={roleOptions}
            ariaLabel="Team filter — role (panel)"
            idPrefix="team-panel-role"
          />
        </section>
        <section
          style={sectionStyle}
          aria-labelledby="team-panel-presence-heading"
        >
          <div id="team-panel-presence-heading" style={sectionTitleStyle}>
            Presence
          </div>
          <FilterTabs<PresenceFilterSlug>
            value={presenceFilter}
            onChange={(next) => onPresenceFilterChange?.(next)}
            options={presenceOptions}
            ariaLabel="Team filter — presence (panel)"
            idPrefix="team-panel-presence"
          />
        </section>
      </div>
    </ContextPanel>
  );
}

// === Styles ============================================================

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: "8px 12px",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "8px 0",
};

const sectionTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.6,
  color: "var(--color-text-subtle, #7e7e84)",
};
