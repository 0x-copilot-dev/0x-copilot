// PR 3.7.3 — reverse handshake helper.
//
// Given a citation_id, find the first inline `CitationChip` rendered
// for it (chips already carry `data-citation-id` from PR 1.1) and:
//   1. Scroll the chip into view, centered.
//   2. Pulse the chip's outline for 1.5s via the `.citation-chip--pulse`
//      animation in styles.css.
//
// Pure DOM — no React state, no rerenders. Safe to call repeatedly; if
// the chip isn't in the DOM (archive-only source not yet referenced in
// any rendered message) the helper no-ops silently.

const PULSE_CLASS = "citation-chip--pulse";
const PULSE_DURATION_MS = 1500;

export function scrollChatToCitation(citationId: string): void {
  if (typeof document === "undefined") {
    return;
  }
  const escaped = cssEscapeAttr(citationId);
  const target = document.querySelector<HTMLElement>(
    `.citation-chip[data-citation-id="${escaped}"]`,
  );
  if (target === null) {
    return;
  }
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.classList.add(PULSE_CLASS);
  window.setTimeout(() => {
    target.classList.remove(PULSE_CLASS);
  }, PULSE_DURATION_MS);
}

function cssEscapeAttr(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  // Fallback for older runtimes — citation_ids today are always
  // `c<base36>` (alphanumeric), so a safe-char allowlist is enough.
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}

// PR 3.2.7 — sibling helper used by paused subagent rows to anchor back
// to the gating interrupt card on the visible chat thread. Looks up the
// element bearing `data-event-id={event_id}` (approval / mcp_auth /
// ask_a_question cards already render this attribute on their root via
// existing focus-management infrastructure) and pulse-flashes it.
//
// Pure DOM, same shape as `scrollChatToCitation` — safe to call
// repeatedly; no-ops silently when the anchor isn't in the DOM (event
// outside the rendered window, or the user just reconnected and the
// interrupt card hasn't replayed yet).

const FLASH_ATTR = "flashHighlight";
const FLASH_DURATION_MS = 1200;

export function scrollChatToEvent(eventId: string): void {
  if (typeof document === "undefined") {
    return;
  }
  const escaped = cssEscapeAttr(eventId);
  const target = document.querySelector<HTMLElement>(
    `[data-event-id="${escaped}"]`,
  );
  if (target === null) {
    return;
  }
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.dataset[FLASH_ATTR] = "true";
  window.setTimeout(() => {
    delete target.dataset[FLASH_ATTR];
  }, FLASH_DURATION_MS);
}
