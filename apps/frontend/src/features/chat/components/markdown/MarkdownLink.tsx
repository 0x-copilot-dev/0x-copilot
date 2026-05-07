import type { AnchorHTMLAttributes, ReactElement } from "react";
import { markdownLinkLabel } from "../../markdownLinks";
import {
  CitationChip,
  citationIdFromHref,
  isCitationHref,
} from "../citations/CitationChip";
import {
  OrdinalCitationChip,
  isOrdinalCitationHref,
  ordinalFromHref,
} from "../citations/OrdinalCitationChip";

export function MarkdownLink({
  children,
  href,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
  // The citation remark plugin rewrites tokens to citation anchors.
  // Routing through the existing link slot keeps Streamdown's
  // streaming-safe parsing while letting us swap in a chip renderer.
  // Two formats coexist during the rollout: PR 1.1 `[c<id>]` chips
  // resolve via `#cite:<id>`, and PR 1.1-rev2 `[[N]]` chips resolve
  // via `#cite-ord:<n>`.
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
}

export function isExternalHref(href: string | undefined): boolean {
  return Boolean(href && /^https?:\/\//i.test(href));
}
