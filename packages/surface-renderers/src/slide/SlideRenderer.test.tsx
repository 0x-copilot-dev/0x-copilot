import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { SlideRenderer, type Slide } from "./SlideRenderer";
import { slideAdapter } from "./SlideDiff";

const SLIDE_WITH_THUMBNAIL: Slide = {
  slideId: "slide-1",
  deckId: "deck-acme-q4",
  slideNumber: 3,
  title: "Q4 Revenue Bridge",
  bullets: [
    { text: "ARR up 22% YoY" },
    { text: "Net retention 118%" },
    { text: "Pipeline coverage 3.4x" },
  ],
  thumbnailUrl: "https://example.invalid/deck-acme-q4/3.png",
};

const SLIDE_WITHOUT_THUMBNAIL: Slide = {
  slideId: "slide-2",
  deckId: "deck-acme-q4",
  slideNumber: 4,
  title: "Risks",
  bullets: [{ text: "Renewal concentration in top-3 logos" }],
};

const SLIDE_EMPTY_BULLETS: Slide = {
  slideId: "slide-3",
  deckId: "deck-acme-q4",
  slideNumber: 5,
  title: "Section divider",
  bullets: [],
  thumbnailUrl: "https://example.invalid/deck-acme-q4/5.png",
};

describe("SlideRenderer", () => {
  it("renders the slide title and slide number", () => {
    render(<SlideRenderer slide={SLIDE_WITH_THUMBNAIL} />);
    expect(screen.getByTestId("slide-title")).toHaveTextContent(
      SLIDE_WITH_THUMBNAIL.title,
    );
    expect(screen.getByTestId("slide-number")).toHaveTextContent("Slide 3");
  });

  it("renders all bullets in order", () => {
    render(<SlideRenderer slide={SLIDE_WITH_THUMBNAIL} />);
    for (let i = 0; i < SLIDE_WITH_THUMBNAIL.bullets.length; i += 1) {
      expect(screen.getByTestId(`slide-bullet-${i}`)).toHaveTextContent(
        SLIDE_WITH_THUMBNAIL.bullets[i].text,
      );
    }
  });

  it("renders the thumbnail image when thumbnailUrl is supplied", () => {
    render(<SlideRenderer slide={SLIDE_WITH_THUMBNAIL} />);
    const img = screen.getByTestId("slide-thumbnail");
    expect(img).toBeInTheDocument();
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", SLIDE_WITH_THUMBNAIL.thumbnailUrl);
    expect(
      screen.queryByTestId("slide-thumbnail-placeholder"),
    ).not.toBeInTheDocument();
  });

  it("renders the placeholder block when thumbnailUrl is missing", () => {
    render(<SlideRenderer slide={SLIDE_WITHOUT_THUMBNAIL} />);
    expect(
      screen.getByTestId("slide-thumbnail-placeholder"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("slide-thumbnail")).not.toBeInTheDocument();
  });

  it("renders the empty-bullets placeholder when bullets is empty", () => {
    render(<SlideRenderer slide={SLIDE_EMPTY_BULLETS} />);
    expect(screen.getByTestId("slide-bullets-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("slide-bullet-0")).not.toBeInTheDocument();
  });
});

describe("slideAdapter contract conformance", () => {
  it("registers the 'slide' scheme", () => {
    expect(slideAdapter.scheme).toBe("slide");
  });

  it("matches slide:// URIs", () => {
    expect(slideAdapter.matches("slide://deck-1/3")).toBe(true);
    expect(slideAdapter.matches("slide://anything")).toBe(true);
  });

  it("rejects non-slide URIs", () => {
    expect(slideAdapter.matches("email://draft-1")).toBe(false);
    expect(slideAdapter.matches("sheet-row://a/b")).toBe(false);
    expect(slideAdapter.matches("")).toBe(false);
  });

  it("declares first-party origin and schemaVersion 1", () => {
    expect(slideAdapter.metadata.origin).toBe("first-party");
    expect(slideAdapter.metadata.schemaVersion).toBe(1);
  });

  it("renderCurrent returns a React element rendering the slide", () => {
    const element = slideAdapter.renderCurrent(SLIDE_WITH_THUMBNAIL);
    render(element);
    expect(screen.getByTestId("slide-renderer")).toBeInTheDocument();
    expect(screen.getByTestId("slide-title")).toHaveTextContent(
      SLIDE_WITH_THUMBNAIL.title,
    );
  });
});
