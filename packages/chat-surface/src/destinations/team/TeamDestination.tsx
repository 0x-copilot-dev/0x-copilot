// TeamDestination — P12-B1
//
// Source: team-memory-cmdk-prd.md §7.1. Catalog shell:
//
//   <PageHeader title="Team">
//   <FilterTabs>  (All / Admins / Members / Guests — by role)
//   <search input + sort-by>
//   <CardGrid>  of <PersonCard>
//
// Pure presentation. No transport, no router. The host supplies the
// `people` array (sub-PRD §3.1) and wires invite / open / sort callbacks.
//
// Filter axis: `role`. Sort: `display_name:asc | last_seen:desc |
// joined_at:desc` (sub-PRD §3.1 TeamListSort, narrowed to the three the
// UI surfaces). The search input owns local state — host receives the
// debounced value via `onSearchChange` if it wants to push the search
// through the wire's `filter[q]` axis. Otherwise the destination filters
// in-memory.

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { Person, TeamRole } from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";

import { PersonCard } from "./PersonCard";

// ---------------------------------------------------------------------------
// Filter & sort vocabulary — destination-local, not mirrored in api-types.
// (Wire-axis `role` accepts the bare TeamRole; this slug union is the
// FilterTabs vocabulary which adds an "all" pseudo-slug.)
// ---------------------------------------------------------------------------

export type TeamFilterSlug = "all" | "admins" | "members" | "guests";

export type TeamSortSlug =
  | "display_name:asc"
  | "last_seen:desc"
  | "joined_at:desc";

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

const SORT_ORDER: ReadonlyArray<TeamSortSlug> = [
  "display_name:asc",
  "last_seen:desc",
  "joined_at:desc",
];

const SORT_LABEL: Readonly<Record<TeamSortSlug, string>> = {
  "display_name:asc": "Name",
  "last_seen:desc": "Recently active",
  "joined_at:desc": "Recently joined",
};

export interface TeamFilterCounts {
  readonly all?: number;
  readonly admins?: number;
  readonly members?: number;
  readonly guests?: number;
}

export interface TeamDestinationProps {
  /**
   * Team listing rows. `null` while loading; empty array means no
   * people. Omitting the prop is equivalent to passing an empty array —
   * exercises the empty-state path so the host can mount the destination
   * before the data-binder phase wires the wire fetch.
   */
  readonly people?: ReadonlyArray<Person> | null;
  /** Controlled filter slug. Defaults to "all" internally. */
  readonly filter?: TeamFilterSlug;
  readonly onFilterChange?: (next: TeamFilterSlug) => void;
  /** Optional counts; omitted when host doesn't precompute. */
  readonly counts?: TeamFilterCounts;
  /** Controlled sort slug. Defaults to "display_name:asc" internally. */
  readonly sort?: TeamSortSlug;
  readonly onSortChange?: (next: TeamSortSlug) => void;
  /** Search string. Controlled. Defaults to "" internally. */
  readonly search?: string;
  readonly onSearchChange?: (next: string) => void;
  /** Invite CTA — host wires the modal. */
  readonly onInvite?: () => void;
  /** Person open → host wires the detail route. */
  readonly onOpenPerson?: (person: Person) => void;
  /** Whether the viewer can invite (admin only — sub-PRD §6.1). */
  readonly canInvite?: boolean;
}

