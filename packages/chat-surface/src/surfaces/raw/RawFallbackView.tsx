// Raw fallback view (Generative Surfaces v2, PRD-B2 D5 / FR-D3 / NFR-2).
//
// The honest floor: when no view fits a surface, show the raw result verbatim —
// "Nothing is hidden." One memoized `JSON.stringify` into a single `<pre>` text
// node (no per-line elements, no highlighting) is the >40 KB no-jank mechanism;
// `contain: content` + `overflow: auto` keep a hostile blob scrollable inside
// its own box. Every display cap is LABELED — Copy/Download always carry the
// full serialized text. Lives beside `GenericStructuredDiff` in chat-surface,
// NOT in surface-renderers: surface-renderers → chat-surface is the only legal
// import direction (SDR §3).

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { Button } from "@0x-copilot/design-system";

/** 256 KiB display cap. Copy/Download are never capped (NFR-2). */
export const RAW_RENDER_MAX_BYTES = 262144;

const HONESTY_LINE =
  "This result doesn't fit a view — here's the raw result. Nothing is hidden.";

export interface RawFallbackViewProps {
  readonly payload: unknown;
  readonly filename: string; // e.g. "r7f3-042-raw.json" (ledger id, ·→-)
  readonly onCopy?: (text: string) => Promise<void>;
  readonly onDownload?: (text: string, filename: string) => Promise<void>;
  readonly actionsSlot?: ReactNode; // reserved for PRD-B4 "Suggest a shape →"
}

type CopyState = "idle" | "copied" | "failed";

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  padding: "var(--space-md)",
  flex: "1 1 auto",
  minHeight: 0,
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const preStyle: CSSProperties = {
  margin: 0,
  padding: "var(--space-sm)",
  flex: "1 1 auto",
  minHeight: 0,
  overflow: "auto",
  contain: "content",
  whiteSpace: "pre",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10-5)",
  lineHeight: "var(--line-height-normal)",
  color: "var(--color-text)",
  background: "var(--color-surface-2)",
  border: "1px solid var(--color-border-subtle)",
  borderRadius: "var(--radius-sm)",
};

/** Serialize a payload to pretty JSON without ever throwing (cyclic / bigint /
 *  undefined-only inputs fall back to `String(payload)`). */
function serialize(payload: unknown): string {
  try {
    const json = JSON.stringify(payload, null, 2);
    return json ?? String(payload);
  } catch {
    return String(payload);
  }
}

/** Human byte-size label (`41.9 KB` / `1.4 MB`) over the UTF-8 byte length. */
function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function RawFallbackView({
  payload,
  filename,
  onCopy,
  onDownload,
  actionsSlot,
}: RawFallbackViewProps): ReactElement {
  // One serialize per payload — the no-jank invariant. `full` always carries the
  // complete text (Copy/Download); `display` is capped + labeled.
  const { full, byteLength } = useMemo(() => {
    const text = serialize(payload);
    return { full: text, byteLength: new TextEncoder().encode(text).length };
  }, [payload]);

  const display = useMemo(() => {
    if (byteLength <= RAW_RENDER_MAX_BYTES) return full;
    // Slice by bytes so a multibyte char never splits mid-render badly; the
    // string slice by chars is a safe superset here (display-only).
    return full.slice(0, RAW_RENDER_MAX_BYTES);
  }, [full, byteLength]);

  const elided = byteLength > RAW_RENDER_MAX_BYTES;

  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [downloadState, setDownloadState] = useState<CopyState>("idle");

  const handleCopy = (): void => {
    if (onCopy === undefined) return;
    onCopy(full)
      .then(() => setCopyState("copied"))
      .catch(() => setCopyState("failed"));
  };

  const handleDownload = (): void => {
    if (onDownload === undefined) return;
    onDownload(full, filename)
      .then(() => setDownloadState("copied"))
      .catch(() => setDownloadState("failed"));
  };

  return (
    <div style={rootStyle} data-testid="tc-raw-fallback">
      <div style={headerRowStyle}>
        <span className="ui-caption" data-testid="tc-raw-honesty">
          {HONESTY_LINE}
        </span>
        <span style={spacerStyle} aria-hidden="true" />
        <span
          className="ui-mono-caps ui-mono-caps--9"
          data-testid="tc-raw-size"
        >
          {sizeLabel(byteLength)}
        </span>
        <Button
          variant="ghost"
          onClick={handleCopy}
          disabled={onCopy === undefined}
          data-testid="tc-raw-copy"
        >
          {copyState === "copied"
            ? "Copied"
            : copyState === "failed"
              ? "Copy failed"
              : "Copy"}
        </Button>
        <Button
          variant="ghost"
          onClick={handleDownload}
          disabled={onDownload === undefined}
          data-testid="tc-raw-download"
        >
          {downloadState === "failed" ? "Download failed" : "Download"}
        </Button>
        {actionsSlot}
      </div>
      <pre style={preStyle} data-testid="tc-raw-pre">
        {display}
      </pre>
      {elided ? (
        <span className="ui-caption" data-testid="tc-raw-elision">
          — showing first {sizeLabel(RAW_RENDER_MAX_BYTES)} of{" "}
          {sizeLabel(byteLength)} · Copy and Download carry everything —
        </span>
      ) : null}
    </div>
  );
}
