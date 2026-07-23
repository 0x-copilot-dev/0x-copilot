// <ItemLink label={…} ref={ref} /> — the ONLY way a destination renders a
// cross-destination link.
//
// Source: cross-audit.md §1.1 + §3.3; reshaped by PRD-04 (Seams A/B + G11).
// Direct `router.navigate(…)` calls from destination cards are still forbidden.
//
// Two facts, two owners (PRD-04):
//   * DISPLAY TEXT is the caller's — `label` is a REQUIRED `ReactNode`. The
//     caller renders the entity's real name where it has it (it almost always
//     does), or `itemKindNoun(ref.kind)` where it holds only an id. There is no
//     `deletedLabel`: a caller that knows an entity is gone passes that phrasing
//     in `label`.
//   * THE ROUTE is the host's — looked up SYNCHRONOUSLY from the route registry
//     (`resolveItemRoute`), which the host populates from its own route union.
//
// Rendering:
//   * kind has a registered route AND it resolves non-null → an `<a>` whose
//     click calls `router.navigate(route)` (modified clicks fall through to the
//     browser so users can open in a new tab);
//   * otherwise → a plain, non-interactive `<span>` carrying `label`. "not
//     navigable yet" and "deleted" are the same inert-text presentation now;
//     the old code conflated both into a "deleted …" chip.
//
// ACCENT-LINK POLICY (README G11, owned here): an `ItemRef` link declares NO
// colour and NO font-size. It inherits `color` / `font-size` / `font-weight`
// from whatever slot it sits in — a `Row` title, a card name, a table cell.
// Accent is reserved for the affordances the design spends it on (status chips,
// live/streaming indicators, active nav) and is never applied to an entity
// name. No `.ui-link` recipe is minted — inheritance is the recipe.

import {
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ItemRef } from "@0x-copilot/api-types";

import { useRouter } from "../providers/RouterProvider";
import type { ArtifactRoute } from "../routing/router";

import { hasItemRoute, resolveItemRoute } from "./registry";

export interface ItemLinkProps {
  readonly ref: ItemRef;
  /**
   * What the link renders — the entity's real display name where the caller
   * has it, or `itemKindNoun(ref.kind)` where it holds only an id. Required on
   * purpose (PRD-04 Seam A): an optional prop leaves the old constant-label
   * defect one careless call site away; required makes the compiler enumerate
   * every call site so each states, in a reviewable diff, what it renders.
   */
  readonly label: ReactNode;
  /** Optional className for host-level styling overrides. */
  readonly className?: string;
}

// No `color`, no `fontSize` (README G11) — the link inherits its slot's
// typography. Only layout + affordance chrome.
const linkStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  textDecoration: "none",
  cursor: "pointer",
};

// Inert text — a kind with no registered route (or a route that resolved to
// null). Same "no colour / no font-size, inherit the slot" discipline.
const staticStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};

export function ItemLink({
  ref,
  label,
  className,
}: ItemLinkProps): ReactElement {
  const router = useRouter<ArtifactRoute>();

  // Pure function of (kind, id) — no effect, no loading state, no promise.
  const route = hasItemRoute(ref.kind) ? resolveItemRoute(ref) : null;

  if (route === null || route === undefined) {
    return (
      <span
        className={className}
        style={staticStyle}
        data-testid="item-link-static"
        data-item-kind={ref.kind}
      >
        {label}
      </span>
    );
  }

  const handleClick = (event: ReactMouseEvent<HTMLAnchorElement>): void => {
    // Plain clicks navigate via the router (substrate-agnostic). Modified
    // clicks (cmd/ctrl/shift/middle) fall through to the browser's default so
    // users can open in a new tab/window.
    if (
      event.defaultPrevented ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    event.preventDefault();
    router.navigate(route as ArtifactRoute);
  };

  return (
    <a
      href="#"
      className={className}
      style={linkStyle}
      onClick={handleClick}
      data-testid="item-link"
      data-item-kind={ref.kind}
      data-item-id={ref.id}
    >
      {label}
    </a>
  );
}
