import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RawFallbackView, RAW_RENDER_MAX_BYTES } from "./RawFallbackView";

describe("RawFallbackView", () => {
  it("renders the honesty line and a size label", () => {
    render(<RawFallbackView payload={{ a: 1 }} filename="r-1-raw.json" />);
    expect(screen.getByTestId("tc-raw-honesty")).toHaveTextContent(
      "This result doesn't fit a view — here's the raw result. Nothing is hidden.",
    );
    expect(screen.getByTestId("tc-raw-size")).toBeInTheDocument();
  });

  it("Copy / Download receive the FULL serialized text (+ filename)", async () => {
    const onCopy = vi.fn(async () => {});
    const onDownload = vi.fn(async () => {});
    const payload = { hello: "world", n: 3 };
    render(
      <RawFallbackView
        payload={payload}
        filename="r7f3-042-raw.json"
        onCopy={onCopy}
        onDownload={onDownload}
      />,
    );
    const full = JSON.stringify(payload, null, 2);
    fireEvent.click(screen.getByTestId("tc-raw-copy"));
    fireEvent.click(screen.getByTestId("tc-raw-download"));
    expect(onCopy).toHaveBeenCalledWith(full);
    expect(onDownload).toHaveBeenCalledWith(full, "r7f3-042-raw.json");
    await waitFor(() =>
      expect(screen.getByTestId("tc-raw-copy")).toHaveTextContent("Copied"),
    );
  });

  it("renders a >40KB payload as ONE <pre> via a single JSON.stringify", () => {
    const spy = vi.spyOn(JSON, "stringify");
    const big = {
      rows: Array.from({ length: 2000 }, (_, i) => ({ i, v: `row-${i}` })),
    };
    const before = spy.mock.calls.length;
    render(<RawFallbackView payload={big} filename="big-raw.json" />);
    const pre = screen.getByTestId("tc-raw-pre");
    // Exactly one text node child.
    expect(pre.childNodes).toHaveLength(1);
    expect(pre.childNodes[0].nodeType).toBe(Node.TEXT_NODE);
    // The component serialized exactly once for this payload.
    expect(spy.mock.calls.length - before).toBe(1);
    spy.mockRestore();
  });

  it("labels the elision above the display cap but copies the full text", () => {
    const onCopy = vi.fn(async () => {});
    // A string payload just over the cap.
    const huge = "x".repeat(RAW_RENDER_MAX_BYTES + 5000);
    render(
      <RawFallbackView
        payload={huge}
        filename="huge-raw.json"
        onCopy={onCopy}
      />,
    );
    expect(screen.getByTestId("tc-raw-elision")).toHaveTextContent(
      "Copy and Download carry everything",
    );
    fireEvent.click(screen.getByTestId("tc-raw-copy"));
    // Copy carries the FULL serialized string (JSON.stringify of the string
    // adds surrounding quotes), never the truncated display slice.
    const full = JSON.stringify(huge, null, 2);
    expect(onCopy).toHaveBeenCalledWith(full);
  });

  it("renders script / injection payloads inert as text", () => {
    const payload = { note: "<script>alert(1)</script> [click](javascript:x)" };
    render(<RawFallbackView payload={payload} filename="x-raw.json" />);
    const pre = screen.getByTestId("tc-raw-pre");
    expect(pre.querySelector("script")).toBeNull();
    expect(pre.textContent).toContain("<script>alert(1)</script>");
  });

  it("falls back without throwing for a non-serializable payload", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() =>
      render(<RawFallbackView payload={cyclic} filename="c-raw.json" />),
    ).not.toThrow();
    expect(screen.getByTestId("tc-raw-pre")).toBeInTheDocument();
  });

  it("shows Copy failed on a rejecting callback (no unhandled rejection)", async () => {
    const onCopy = vi.fn(async () => {
      throw new Error("clipboard blocked");
    });
    render(
      <RawFallbackView payload={{ a: 1 }} filename="x.json" onCopy={onCopy} />,
    );
    fireEvent.click(screen.getByTestId("tc-raw-copy"));
    await waitFor(() =>
      expect(screen.getByTestId("tc-raw-copy")).toHaveTextContent(
        "Copy failed",
      ),
    );
  });

  it("disables buttons when callbacks are absent", () => {
    render(<RawFallbackView payload={{ a: 1 }} filename="x.json" />);
    expect(screen.getByTestId("tc-raw-copy")).toBeDisabled();
    expect(screen.getByTestId("tc-raw-download")).toBeDisabled();
  });
});
