// Tests for <FilePreview /> (P7-B2).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FilePreview } from "./FilePreview";

describe("<FilePreview>", () => {
  it("renders loading skeleton in idle/loading states", () => {
    const { rerender } = render(
      <FilePreview
        fileKind="pdf"
        mimeLabel="PDF document"
        state={{ kind: "idle" }}
      />,
    );
    expect(
      screen.getByTestId("library-file-preview").getAttribute("data-state"),
    ).toBe("idle");

    rerender(
      <FilePreview
        fileKind="pdf"
        mimeLabel="PDF document"
        state={{ kind: "loading" }}
      />,
    );
    expect(
      screen.getByTestId("library-file-preview").getAttribute("data-state"),
    ).toBe("loading");
  });

  it("renders an iframe for kind=pdf with the signed URL", () => {
    render(
      <FilePreview
        fileKind="pdf"
        mimeLabel="PDF document"
        state={{
          kind: "ready",
          signedUrl: "https://signed.example/file.pdf",
        }}
      />,
    );
    const iframe = screen.getByTestId(
      "library-file-preview-pdf",
    ) as HTMLIFrameElement;
    expect(iframe.src).toBe("https://signed.example/file.pdf");
  });

  it("renders an <img> for kind=image with alt fallback", () => {
    render(
      <FilePreview
        fileKind="image"
        mimeLabel="PNG image"
        state={{
          kind: "ready",
          signedUrl: "https://signed.example/img.png",
          alt: "Q3 chart",
        }}
      />,
    );
    const img = screen.getByTestId(
      "library-file-preview-image",
    ) as HTMLImageElement;
    expect(img.src).toBe("https://signed.example/img.png");
    expect(img.alt).toBe("Q3 chart");
  });

  it("renders a thumbnail for doc/sheet/slide when thumbnailUrl is supplied", () => {
    render(
      <FilePreview
        fileKind="sheet"
        mimeLabel="Spreadsheet"
        state={{
          kind: "ready",
          signedUrl: "https://signed.example/sheet.xlsx",
          thumbnailUrl: "https://signed.example/sheet-thumb.png",
        }}
      />,
    );
    expect(screen.getByTestId("library-file-preview-thumbnail")).toBeTruthy();
  });

  it("falls back to a metadata-only placeholder with a download link for kinds with no thumb", () => {
    render(
      <FilePreview
        fileKind="other"
        mimeLabel="Generic binary"
        state={{
          kind: "ready",
          signedUrl: "https://signed.example/blob",
        }}
      />,
    );
    expect(screen.getByText("Preview not available")).toBeTruthy();
    expect(screen.getByText("Generic binary")).toBeTruthy();
    const link = screen.getByTestId(
      "library-file-preview-download-link",
    ) as HTMLAnchorElement;
    expect(link.href).toBe("https://signed.example/blob");
  });

  it("renders error state and wires the retry callback", () => {
    const onRetry = vi.fn();
    render(
      <FilePreview
        fileKind="pdf"
        mimeLabel="PDF document"
        state={{ kind: "error", message: "signed URL expired" }}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("signed URL expired")).toBeTruthy();
    fireEvent.click(screen.getByTestId("library-file-preview-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
