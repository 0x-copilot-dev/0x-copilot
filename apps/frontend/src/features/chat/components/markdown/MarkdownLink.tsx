import type { AnchorHTMLAttributes, ReactElement } from "react";
import { markdownLinkLabel } from "../../markdownLinks";

export function MarkdownLink({
  children,
  href,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
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
