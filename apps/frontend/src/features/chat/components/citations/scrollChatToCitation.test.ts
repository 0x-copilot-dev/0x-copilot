import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { scrollChatToCitation } from "./scrollChatToCitation";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = "";
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("scrollChatToCitation", () => {
  it("scrolls and pulses the matching chip; clears the pulse after 1500ms", () => {
    const chip = document.createElement("a");
    chip.className = "citation-chip";
    chip.setAttribute("data-citation-id", "c3");
    document.body.appendChild(chip);
    const scrollIntoView = vi.fn();
    chip.scrollIntoView =
      scrollIntoView as unknown as typeof chip.scrollIntoView;

    scrollChatToCitation("c3");

    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "center",
      behavior: "smooth",
    });
    expect(chip.classList.contains("citation-chip--pulse")).toBe(true);

    vi.advanceTimersByTime(1600);
    expect(chip.classList.contains("citation-chip--pulse")).toBe(false);
  });

  it("no-ops silently when no chip exists for the citation_id", () => {
    expect(() => scrollChatToCitation("c-missing")).not.toThrow();
  });
});
