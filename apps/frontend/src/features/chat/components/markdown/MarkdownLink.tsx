import type { AnchorHTMLAttributes, ReactElement } from "react";
import { markdownLinkLabel } from "../../markdownLinks";
import {
  CitationChip,
  citationIdFromHref,
  isCitationHref,
} from "../citations/CitationChip";

export function MarkdownLink({
  children,
  href,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
  // The citation remark plugin rewrites [c<id>] tokens to `#cite:<id>`
  // anchors. Routing through the existing link slot keeps Streamdown's
  // streaming-safe parsing while letting us swap in a chip renderer.
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
