// PersonCard — one row in the Team catalog grid.
//
// Source: team-memory-cmdk-prd.md §7.1 (CardGrid of PersonCards). Pure
// presentation; no transport, no router. Click bubbles via `onOpen(person)`
// so the host wires the route — the destination shell never calls
// `router.navigate` directly (cross-audit §1.1).
//
// Fields rendered (sub-PRD §3.1 Person):
//   - avatar (img if avatar_url, else initials tile)
//   - display_name (+ "(you)" suffix when `is_self`)
//   - role chip (TeamRole)
//   - presence dot (Presence)
//   - agents_count + projects_count
//
// SP-1: no new chrome — the card is a plain button-tile mirroring
// AgentCard / ProjectCard density (cross-audit §1.6).

import type { CSSProperties, ReactElement } from "react";

import type { Person, Presence, TeamRole } from "@0x-copilot/api-types";

const ROLE_LABEL: Readonly<Record<TeamRole, string>> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

const PRESENCE_LABEL: Readonly<Record<Presence, string>> = {
  active: "Active",
  away: "Away",
  in_meeting: "In meeting",
  offline: "Offline",
};

const PRESENCE_COLOR: Readonly<Record<Presence, string>> = {
  active: "var(--color-success, #22c55e)",
  away: "var(--color-warning, #d9a857)",
  in_meeting: "var(--color-accent, #d97757)",
  offline: "var(--color-text-subtle, #7e7e84)",
};

export interface PersonCardProps {
  readonly person: Person;
  readonly onOpen?: (person: Person) => void;
}

export function PersonCard({ person, onOpen }: PersonCardProps): ReactElement {
  const handleClick = (): void => {
    onOpen?.(person);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      style={cardStyle}
      data-testid="person-card"
      data-person-id={person.id}
      data-person-role={person.role}
      data-person-presence={person.presence}
      aria-label={`Open ${person.display_name}`}
    >
      <div style={topRowStyle}>
        <AvatarTile person={person} />
        <div style={namesBlockStyle}>
          <div style={nameRowStyle}>
            <span style={nameStyle} data-testid="person-card-name">
              {person.display_name}
              {person.is_self ? (
                <span style={selfHintStyle} aria-label="you">
                  {" "}
                  (you)
                </span>
              ) : null}
            </span>
          </div>
          <span style={emailStyle} data-testid="person-card-email">
            {person.email}
          </span>
        </div>
      </div>
      <div style={chipRowStyle}>
        <RoleChip role={person.role} />
        <PresenceDot presence={person.presence} />
      </div>
      <div style={countsRowStyle} data-testid="person-card-counts">
        <span data-testid="person-card-agents-count">
          {person.agents_count} agent{person.agents_count === 1 ? "" : "s"}
        </span>
        <span aria-hidden="true" style={dotSepStyle}>
          ·
        </span>
        <span data-testid="person-card-projects-count">
          {person.projects_count} project
          {person.projects_count === 1 ? "" : "s"}
        </span>
      </div>
    </button>
  );
}

interface AvatarTileProps {
  readonly person: Person;
}

function AvatarTile({ person }: AvatarTileProps): ReactElement {
  if (person.avatar_url !== undefined && person.avatar_url.length > 0) {
    return (
      <img
        src={person.avatar_url}
        alt=""
        style={avatarImgStyle}
        data-testid="person-card-avatar-img"
      />
    );
  }
  return (
    <div
      style={avatarInitialsStyle}
      data-testid="person-card-avatar-initials"
      aria-hidden="true"
    >
      {initialsOf(person.display_name)}
    </div>
  );
}

interface RoleChipProps {
  readonly role: TeamRole;
}

function RoleChip({ role }: RoleChipProps): ReactElement {
  return (
    <span
      style={roleChipStyle(role)}
      data-testid={`person-card-role-${role}`}
      aria-label={`Role: ${ROLE_LABEL[role]}`}
    >
      {ROLE_LABEL[role]}
    </span>
  );
}

interface PresenceDotProps {
  readonly presence: Presence;
}

function PresenceDot({ presence }: PresenceDotProps): ReactElement {
  return (
    <span
      style={presenceWrapStyle}
      data-testid={`person-card-presence-${presence}`}
    >
      <span
        aria-hidden="true"
        style={{ ...presenceDotStyle, background: PRESENCE_COLOR[presence] }}
      />
      <span style={presenceLabelStyle}>{PRESENCE_LABEL[presence]}</span>
    </span>
  );
}

function initialsOf(name: string): string {
  const cleaned = name.trim();
  if (cleaned.length === 0) return "?";
  const parts = cleaned.split(/\s+/);
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

// === Styles ============================================================

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 12,
  borderRadius: "var(--radius-md, 10px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  textAlign: "left",
  cursor: "pointer",
  fontFamily: "inherit",
  font: "inherit",
  width: "100%",
  boxSizing: "border-box",
};

const topRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const avatarImgStyle: CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: "var(--radius-full, 999px)",
  objectFit: "cover",
  flexShrink: 0,
};

const avatarInitialsStyle: CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: "var(--radius-full, 999px)",
  background: "var(--color-border-strong, #2a2a2c)",
  color: "var(--color-text, #ededee)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
  flexShrink: 0,
};

const namesBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  minWidth: 0,
  flex: 1,
};

const nameRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const nameStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: 0,
};

const selfHintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 400,
  color: "var(--color-text-subtle, #7e7e84)",
};

const emailStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const chipRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
};

function roleChipStyle(role: TeamRole): CSSProperties {
  const base: CSSProperties = {
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: "var(--radius-full, 999px)",
    border: "1px solid var(--color-border, #232325)",
    color: "var(--color-text, #ededee)",
    background: "var(--color-surface-muted, #222224)",
  };
  if (role === "owner") {
    return {
      ...base,
      color: "var(--color-accent, #d97757)",
      borderColor: "var(--color-accent, #d97757)",
    };
  }
  if (role === "admin") {
    return {
      ...base,
      color: "var(--color-success, #22c55e)",
      borderColor: "var(--color-success, #22c55e)",
    };
  }
  if (role === "guest") {
    return {
      ...base,
      color: "var(--color-warning, #d9a857)",
      borderColor: "var(--color-warning, #d9a857)",
    };
  }
  return base;
}

const presenceWrapStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const presenceDotStyle: CSSProperties = {
  width: 8,
  height: 8,
  borderRadius: "var(--radius-full, 999px)",
  display: "inline-block",
};

const presenceLabelStyle: CSSProperties = {
  fontWeight: 500,
};

const countsRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const dotSepStyle: CSSProperties = {
  color: "var(--color-text-subtle, #7e7e84)",
};
