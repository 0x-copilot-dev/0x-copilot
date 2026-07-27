/*
 * Adds stable extraction anchors to the rendered Design Compiler DOM.
 * It does not change styling, copy, or interaction state.
 */
(() => {
  "use strict";

  const state =
    new URLSearchParams(globalThis.location.search).get("state") ||
    "draft-held";
  const valid = new Set([
    "draft-held",
    "draft-edit",
    "bulk-review",
    "bulk-partial",
    "sources",
  ]);
  if (!valid.has(state)) throw new Error(`Unknown v3 parity state: ${state}`);

  const all = (selector, root = document) => [
    ...root.querySelectorAll(selector),
  ];
  const text = (node) => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const mark = (node, name) => {
    if (node instanceof Element) node.setAttribute("data-parity-anchor", name);
    return node;
  };
  const byText = (selector, needle, root = document) =>
    all(selector, root)
      .filter((node) => text(node).includes(needle))
      .sort((left, right) => text(left).length - text(right).length)[0] || null;

  let attempts = 0;
  let settleTimer = 0;
  const anchor = () => {
    attempts += 1;
    const draft = document.querySelector(
      '[data-screen-label="Surface — Gmail draft"]',
    );
    const bulk = document.querySelector(
      '[data-screen-label="Surface — Salesforce bulk review"]',
    );
    const sources = document.querySelector(".sd");
    const root = state.startsWith("draft")
      ? draft
      : state === "sources"
        ? sources
        : bulk;
    if (!(root instanceof Element)) {
      if (attempts < 300) return schedule();
      throw new Error(`v3 design state ${state} did not mount its surface.`);
    }

    mark(root, "surface-root");
    if (state !== "sources") {
      mark(root.querySelector(".sheet-scroll"), "surface-scroll");
    }

    if (state === "sources") {
      const panel = root.querySelector(".side-body");
      const groups = all(":scope > div", panel).filter((node) =>
        node.querySelector(".rowlist"),
      );
      const firstGroup = groups[0];
      const firstRow = firstGroup?.querySelector(".lrow");
      mark(panel, "sources-panel");
      mark(panel?.querySelector(".sd-note"), "sources-note");
      mark(firstGroup?.querySelector(".sect-h"), "sources-group-header");
      mark(firstGroup?.querySelector(".rowlist"), "sources-list");
      mark(firstRow, "sources-row");
      mark(firstRow?.querySelector(".lrow__logo"), "sources-icon");
      mark(firstRow?.querySelector(".src-nm"), "sources-title");
      mark(firstRow?.querySelector(".lrow__sub"), "sources-sub");
      mark(firstRow?.querySelector(":scope > svg"), "sources-trailing");
    } else if (state.startsWith("draft")) {
      mark(byText("span", "SUBJECT", root), "draft-subject-label");
      mark(byText("span", "Re: Checkout fix", root), "draft-subject");
      mark(byText("span", "DRAFT ·", root), "draft-revision");
      mark(root.querySelector(".sheet-scroll p"), "draft-body");
      mark(
        root.querySelector('[data-screen-label="Approval bar"]'),
        "draft-approval",
      );
      mark(byText("span", "Exactly this draft", root), "draft-approval-copy");
      mark(byText("button", "Reject", root), "draft-reject");
      mark(byText("button", "Edit draft", root), "draft-edit");
      mark(byText("button", "Approve & send", root), "draft-approve");
      mark(byText("span", "gmail.drafts.create", root), "draft-provenance");
      if (state === "draft-edit") {
        mark(root.querySelector("textarea.cta"), "draft-editor");
        mark(byText("button", "Done editing", root), "draft-save");
        mark(byText("button", "Cancel", root), "draft-cancel");
      }
    } else {
      mark(root.firstElementChild, "bulk-header");
      mark(byText("span", "8 opportunities", root), "bulk-title");
      mark(byText("span", "staged, not applied", root), "bulk-status");
      mark(root.querySelector(".sheet-scroll"), "bulk-table");
      const rows = all(".sheet-scroll > div", root).slice(1);
      mark(rows[0], "bulk-row");
      mark(byText("span", "Closed-Lost", rows[0]), "bulk-row-change");
      mark(
        rows[0]?.querySelector('button[title="Hold — keep as is"]'),
        "bulk-row-hold",
      );
      mark(byText("div", "agent pre-held", root), "bulk-held-reason");
      if (state === "bulk-review") {
        mark(byText("span", "approved", root), "bulk-review-copy");
        const action = mark(byText("button", "Apply", root), "bulk-action");
        mark(action?.parentElement, "bulk-action-bar");
        mark(action?.parentElement?.querySelector("span"), "bulk-action-copy");
      } else {
        mark(byText("span", "nothing lost", root), "bulk-partial-copy");
        mark(byText("button", "Retry", root), "bulk-retry");
        mark(byText("span", "failed", root), "bulk-row-failed");
      }
    }

    document.documentElement.setAttribute("data-parity-ready", state);
  };

  function schedule() {
    document.documentElement.removeAttribute("data-parity-ready");
    globalThis.clearTimeout(settleTimer);
    settleTimer = globalThis.setTimeout(anchor, 120);
  }

  // Design Compiler can replace the rendered subtree once after the support
  // runtime mounts. Re-anchor after the DOM has been quiet for 120 ms. Anchor
  // writes are attributes only, so observing childList cannot self-trigger.
  new MutationObserver(schedule).observe(document.body, {
    childList: true,
    subtree: true,
  });
  schedule();
})();
