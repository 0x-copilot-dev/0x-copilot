/*
 * Runtime-only driver for the supplied Design Compiler walkthrough.
 *
 * It never changes a design style or copy rule. The preparation transform
 * selects the mock's own mode/step before first render and disables autoplay;
 * this driver then adds temporary data anchors to the rendered DOM. The
 * extractor waits for `data-parity-ready`, so every state is deterministic.
 */
(() => {
  "use strict";

  const STATES = Object.freeze({
    "focus-thinking": { mode: "focus", chapter: "Thinking" },
    "studio-third-party-read": { mode: "studio", chapter: "Read · 3rd-party" },
    "studio-web-chat-only": { mode: "studio", chapter: "Web search" },
    "studio-csv-chat-only": { mode: "studio", chapter: "Read CSV · local" },
    "studio-write-held": { mode: "studio", chapter: "Write file" },
    "studio-wrap-file": { mode: "studio", chapter: "Wrap" },
  });

  const params = new URLSearchParams(globalThis.location.search);
  const state = params.get("state") || "focus-thinking";
  const spec = STATES[state];
  if (spec === undefined) {
    throw new Error(
      `Unknown chat/tool-call parity state "${state}". Expected one of: ${Object.keys(STATES).join(", ")}`,
    );
  }

  const text = (node) => (node.textContent || "").replace(/\s+/g, " ").trim();
  const directText = (node) =>
    [...node.childNodes]
      .filter((child) => child.nodeType === Node.TEXT_NODE)
      .map((child) => child.nodeValue || "")
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  const first = (selector) => document.querySelector(selector);
  const all = (selector) => [...document.querySelectorAll(selector)];
  const mark = (selector, name) => {
    const node = typeof selector === "string" ? first(selector) : selector;
    if (node instanceof Element) node.setAttribute("data-parity-anchor", name);
    return node;
  };
  const smallestTextNode = (needle) =>
    all(".side-body *")
      .filter((node) => text(node).includes(needle))
      .sort((left, right) => text(left).length - text(right).length)[0] || null;

  const anchorRenderedDom = () => {
    const shell = mark(".mw", "shell");
    if (!shell) return false;
    mark(".mw-bar", "window-bar");
    mark(".mw-bar .seg", "mode-switcher");
    mark(".ws3-main > section", "chat-column");
    mark(".ws3-main > section .side-body", "chat-transcript");
    mark(".sd-cmp .cmp", "composer");
    mark(".cv", "studio-canvas");
    mark(".cv .sheet-scroll", "studio-canvas-empty");
    mark(".sd", "focus-activity-panel");
    mark(".sd .sd-h", "focus-activity-heading");
    mark(".sd .plan", "focus-plan");
    mark(".tcall", "tool-card-first");

    const thinking = all(".side-body *").find((node) =>
      directText(node).includes("Thought for 4s"),
    );
    if (thinking) mark(thinking.parentElement || thinking, "thinking-card");

    const toolByName = (name, anchor) => {
      const node = all(".tcall").find((card) => text(card).includes(name));
      if (node) mark(node, anchor);
    };
    toolByName("linear.issues.get", "tool-card-linear");
    toolByName("web.search", "tool-card-web");
    toolByName("fs.read", "tool-card-csv");
    toolByName("fs.write", "tool-card-write");

    // These are intentionally leaf/header anchors. The mock has no durable
    // component identifier for those cards, and walking to a generic ancestor
    // risks overwriting the chat-transcript anchor; the label is still a
    // deterministic representative of the card's presence and styling.
    mark(smallestTextNode("SOURCES · 3"), "sources-card");
    mark(smallestTextNode("forecast_q1.csv"), "csv-card");
    const filePre = all("pre").find((node) =>
      text(node).includes("Standup — Mon Feb 9"),
    );
    if (filePre) mark(filePre.parentElement, "file-card");

    shell.setAttribute("data-parity-state", state);
    document.documentElement.setAttribute("data-parity-ready", state);
    document.dispatchEvent(
      new CustomEvent("chat-tool-call-parity-ready", { detail: { state } }),
    );
    return true;
  };

  let attempts = 0;
  const drive = () => {
    attempts += 1;
    const dcRootName = globalThis.__dcRootName?.();
    if (
      typeof globalThis.__dcSetProps !== "function" ||
      typeof dcRootName !== "string" ||
      !first(".mw")
    ) {
      if (attempts < 300) return globalThis.setTimeout(drive, 20);
      throw new Error(
        "Design Compiler runtime did not mount the chat/tool-call walkthrough.",
      );
    }

    // `prepare-chat-tool-call-shell-reference.mjs` changed the *default prop*
    // to false before this class mounted. Calling __dcSetProps here would force
    // a compiler re-render while anchors are being attached, so do not mutate a
    // second time; validate the settled source state instead.
    globalThis.requestAnimationFrame(() => {
      const title = first(".mw-title");
      if (!(title instanceof Element)) {
        if (attempts < 300) return globalThis.setTimeout(drive, 20);
        throw new Error(`Reference title did not mount for ${state}.`);
      }
      const modeLabel = text(title);
      if (!modeLabel.includes(spec.mode === "focus" ? "Focus" : "Studio")) {
        if (attempts < 300) return globalThis.setTimeout(drive, 20);
        throw new Error(
          `Reference mode did not settle to ${spec.mode} for ${state}.`,
        );
      }
      globalThis.requestAnimationFrame(() => {
        globalThis.requestAnimationFrame(() => {
          if (!anchorRenderedDom() && attempts < 300) {
            globalThis.setTimeout(drive, 20);
          }
        });
      });
    });
  };

  drive();
})();
