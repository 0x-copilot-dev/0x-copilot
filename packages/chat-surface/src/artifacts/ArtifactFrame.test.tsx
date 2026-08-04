import { render, screen } from "@testing-library/react";
import type { ArtifactRenderState } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";
import { describe, expect, it, vi } from "vitest";
import { ArtifactFrame } from "./ArtifactFrame";

const transport = {
  request: vi.fn(),
  subscribe: vi.fn(),
} as unknown as Transport;

const artifact: ArtifactRenderState = {
  artifactId: "art_550e8400-e29b-41d4-a716-446655440000",
  kind: "document",
  title: "forecast-notes.md",
  mediaType: "text/markdown",
  filename: "forecast-notes.md",
  revision: 1,
  digest: "a".repeat(64),
  byteSize: 77,
  author: "model",
  createdAt: "2026-01-01T00:00:00Z",
  preview: "ready",
};

/**
 * `useArtifactSurface` nulls the artifact out on BOTH `error` and `deleted`, so
 * this component is the only thing that can tell a reader which one happened.
 * It used to test the null artifact before the status, which meant every failed
 * fetch rendered the loading placeholder and `artifact-error` was unreachable —
 * a wire-contract break that stopped every artifact from loading presented
 * itself as a spinner. Each state gets its own case so that cannot recur
 * silently.
 */
describe("ArtifactFrame state dispatch", () => {
  it("shows the loading placeholder only while the fetch is in flight", () => {
    render(
      <ArtifactFrame artifact={null} status="loading" transport={transport} />,
    );
    expect(screen.getByTestId("artifact-loading")).toBeInTheDocument();
  });

  it("reports a failed fetch as an error, not as perpetual loading", () => {
    render(
      <ArtifactFrame artifact={null} status="error" transport={transport} />,
    );
    expect(screen.getByTestId("artifact-error")).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-loading")).toBeNull();
  });

  it("keeps a deleted artifact distinct from a failed one", () => {
    render(
      <ArtifactFrame artifact={null} status="deleted" transport={transport} />,
    );
    expect(screen.getByTestId("artifact-deleted")).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-loading")).toBeNull();
  });

  it("does not spin forever when the fetch settled with nothing to show", () => {
    render(
      <ArtifactFrame artifact={null} status="ready" transport={transport} />,
    );
    expect(screen.getByTestId("artifact-error")).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-loading")).toBeNull();
  });

  it("still frames an artifact whose content could not be streamed", () => {
    // `useArtifactSurface` reports `error` WITH the artifact when the host has
    // metadata but cannot stream bytes. The frame and its notice are the whole
    // point of that state, so the error card must not swallow it.
    render(
      <ArtifactFrame
        artifact={{ ...artifact, preview: "error" }}
        status="error"
        transport={transport}
      />,
    );
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-error")).toBeNull();
  });

  it("renders the artifact once it is ready", () => {
    render(
      <ArtifactFrame artifact={artifact} status="ready" transport={transport}>
        <p>body</p>
      </ArtifactFrame>,
    );
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    expect(screen.getByText("forecast-notes.md")).toBeInTheDocument();
  });
});
