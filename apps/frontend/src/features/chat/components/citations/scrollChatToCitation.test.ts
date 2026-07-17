import {
  scrollChatToCitation,
  scrollChatToEvent,
} from "@0x-copilot/chat-surface";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// PR 3.2.7 — `scrollChatToEvent` is the sibling helper paused subagent
// rows use to anchor back to the gating interrupt event card.
describe("scrollChatToEvent", () => {
  it("scrolls the matching anchor into view and flashes for 1.2s", () => {
    const card = document.createElement("section");
    card.setAttribute("data-event-id", "evt_42");
    document.body.appendChild(card);
    const scrollIntoView = vi.fn();
    card.scrollIntoView =
      scrollIntoView as unknown as typeof card.scrollIntoView;

    scrollChatToEvent("evt_42");

    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "center",
      behavior: "smooth",
    });
    expect(card.dataset.flashHighlight).toBe("true");

    vi.advanceTimersByTime(1300);
    expect(card.dataset.flashHighlight).toBeUndefined();
  });

  it("no-ops silently when the event_id isn't on the rendered thread", () => {
    expect(() => scrollChatToEvent("evt_missing")).not.toThrow();
  });

  it("escapes weird characters in the event_id selector", () => {
    const card = document.createElement("section");
    const eventId = 'evt"42';
    card.setAttribute("data-event-id", eventId);
    document.body.appendChild(card);
    card.scrollIntoView = vi.fn();

    expect(() => scrollChatToEvent(eventId)).not.toThrow();
  });
});