export function TeamDestination(
  props: TeamDestinationProps = {},
): ReactElement {
  const {
    people = [],
    filter: controlledFilter,
    onFilterChange,
    counts,
    sort: controlledSort,
    onSortChange,
    search: controlledSearch,
    onSearchChange,
    onInvite,
    onOpenPerson,
    canInvite = true,
  } = props;

  const [internalFilter, setInternalFilter] = useState<TeamFilterSlug>("all");
  const [internalSort, setInternalSort] =
    useState<TeamSortSlug>("display_name:asc");
  const [internalSearch, setInternalSearch] = useState("");

  const filter = controlledFilter ?? internalFilter;
  const sort = controlledSort ?? internalSort;
  const search = controlledSearch ?? internalSearch;

  const handleFilterChange = (next: TeamFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
    else setInternalFilter(next);
  };

  const handleSortChange = (next: TeamSortSlug): void => {
    if (onSortChange !== undefined) onSortChange(next);
    else setInternalSort(next);
  };

  const handleSearchChange = (next: string): void => {
    if (onSearchChange !== undefined) onSearchChange(next);
    else setInternalSearch(next);
  };

  const filterOptions: ReadonlyArray<FilterTabOption<TeamFilterSlug>> =
    FILTER_ORDER.map((slug) => ({
      slug,
      label: FILTER_LABEL[slug],
      count: counts?.[slug],
    }));

  const visible = useMemo<ReadonlyArray<Person> | null>(() => {
    if (people === null) return null;
    const byRole = applyRoleFilter(people, filter);
    const bySearch = applySearch(byRole, search);
    return applySort(bySearch, sort);
  }, [people, filter, search, sort]);

  return (
    <section
      data-component="team-destination"
      data-testid="team-destination"
      aria-label="Team destination"
      style={containerStyle}
    >
      <div style={headerWrapStyle}>
        <PageHeader
          title="Team"
          subtitle="Workspace members, roles, and presence."
          primaryAction={
            canInvite && onInvite !== undefined
              ? { label: "Invite", onClick: onInvite }
              : undefined
          }
        />
      </div>

      <div style={filterRowStyle}>
        <FilterTabs<TeamFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Team filter (role)"
          idPrefix="team-filter"
        />
      </div>

      <div style={toolbarStyle} data-testid="team-toolbar">
        <input
          type="search"
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Search by name or email"
          style={searchInputStyle}
          data-testid="team-search"
          aria-label="Search team"
        />
        <label style={sortLabelStyle}>
          <span style={sortLabelTextStyle}>Sort</span>
          <select
            value={sort}
            onChange={(e) => handleSortChange(e.target.value as TeamSortSlug)}
            style={sortSelectStyle}
            data-testid="team-sort"
            aria-label="Sort team"
          >
            {SORT_ORDER.map((slug) => (
              <option key={slug} value={slug}>
                {SORT_LABEL[slug]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div style={bodyStyle} data-testid="team-body">
        {visible === null ? (
          <div data-testid="team-loading" style={loadingStyle}>
            Loading team…
          </div>
        ) : visible.length === 0 ? (
          <EmptyState
            title={search.trim() === "" ? "No teammates yet" : "No matches"}
            body={
              search.trim() === ""
                ? "Invite teammates to collaborate in this workspace."
                : "Try a different search or filter."
            }
            action={
              canInvite && onInvite !== undefined && search.trim() === ""
                ? { label: "Invite teammate", onClick: onInvite }
                : undefined
            }
          />
        ) : (
          <CardGrid minCardWidth={260} gap={12} ariaLabel="Team members">
            {visible.map((person) => (
              <PersonCard
                key={person.id}
                person={person}
                onOpen={onOpenPerson}
              />
            ))}
          </CardGrid>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Pure transforms (exported for tests)
// ---------------------------------------------------------------------------

const ROLE_BUCKETS: Readonly<Record<TeamFilterSlug, ReadonlyArray<TeamRole>>> =
  {
    all: ["owner", "admin", "member", "guest"],
    // Sub-PRD §7.1 reads "Admins" as the conceptual bucket that includes
    // owner + admin (an owner is an admin with the demote-protected flag).
    admins: ["owner", "admin"],
    members: ["member"],
    guests: ["guest"],
  };

export function applyRoleFilter(
  people: ReadonlyArray<Person>,
  filter: TeamFilterSlug,
): ReadonlyArray<Person> {
  const allowed = ROLE_BUCKETS[filter];
  if (filter === "all") return people;
  return people.filter((p) => allowed.includes(p.role));
}

export function applySearch(
  people: ReadonlyArray<Person>,
  search: string,
): ReadonlyArray<Person> {
  const q = search.trim().toLowerCase();
  if (q.length === 0) return people;
  return people.filter(
    (p) =>
      p.display_name.toLowerCase().includes(q) ||
      p.email.toLowerCase().includes(q),
  );
}

export function applySort(
  people: ReadonlyArray<Person>,
  sort: TeamSortSlug,
): ReadonlyArray<Person> {
  const copy = [...people];
  switch (sort) {
    case "display_name:asc":
      copy.sort((a, b) =>
        a.display_name.localeCompare(b.display_name, undefined, {
          sensitivity: "base",
        }),
      );
      break;
    case "last_seen:desc":
      copy.sort((a, b) => {
        // null last_seen_at sinks to the bottom.
        const ta =
          a.last_seen_at === null ? -Infinity : Date.parse(a.last_seen_at);
        const tb =
          b.last_seen_at === null ? -Infinity : Date.parse(b.last_seen_at);
        return tb - ta;
      });
      break;
    case "joined_at:desc":
      copy.sort((a, b) => Date.parse(b.joined_at) - Date.parse(a.joined_at));
      break;
  }
  return copy;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: "100%",
  height: "100%",
  minHeight: 0,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  boxSizing: "border-box",
};

const headerWrapStyle: CSSProperties = {
  padding: "16px 20px 0",
};

const filterRowStyle: CSSProperties = {
  padding: "12px 20px 0",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "12px 20px",
};

const searchInputStyle: CSSProperties = {
  flex: 1,
  height: 32,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  outline: "none",
  minWidth: 0,
};

const sortLabelStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  flexShrink: 0,
};

const sortLabelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const sortSelectStyle: CSSProperties = {
  height: 32,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
  padding: "0 20px 20px",
};

const loadingStyle: CSSProperties = {
  padding: 24,
  textAlign: "center",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-sm, 13px)",
};
