import type { AnchorHTMLAttributes, ComponentType, ReactElement } from "react";

import {
  citationIdFromHref,
  isCitationHref,
  isOrdinalCitationHref,
  ordinalFromHref,
} from "./citationHrefs";
import { markdownLinkLabel } from "./markdownLinks";

/**
 * Chip renderers the host binds to the anchor dispatcher (FR-1.3). Kept as
 * an injection point so `chat-surface` never imports the substrate's
 * citation-resolving wrappers (which read the host's `CitationsProvider`);
 * the dispatcher renders whatever chip components the host supplies.
 */
export interface MarkdownLinkChips {
  readonly CitationChip: ComponentType<{ citationId: string }>;
  readonly OrdinalCitationChip: ComponentType<{ conversationOrdinal: number }>;
}

/** True when `href` is an absolute http(s) URL (opens in a new tab). */
export function isExternalHref(href: string | undefined): boolean {
  return Boolean(href && /^https?:\/\//i.test(href));
}

/**
 * Build the markdown anchor dispatcher registered as Streamdown's
 * `components.a`.
 *
 * The citation remark plugin rewrites tokens to citation anchors. Routing
 * through the existing link slot keeps Streamdown's streaming-safe parsing
 * while letting us swap in a chip renderer. Two formats coexist during the
 * rollout: `[c<id>]` chips resolve via `#cite:<id>`, and `[[N]]` chips
 * resolve via `#cite-ord:<n>`.
 *
 * A factory (rather than a bare component) lets the host inject its
 * citation-resolving chip wrappers while this module stays
 * substrate-agnostic and app-import-free.
 */
export function createMarkdownLink({
  CitationChip,
  OrdinalCitationChip,
}: MarkdownLinkChips): (
  props: AnchorHTMLAttributes<HTMLAnchorElement>,
) => ReactElement {
  return function MarkdownLink({
    children,
    href,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
    if (isOrdinalCitationHref(href)) {
      const ordinal = ordinalFromHref(href as string);
      if (ordinal !== null) {
        return <OrdinalCitationChip conversationOrdinal={ordinal} />;
      }
    }
    if (isCitationHref(href)) {
      const id = citationIdFromHref(href as string);
      if (id !== null) {
        return <CitationChip citationId={id} />;
      }
    }
    const external = isExternalHref(href);
    return (
      <a
        {...props}
        href={href}
        rel={external ? "noreferrer" : props.rel}
        target={external ? "_blank" : props.target}
        title={props.title ?? (typeof href === "string" ? href : undefined)}
      >
        {markdownLinkLabel(href, children)}
      </a>
    );
  };
}
