import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdapterPreview, PREVIEW_CSP } from "./AdapterPreview";
import { syntheticStateFor } from "./SyntheticStateFactory";

const SAMPLE_STATE = syntheticStateFor("form");

describe("AdapterPreview", () => {
  it("renders the iframe with the documented sandbox and CSP", () => {
    render(
      <AdapterPreview
        candidateSource="module.exports = { renderCurrent: () => 'ok', renderDiff: () => 'ok' };"
        state={SAMPLE_STATE}
        mode="current"
      />,
    );

    const iframe = screen.getByTestId(
      "adapter-preview-iframe",
    ) as HTMLIFrameElement;
    // sandbox MUST NOT include allow-same-origin — that's the boundary
    // that keeps the candidate off the parent's DOM / storage / cookies.
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    // CSP is documented on the iframe element so the test can pin the
    // exact header. ``connect-src 'none'`` forbids fetch /
    // XMLHttpRequest / WebSocket / EventSource.
    expect(iframe.getAttribute("data-csp")).toBe(PREVIEW_CSP);
    expect(PREVIEW_CSP).toContain("connect-src 'none'");
    expect(PREVIEW_CSP).toContain("default-src 'none'");
  });

  it("posts a srcdoc that includes the candidate source", () => {
    const source =
      "module.exports = { renderCurrent: function(s){ return 'CUR:'+s.saas; }, renderDiff: function(d){ return 'DIFF'; } };";
    render(
      <AdapterPreview
        candidateSource={source}
        state={SAMPLE_STATE}
        mode="current"
      />,
    );

    const iframe = screen.getByTestId(
      "adapter-preview-iframe",
    ) as HTMLIFrameElement;
    // srcdoc is the only data path into the iframe (no src URL, no
    // allow-same-origin) so we assert the candidate source is embedded.
    expect(iframe.srcdoc).toContain("renderCurrent");
    expect(iframe.srcdoc).toContain("renderDiff");
    // CSP meta-tag is inlined into the srcdoc as the inner enforcement
    // layer (the outer layer is the iframe sandbox attribute).
    expect(iframe.srcdoc).toContain('http-equiv="Content-Security-Policy"');
    expect(iframe.srcdoc).toContain("connect-src 'none'");
  });

  it("fails closed when the candidate throws — surfaces an error placeholder", async () => {
    const throwingSource =
      "module.exports = { renderCurrent: function(){ throw new Error('boom'); }, renderDiff: function(){ throw new Error('boom'); } };";
    render(
      <AdapterPreview
        candidateSource={throwingSource}
        state={SAMPLE_STATE}
        mode="current"
      />,
    );

    // Simulate the iframe posting an error message back to the parent.
    // The component's window listener accepts messages whose
    // ``source === frame.contentWindow``; in jsdom the iframe's
    // contentWindow is a Window-like object that does dispatch
    // postMessage events on the parent. We fire one manually to pin the
    // contract — the host MUST render an error placeholder when the
    // candidate signals failure.
    const iframe = screen.getByTestId(
      "adapter-preview-iframe",
    ) as HTMLIFrameElement;
    // Manually dispatch a MessageEvent whose source is the iframe's
    // contentWindow — the component matches on that to authenticate
    // the sender.
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { kind: "error", detail: "render: boom" },
        source: iframe.contentWindow,
      }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("adapter-preview-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("adapter-preview-error").textContent).toContain(
      "boom",
    );
    // The error badge is set so the reviewer sees the failure at a
    // glance — never a silent tier-3 fallback.
    expect(
      screen.getByTestId("adapter-preview-error-badge"),
    ).toBeInTheDocument();
  });

  it("flips to rendered when the iframe signals success", async () => {
    render(
      <AdapterPreview
        candidateSource="module.exports = { renderCurrent: () => 'ok', renderDiff: () => 'ok' };"
        state={SAMPLE_STATE}
        mode="current"
      />,
    );

    const iframe = screen.getByTestId(
      "adapter-preview-iframe",
    ) as HTMLIFrameElement;
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { kind: "rendered" },
        source: iframe.contentWindow,
      }),
    );

    await waitFor(() => {
      const preview = screen.getByTestId("adapter-preview");
      expect(preview.getAttribute("data-status")).toBe("rendered");
    });
  });
});
