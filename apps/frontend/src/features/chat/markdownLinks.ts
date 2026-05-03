import type { ReactNode } from "react";

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
