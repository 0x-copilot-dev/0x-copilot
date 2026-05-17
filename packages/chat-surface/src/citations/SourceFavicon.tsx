// Connector / favicon glyph for source rows.
//
// Three-tier fallback chain:
//   1. Real URL → host favicon via Google's s2 service (browser-cached).
//   2. Favicon load fails OR no URL → AppIcon brand glyph (Notion, Drive,
//      Web, …) when the connector slug is in the design system's curated
//      `BRAND_GLYPHS` map.
//   3. Unknown connector → AppIcon's letter-circle fallback.
//
// The `<img>` is `aria-hidden`; the consuming row already labels itself
// with title + connector for assistive tech.

import type { SourceEntry } from "@enterprise-search/api-types";
import { AppIcon } from "@enterprise-search/design-system";
import { useMemo, useState, type ReactElement } from "react";

const FAVICON_BASE = "https://www.google.com/s2/favicons";

export interface SourceFaviconProps {
  source: SourceEntry;
  size?: "sm" | "lg";
  className?: string;
}

export function SourceFavicon({
  source,
  size = "sm",
  className,
}: SourceFaviconProps): ReactElement {
  const host = useMemo(
    () => extractHost(source.source_url),
    [source.source_url],
  );
  const [faviconBroken, setFaviconBroken] = useState(false);

  if (host === null || faviconBroken) {
    return (
      <AppIcon
        name={source.source_connector}
        size={size}
        className={className}
      />
    );
  }

  const px = size === "lg" ? 24 : 16;
  return (
    <span
      className={`ui-app-icon source-favicon${size === "lg" ? " ui-app-icon--lg" : ""}${
        className ? ` ${className}` : ""
      }`}
      aria-hidden="true"
    >
      <img
        src={`${FAVICON_BASE}?domain=${encodeURIComponent(host)}&sz=64`}
        alt=""
        width={px}
        height={px}
        loading="lazy"
        onError={() => setFaviconBroken(true)}
      />
    </span>
  );
}

function extractHost(url: string | null | undefined): string | null {
  if (typeof url !== "string" || url.length === 0) {
    return null;
  }
  try {
    return new URL(url).host || null;
  } catch {
    return null;
  }
}
