// Reverse-handshake DOM helpers used by the Sources surface and the
// agent fleet panes. Given a citation_id or event_id, find the matching
// element in the rendered DOM and pulse-flash it.
//
// Pure DOM — no React state, no rerenders. Safe to call repeatedly; if
// the target isn't in the DOM (off-screen, archived, not yet replayed),
// the helper no-ops silently.
//
// Substrate touchpoint: this file uses `globalThis.document` /
// `globalThis.CSS` / `setTimeout` (timer global is universal). The
// member-access pattern matches LocalStorageKeyValueStore and the
// PresenceSignal web impl — chat-surface's allowed-but-deliberate
// substrate-bridge style. Desktop substrate's webview ships the same
// DOM APIs, so this lands unchanged there.

const PULSE_CLASS = "citation-chip--pulse";
const PULSE_DURATION_MS = 1500;

export function scrollChatToCitation(citationId: string): void {
  const doc = globalThis.document;
  if (!doc) {
    return;
  }
  const escaped = cssEscapeAttr(citationId);
  const target = doc.querySelector<HTMLElement>(
    `.citation-chip[data-citation-id="${escaped}"]`,
  );
  if (target === null) {
    return;
  }
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.classList.add(PULSE_CLASS);
  setTimeout(() => {
    target.classList.remove(PULSE_CLASS);
  }, PULSE_DURATION_MS);
}

// PR 3.2.7 — sibling helper used by paused subagent rows to anchor back
// to the gating interrupt card on the visible chat thread. Looks up the
// element bearing `data-event-id={event_id}` (approval / mcp_auth /
// ask_a_question cards already render this attribute on their root via
// existing focus-management infrastructure) and pulse-flashes it.

const FLASH_ATTR = "flashHighlight";
const FLASH_DURATION_MS = 1200;

export function scrollChatToEvent(eventId: string): void {
  const doc = globalThis.document;
  if (!doc) {
    return;
  }
  const escaped = cssEscapeAttr(eventId);
  const target = doc.querySelector<HTMLElement>(`[data-event-id="${escaped}"]`);
  if (target === null) {
    return;
  }
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.dataset[FLASH_ATTR] = "true";
  setTimeout(() => {
    delete target.dataset[FLASH_ATTR];
  }, FLASH_DURATION_MS);
}

function cssEscapeAttr(value: string): string {
  const css = globalThis.CSS;
  if (css && typeof css.escape === "function") {
    return css.escape(value);
  }
  // Fallback for older runtimes — citation_ids today are always
  // `c<base36>` (alphanumeric), so a safe-char allowlist is enough.
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}
