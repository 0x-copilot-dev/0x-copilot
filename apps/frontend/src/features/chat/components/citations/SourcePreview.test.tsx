import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import type { SourceEntry } from "@0x-copilot/api-types";
import {
  SourcePreviewProvider,
  useSourcePreviewTrigger,
} from "./SourcePreview";

function source(overrides: Partial<SourceEntry> = {}): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "web_search",
    source_doc_id: "https://pypi.org/project/deepagents",
    source_url: "https://pypi.org/project/deepagents",
    title: "DeepAgents on PyPI",
    snippet: "An agent harness built on langchain.",
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-06T12:00:00Z",
    ...overrides,
  };
}

function Trigger({ entry }: { entry: SourceEntry }): ReactElement {
  const props = useSourcePreviewTrigger(entry);
  return (
    <a href="#" data-testid="trigger" {...props}>
      [1]
    </a>
  );
}

function mockHoverViewport(supportsHover: boolean): () => void {
  const original = window.matchMedia;
  window.matchMedia = (query: string) =>
    ({
      matches:
        query.includes("hover: hover") === supportsHover &&
        query.includes("hover: hover"),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
      onchange: null,
    }) as unknown as MediaQueryList;
  return () => {
    if (original === undefined) {
      delete (window as { matchMedia?: unknown }).matchMedia;
    } else {
      window.matchMedia = original;
    }
  };
}

let restoreMatchMedia: (() => void) | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  restoreMatchMedia = mockHoverViewport(true);
});

afterEach(() => {
  vi.useRealTimers();
  restoreMatchMedia?.();
  restoreMatchMedia = null;
});

describe("SourcePreviewProvider", () => {
  it("opens the card after the open delay and shows source fields", () => {
    render(
      <SourcePreviewProvider>
        <Trigger entry={source()} />
      </SourcePreviewProvider>,
    );
    fireEvent.pointerEnter(screen.getByTestId("trigger"));
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(220);
    });
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/DeepAgents on PyPI/);
    expect(dialog).toHaveTextContent(/An agent harness built on langchain/);
  });

  it("dismisses after pointer leave + close delay", () => {
    render(
      <SourcePreviewProvider>
        <Trigger entry={source()} />
      </SourcePreviewProvider>,
    );
    fireEvent.pointerEnter(screen.getByTestId("trigger"));
    act(() => {
      vi.advanceTimersByTime(220);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerLeave(screen.getByTestId("trigger"));
    act(() => {
      vi.advanceTimersByTime(120);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on Escape", () => {
    render(
      <SourcePreviewProvider>
        <Trigger entry={source()} />
      </SourcePreviewProvider>,
    );
    fireEvent.pointerEnter(screen.getByTestId("trigger"));
    act(() => {
      vi.advanceTimersByTime(220);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders no card on touch-only viewports", () => {
    restoreMatchMedia?.();
    restoreMatchMedia = mockHoverViewport(false);
    render(
      <SourcePreviewProvider>
        <Trigger entry={source()} />
      </SourcePreviewProvider>,
    );
    fireEvent.pointerEnter(screen.getByTestId("trigger"));
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
