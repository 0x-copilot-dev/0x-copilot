// Sandboxed preview pane for a tier-2 adapter candidate (Phase 7C).
//
// Compliance bar (PRD §9.5.2):
//   - The candidate runs inside an <iframe sandbox="allow-scripts"> with
//     NO ``allow-same-origin``. The iframe document carries a strict CSP
//     meta tag (no network, no images, no fonts, no nested frames).
//   - Adapter throws collapse into an explicit error placeholder. The
//     reviewer must see that the candidate broke, never a silent tier-3
//     fallback.
//   - The candidate is mounted against synthetic state only (passed in
//     from the caller — built by SyntheticStateFactory).
//
// The candidate source is a string. We compile it once via
// ``new Function(...)`` inside the iframe (the parent's CSP is
// orthogonal — script-eval lives in the sandboxed iframe, which has no
// privileged objects, no parent access, no network). The contract: the
// candidate source evaluates to a CommonJS-style ``module.exports`` or
// to an ``exports.adapter`` whose ``renderCurrent(state)`` /
// ``renderDiff(diff)`` produce HTML strings. We render those into the
// iframe body. Reviewers see what a real tier-2 mount produces visually
// without giving the candidate access to the real React tree.

import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";

import { Badge, Card } from "@enterprise-search/design-system";

import type { SyntheticState } from "./SyntheticStateFactory";

export type PreviewMode = "current" | "diff";

export interface AdapterPreviewProps {
  readonly candidateSource: string;
  readonly state: SyntheticState;
  readonly mode: PreviewMode;
}

interface PreviewMessage {
  readonly kind: "ready" | "rendered" | "error";
  readonly detail?: string;
}

export const PREVIEW_CSP =
  "default-src 'none'; " +
  "script-src 'unsafe-inline'; " +
  "style-src 'unsafe-inline'; " +
  "connect-src 'none'; " +
  "img-src 'none'; " +
  "font-src 'none'; " +
  "frame-src 'none'";

export function AdapterPreview({
  candidateSource,
  state,
  mode,
}: AdapterPreviewProps): ReactElement {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [status, setStatus] = useState<"loading" | "rendered" | "error">(
    "loading",
  );
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  useEffect(() => {
    setStatus("loading");
    setErrorDetail(null);
    const frame = iframeRef.current;
    if (frame === null) return;
    const channel = new MessageChannel();
    const handleMessage = (event: MessageEvent<PreviewMessage>): void => {
      const data = event.data;
      if (typeof data !== "object" || data === null) return;
      if (data.kind === "rendered") {
        setStatus("rendered");
      } else if (data.kind === "error") {
        setStatus("error");
        setErrorDetail(
          typeof data.detail === "string" ? data.detail : "render_failed",
        );
      }
    };
    channel.port1.addEventListener("message", handleMessage);
    channel.port1.start();

    const stateForMode = mode === "current" ? state.current : state.diff;
    const html = buildPreviewHtml({
      candidateSource,
      state: stateForMode,
      mode,
    });
    frame.srcdoc = html;
    // The iframe posts via its own ``parent.postMessage`` after onload;
    // we accept via ``window.addEventListener`` here so we never grant
    // the iframe a port reference. Same-origin is forbidden by the
    // sandbox, so the iframe cannot read this.
    const windowListener = (event: MessageEvent<PreviewMessage>): void => {
      if (event.source !== frame.contentWindow) return;
      handleMessage(event);
    };
    window.addEventListener("message", windowListener);

    return () => {
      window.removeEventListener("message", windowListener);
      channel.port1.removeEventListener("message", handleMessage);
      channel.port1.close();
      channel.port2.close();
    };
  }, [candidateSource, state, mode]);

  return (
    <Card
      data-testid="adapter-preview"
      data-status={status}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minHeight: 320,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <strong>Preview ({mode})</strong>
        {status === "rendered" ? (
          <Badge tone="success">Rendered</Badge>
        ) : status === "error" ? (
          <Badge tone="danger" data-testid="adapter-preview-error-badge">
            Failed
          </Badge>
        ) : (
          <Badge tone="neutral">Loading</Badge>
        )}
      </header>
      {status === "error" && errorDetail !== null ? (
        <p
          role="alert"
          data-testid="adapter-preview-error"
          style={{ margin: 0, color: "var(--color-text-danger)" }}
        >
          Candidate threw during render: {errorDetail}
        </p>
      ) : null}
      <iframe
        ref={iframeRef}
        title="Tier-2 adapter preview"
        // sandbox="allow-scripts" with NO ``allow-same-origin`` —
        // the iframe is treated as a foreign origin and cannot read the
        // parent's DOM, cookies, or storage.
        sandbox="allow-scripts"
        data-testid="adapter-preview-iframe"
        data-csp={PREVIEW_CSP}
        style={{
          flex: 1,
          width: "100%",
          minHeight: 240,
          border: "1px solid var(--color-border)",
          borderRadius: 6,
          background: "var(--color-surface)",
        }}
      />
    </Card>
  );
}

