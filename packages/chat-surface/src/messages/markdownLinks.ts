import type { ReactNode } from "react";

// Pick the label to render inside a markdown anchor.
//
// Streamdown hands us the raw children + href for every `<a>` it
// renders. When the visible text IS the URL (e.g. autolinks like
// `<https://example.com/path>` or bare URLs the parser interpreted as
// links), the bare string is visually noisy — long URLs wrap awkwardly
// inside chat bubbles. Substitute a compact "host + path, middle-elided"
// label so the link reads as a destination rather than a raw blob.
//
// Descriptive labels (`[See the docs](https://…)`) pass through
// untouched. Anything that isn't a parseable URL falls back to the
// original children.
export function markdownLinkLabel(
  href: string | undefined,
  children: ReactNode,
): ReactNode {
  const text = plainTextFromReactNode(children)?.trim();
  if (!href || !text || !isRawUrlLabel(text, href)) {
    return children;
  }
  return compactUrlLabel(href) ?? children;
}

function isRawUrlLabel(text: string, href: string): boolean {
  return text === href || normalizeUrlText(text) === normalizeUrlText(href);
}

function normalizeUrlText(value: string): string {
  return value.trim().replace(/\/$/, "");
}

function compactUrlLabel(href: string): string | null {
  try {
    const url = new URL(href);
    const path = url.pathname === "/" ? "" : url.pathname.replace(/\/$/, "");
    const label = `${url.hostname}${path}`;
    return truncateMiddle(label || url.hostname, 72);
  } catch {
    return null;
  }
}

function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const prefixLength = Math.ceil((maxLength - 3) / 2);
  const suffixLength = Math.floor((maxLength - 3) / 2);
  return `${value.slice(0, prefixLength)}...${value.slice(-suffixLength)}`;
}

function plainTextFromReactNode(node: ReactNode): string | null {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    const parts = node.map(plainTextFromReactNode);
    return parts.every((part) => part !== null) ? parts.join("") : null;
  }
  return null;
}
