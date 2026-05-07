// PR 3.7.1 — single shimmer row shown while a source-producing tool is
// in flight and the Sources tab has no real rows yet.
//
// Mirrors the visual footprint of `<SourceRow />` — same Card chrome,
// same chip slot — so when the first real row arrives it slides into the
// same position without a layout flash.
//
// One row max, regardless of how many tool calls are running. The label
// is the only thing that varies (driven by the parent).

import { Badge, Card } from "@enterprise-search/design-system";
import type { ReactElement } from "react";

export interface SourceSkeletonRowProps {
  label: string;
}

export function SourceSkeletonRow({
  label,
}: SourceSkeletonRowProps): ReactElement {
  return (
    <ul
      className="atlas-workspace-tab__list"
      aria-live="polite"
      aria-busy="true"
    >
      <li className="atlas-source-row atlas-source-row--skeleton">
        <Card tone="default">
          <div className="atlas-source-row__head">
            <Badge tone="neutral">
              <span
                className="atlas-source-row__pulse-dot"
                aria-hidden="true"
              />
            </Badge>
            <span
              className="ui-app-icon source-favicon atlas-source-row__glyph"
              aria-hidden="true"
            />
            <span className="atlas-source-row__title atlas-source-row__title--skeleton">
              {label}
            </span>
          </div>
          <p
            className="atlas-source-row__snippet atlas-source-row__snippet--skeleton"
            aria-hidden="true"
          />
        </Card>
      </li>
    </ul>
  );
}