interface BuildHtmlArgs {
  readonly candidateSource: string;
  readonly state: unknown;
  readonly mode: PreviewMode;
}

function buildPreviewHtml(args: BuildHtmlArgs): string {
  // Embed the candidate source as a Function body. The iframe parses it
  // in a sandboxed origin (no allow-same-origin) so even if the source
  // attempts ``window.parent``, ``document.cookie``, etc. it sees only
  // the iframe's own (empty) DOM. The CSP additionally forbids any
  // network egress.
  //
  // Convention: the candidate is expected to set
  // ``module.exports = { renderCurrent, renderDiff }`` (CommonJS-style)
  // or to assign ``window.__atlas_adapter = { renderCurrent, renderDiff }``.
  // The host shim below supports both. ``renderCurrent`` / ``renderDiff``
  // return an HTML string (the preview is a render-to-html drill, not a
  // React tree — the real renderer mounts React inside the worker; the
  // review-time goal is to surface throws and rough visual shape).
  //
  // Errors at compile or render time get reported back via
  // ``parent.postMessage`` so the host can flip the badge to ``Failed``.
  const escapedSource = JSON.stringify(args.candidateSource);
  const escapedState = JSON.stringify(args.state);
  const escapedMode = JSON.stringify(args.mode);
  // The CSP is enforced by the browser; we re-document it via data-csp
  // for the test. ``unsafe-inline`` is required for ``script`` because
  // the candidate source is embedded as an inline script. The sandbox
  // attribute (no allow-same-origin) is the harder boundary — even with
  // arbitrary inline script, the iframe cannot escape its own origin.
  return [
    "<!doctype html>",
    "<html>",
    "<head>",
    `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">`,
    "<style>",
    "body { font-family: system-ui, sans-serif; margin: 0; padding: 12px; ",
    "color: #111; background: #fff; }",
    ".adapter-error { color: #b91c1c; }",
    "</style>",
    "</head>",
    "<body>",
    '<div id="root"></div>',
    "<script>",
    "(function() {",
    "  var source = " + escapedSource + ";",
    "  var state = " + escapedState + ";",
    "  var mode = " + escapedMode + ";",
    "  function report(kind, detail) {",
    "    try {",
    "      parent.postMessage(",
    "        { kind: kind, detail: detail || '' },",
    "        '*'",
    "      );",
    "    } catch (e) {}",
    "  }",
    "  var adapter;",
    "  try {",
    "    var moduleObj = { exports: {} };",
    "    var exportsObj = moduleObj.exports;",
    "    var factory = new Function('module', 'exports', source);",
    "    factory(moduleObj, exportsObj);",
    "    adapter = moduleObj.exports && (moduleObj.exports.adapter ||",
    "      moduleObj.exports.default || moduleObj.exports);",
    "    if (!adapter || (typeof adapter.renderCurrent !== 'function' &&",
    "        typeof adapter.renderDiff !== 'function')) {",
    "      adapter = (typeof window !== 'undefined' &&",
    "        window.__atlas_adapter) || adapter;",
    "    }",
    "  } catch (e) {",
    "    report('error', 'compile: ' + (e && e.message ? e.message : 'unknown'));",
    "    return;",
    "  }",
    "  if (!adapter || typeof adapter.renderCurrent !== 'function' ||",
    "      typeof adapter.renderDiff !== 'function') {",
    "    report('error', 'shape: missing renderCurrent or renderDiff');",
    "    return;",
    "  }",
    "  try {",
    "    var fn = mode === 'diff' ? adapter.renderDiff : adapter.renderCurrent;",
    "    var output = fn(state);",
    "    var root = document.getElementById('root');",
    "    if (typeof output === 'string') {",
    "      root.textContent = output;",
    "    } else if (output && typeof output === 'object') {",
    "      root.textContent = JSON.stringify(output);",
    "    } else {",
    "      root.textContent = String(output);",
    "    }",
    "    report('rendered');",
    "  } catch (e) {",
    "    report('error', 'render: ' + (e && e.message ? e.message : 'unknown'));",
    "  }",
    "})();",
    "</script>",
    "</body>",
    "</html>",
  ].join("\n");
}
