// <FavoriteTools> — P2-B3 home section.
//
// Pure presentation: renders favorite-tool cards in a <CardGrid>. Each
// card wraps an <ItemLink ref={{kind: "tool", id: skill_id}} /> so the
// cross-destination jump goes through the registry (cross-audit §1.1 +
// §3.3). Use-count + last-used-at are shown as muted meta.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4.2 +
// cross-audit.md §1.1.
//
// TODO(merge): _home-stub.ts is local; repoint to api-types when P2-A1
// merges (see _home-stub.ts header). The branded id type for the tool
// ref is currently `SkillId`; when the tool/skill consolidation lands
// (master PRD §4.3), pass it through unchanged.

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { CardGrid } from "../../../shell/CardGrid";
import { EmptyState } from "../../../shell/EmptyState";
import { ItemLink } from "../../../refs/ItemLink";
import { formatRelativeTime } from "../../../util/time";

import type { HomeFavoriteTool } from "../_home-stub";

export interface FavoriteToolsProps {
  readonly tools: SectionResult<HomeFavoriteTool[]>;
  /** Optional reference instant for relative time (test seam). */
  readonly now?: number;
}

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: 12,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
  minHeight: 80,
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaStyle: CSSProperties = {
  marginTop: "auto",
  display: "flex",
  gap: 10,
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

function pluralizeUses(count: number): string {
  return count === 1 ? "1 use" : `${count} uses`;
}

export function FavoriteTools({
  tools,
  now,
}: FavoriteToolsProps): ReactElement {
  if (tools.status === "error") {
    return (
      <div
        role="alert"
        data-testid="home-favorite-tools-error"
        data-section-status="error"
      >
        <EmptyState
          title="Couldn't load favorite tools"
          body={tools.error ?? "Try again in a moment."}
        />
      </div>
    );
  }

  if (tools.status === "unavailable") {
    return (
      <div
        data-testid="home-favorite-tools-unavailable"
        data-section-status="unavailable"
      >
        <EmptyState
          title="Favorite tools unavailable"
          body={tools.error ?? "This section is temporarily unavailable."}
        />
      </div>
    );
  }

  const items = tools.data ?? [];
  if (items.length === 0) {
    return (
      <div data-testid="home-favorite-tools-empty" data-section-status="ok">
        <EmptyState title="Use a tool to see it here." />
      </div>
    );
  }

  return (
    <div data-testid="home-favorite-tools" data-section-status="ok">
      <CardGrid ariaLabel="Favorite tools" minCardWidth={220}>
        {items.map((tool) => (
          <div
            key={tool.skill_id}
            style={cardStyle}
            data-testid="home-favorite-tool-card"
            data-skill-id={tool.skill_id}
          >
            <div style={headerStyle}>
              <ItemLink ref={{ kind: "skill", id: tool.skill_id }} />
              <span data-testid="home-favorite-tool-name">{tool.name}</span>
            </div>
            {tool.subtitle !== undefined && tool.subtitle.length > 0 ? (
              <div style={subtitleStyle}>{tool.subtitle}</div>
            ) : null}
            <div style={metaStyle}>
              <span data-testid="home-favorite-tool-use-count">
                {pluralizeUses(tool.use_count)}
              </span>
              {tool.last_used_at !== undefined ? (
                <span data-testid="home-favorite-tool-last-used">
                  last used {formatRelativeTime(tool.last_used_at, now)}
                </span>
              ) : null}
            </div>
          </div>
        ))}
      </CardGrid>
    </div>
  );
}
