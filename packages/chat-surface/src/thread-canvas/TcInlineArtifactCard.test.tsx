import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import type { Transport } from "@0x-copilot/chat-transport";

import {
  TcInlineArtifactCard,
  type InlineArtifactEntry,
} from "./TcInlineArtifactCard";

const ARTIFACT: InlineArtifactEntry = {
  artifactId: "art_1",
  kind: "dataset",
  uri: "artifact-dataset://art_1@1",
  name: "bookings-forecast.csv",
  revision: 1,
  createdSeq: 12,
  subjectKey: "artifact:art_1",
};

/** Never resolves — proves the collapsed card asks for nothing. */
function idleTransport(): { transport: Transport; requests: string[] } {
  const requests: string[] = [];
  const transport = {
    request: (req: { path: string }) => {
      requests.push(req.path);
      return new Promise<never>(() => {});
    },
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({}),
    capabilities: () => ({}),
  } as unknown as Transport;
  return { transport, requests };
}

describe("TcInlineArtifactCard", () => {
  it("shows the real filename, not the synthesized kind label", () => {
    const { transport } = idleTransport();
    render(<TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />);
    expect(screen.getByText("bookings-forecast.csv")).toBeTruthy();
    // The bug this replaces: Focus rendered "dataset artifact" for a file the
    // user named themselves.
    expect(screen.queryByText("dataset artifact")).toBeNull();
  });

  // The point of collapsing by default: an artifact the reader has not opened
  // must not cost a fetch. `ArtifactSurface` owns the fetch, so this holds only
  // as long as it stays unmounted while collapsed.
  it("fetches nothing until it is expanded", () => {
    const { transport, requests } = idleTransport();
    render(<TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />);
    expect(requests).toEqual([]);

    fireEvent.click(screen.getByTestId("tc-inline-artifact-toggle"));
    expect(requests.length).toBeGreaterThan(0);
    expect(requests[0]).toContain("art_1");
  });

  it("toggles with a labelled control that carries disclosure semantics", () => {
    const { transport } = idleTransport();
    render(<TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />);
    const toggle = screen.getByTestId("tc-inline-artifact-toggle");

    // A labelled button, not a caret — the label is part of the design.
    expect(toggle.textContent).toBe("Expand");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(toggle.textContent).toBe("Collapse");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(
      screen.getByTestId("tc-inline-artifact").getAttribute("data-open"),
    ).toBe("true");
  });

  it("offers Studio only when the host wired it, and passes the subject key", () => {
    const { transport } = idleTransport();
    const onOpenInStudio = vi.fn();
    const { rerender } = render(
      <TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-artifact-toggle"));
    expect(screen.queryByTestId("tc-inline-artifact-open-studio")).toBeNull();

    rerender(
      <TcInlineArtifactCard
        artifact={ARTIFACT}
        transport={transport}
        onOpenInStudio={onOpenInStudio}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-artifact-open-studio"));
    expect(onOpenInStudio).toHaveBeenCalledWith("artifact:art_1");
  });

  // Inline, two artifacts can be expanded at once. A shared DOM id makes the
  // second label focus the FIRST field, so the scope has to come from identity.
  it("scopes its editor DOM ids by artifact id", () => {
    const { transport } = idleTransport();
    const { container } = render(
      <>
        <TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />
        <TcInlineArtifactCard
          artifact={{ ...ARTIFACT, artifactId: "art_2", name: "notes.md" }}
          transport={transport}
        />
      </>,
    );
    const cards = container.querySelectorAll(
      '[data-testid="tc-inline-artifact"]',
    );
    expect(cards[0]?.getAttribute("data-artifact-id")).toBe("art_1");
    expect(cards[1]?.getAttribute("data-artifact-id")).toBe("art_2");
  });

  it("carries an identity hue derived from the artifact uri", () => {
    const { transport } = idleTransport();
    render(<TcInlineArtifactCard artifact={ARTIFACT} transport={transport} />);
    const hue = screen
      .getByTestId("tc-inline-artifact")
      .getAttribute("data-surface-hue");
    expect(hue).toBeTruthy();
    expect(hue).not.toBe("none");
  });
});
