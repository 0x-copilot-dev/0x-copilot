// SCRATCH AUDIT HARNESS — delete after the audit. Not a product test.
import { parseBlocks } from "@0x-copilot/chat-surface";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
import type { ArtifactRenderState, ArtifactRevisionSaveOutcome } from "./model";

function documentState(source: string, editor: unknown): ArtifactRenderState {
  return {
    artifactId: "art_doc",
    kind: "document",
    title: "t",
    mediaType: "text/markdown",
    filename: "a.md",
    revision: 3,
    digest: "d".repeat(64),
    byteSize: source.length,
    author: "model",
    createdAt: "2026-08-06T00:00:00Z",
    preview: "ready",
    text: source,
    ...(editor === undefined ? {} : { documentEditor: editor }),
  } as ArtifactRenderState;
}

function renderEditable(
  source: string,
  outcome: ArtifactRevisionSaveOutcome = "saved",
): { readonly saveRevision: ReturnType<typeof vi.fn> } {
  const saveRevision = vi.fn(async (_s: string) => outcome);
  render(
    <DocumentArtifactRenderer
      artifact={documentState(source, { disabled: false, saveRevision })}
    />,
  );
  return { saveRevision };
}

async function saveAnd(
  saveRevision: ReturnType<typeof vi.fn>,
): Promise<string> {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));
  return (saveRevision.mock.calls[0] as [string])[0];
}

describe("AUDIT: G1 single-construct documents through the real renderer", () => {
  const CASES: [string, string, string, string][] = [
    // name, source, testid, replacement
    [
      "checklist-only",
      "- [ ] one\n- [x] two\n",
      "doc-raw-0",
      "- [x] one\n- [x] two",
    ],
    [
      "fence-only",
      "```js\nconst a = 1;\n```\n",
      "doc-raw-0",
      "```js\nconst a = 2;\n```",
    ],
    ["quote-only", "> quoted\n> lines\n", "doc-raw-0", "> quoted\n> edited"],
    ["html-only", "<div>\nhello\n</div>\n", "doc-raw-0", "<div>\nbye\n</div>"],
    [
      "indented-code-only",
      "    code\n    more\n",
      "doc-raw-0",
      "    code\n    edited",
    ],
    ["rule-only", "***\n", "doc-raw-0", "---"],
    ["setext-only", "Title\n===\n", "doc-raw-0", "Edited\n==="],
  ];

  for (const [name, source, testId, replacement] of CASES) {
    it(`${name}: gets a field, splices only its span`, async () => {
      const { saveRevision } = renderEditable(source);
      const node = screen.queryByTestId(testId);
      expect(node, `${name}: no editable node`).not.toBeNull();
      // Does the read node paint ANYTHING a reader could aim at?
      // eslint-disable-next-line no-console
      console.log(
        `  [${name}] read node innerHTML=${JSON.stringify(node!.innerHTML)} textContent=${JSON.stringify(node!.textContent)} tabindex=${node!.getAttribute("tabindex")}`,
      );
      fireEvent.click(node!);
      const field = screen.getByTestId(`${testId}-input`);
      const block = parseBlocks(source).find(
        (b) => b.kind === "raw" && b.reason !== "blank",
      )!;
      expect((field as HTMLTextAreaElement).value).toBe(
        (block as { text: string }).text,
      );
      fireEvent.change(field, { target: { value: replacement } });
      fireEvent.keyDown(field, { key: "Enter", metaKey: true });
      const saved = await saveAnd(saveRevision);
      const expected =
        source.slice(0, block.start === undefined ? 0 : 0) +
        source.slice(0, (block as { textStart: number }).textStart) +
        replacement +
        source.slice((block as { textEnd: number }).textEnd);
      expect(saved, `${name}: not a pure splice`).toBe(expected);
      // eslint-disable-next-line no-console
      console.log(`  [${name}] saved=${JSON.stringify(saved)}`);
    });
  }
});

describe("AUDIT: raw block with a trailing blank line + a next block", () => {
  const CASES: [string, string, string][] = [
    ["list", "- one\n- two\n\nProse.\n", "- one\n- two\n- three"],
    ["fence", "```js\nx\n```\n\nProse.\n", "```js\nx\ny\n```"],
    ["quote", "> a\n\nProse.\n", "> a\n> b"],
    ["html", "<div>\nx\n</div>\n\nProse.\n", "<div>\ny\n</div>"],
    ["indented", "    a\n\nProse.\n", "    a\n    b"],
  ];
  for (const [name, source, replacement] of CASES) {
    it(`${name}: keeps the absorbed blank line`, async () => {
      const { saveRevision } = renderEditable(source);
      fireEvent.click(screen.getByTestId("doc-raw-0"));
      fireEvent.change(screen.getByTestId("doc-raw-0-input"), {
        target: { value: replacement },
      });
      fireEvent.keyDown(screen.getByTestId("doc-raw-0-input"), {
        key: "Enter",
        metaKey: true,
      });
      const saved = await saveAnd(saveRevision);
      expect(
        saved.endsWith("\n\nProse.\n"),
        `${name}: welded — ${JSON.stringify(saved)}`,
      ).toBe(true);
    });
  }
});

describe("AUDIT: CRLF documents through the real textarea", () => {
  it("reports what the field hands back for a CRLF fence", async () => {
    const source = "```js\r\nconst a = 1;\r\n```\r\n\r\nProse.\r\n";
    const { saveRevision } = renderEditable(source);
    fireEvent.click(screen.getByTestId("doc-raw-0"));
    const field = screen.getByTestId("doc-raw-0-input") as HTMLTextAreaElement;
    // eslint-disable-next-line no-console
    console.log(`  [crlf] field.value=${JSON.stringify(field.value)}`);
    // What a real browser hands back from a textarea is the API value: CRLF
    // normalized to LF. Simulate a single-character keystroke faithfully.
    const asBrowser = field.value.replace(/\r\n|\r/g, "\n");
    fireEvent.change(field, { target: { value: asBrowser + "\n// x" } });
    fireEvent.keyDown(field, { key: "Enter", metaKey: true });
    const saved = await saveAnd(saveRevision);
    // eslint-disable-next-line no-console
    console.log(`  [crlf] saved=${JSON.stringify(saved)}`);
    // eslint-disable-next-line no-console
    console.log(
      `  [crlf] CR count before=${(source.match(/\r/g) ?? []).length} after=${(saved.match(/\r/g) ?? []).length}`,
    );
  });

  it("reports whether the no-op guard survives a CRLF round trip", () => {
    const source = "```js\r\nconst a = 1;\r\n```\r\n";
    renderEditable(source);
    fireEvent.click(screen.getByTestId("doc-raw-0"));
    const field = screen.getByTestId("doc-raw-0-input") as HTMLTextAreaElement;
    fireEvent.change(field, {
      target: { value: field.value.replace(/\r\n|\r/g, "\n") },
    });
    // eslint-disable-next-line no-console
    console.log(
      `  [crlf-noop] dirty=${screen.getByTestId("doc-editor").getAttribute("data-dirty")}`,
    );
  });
});

describe("AUDIT: clearing a block", () => {
  for (const [name, source, testId] of [
    ["paragraph", "Prose here.\n\nTail.\n", "doc-block-0"],
    ["list", "- one\n- two\n\nTail.\n", "doc-raw-0"],
    ["fence", "```js\nx\n```\n\nTail.\n", "doc-raw-0"],
  ] as [string, string, string][]) {
    it(`${name}: what the read node becomes when cleared`, () => {
      renderEditable(source);
      fireEvent.click(screen.getByTestId(testId));
      const field = screen.getByTestId(`${testId}-input`);
      fireEvent.change(field, { target: { value: "" } });
      fireEvent.keyDown(field, { key: "Enter", metaKey: true });
      const node = screen.queryByTestId(testId);
      // eslint-disable-next-line no-console
      console.log(
        `  [clear-${name}] node=${node === null ? "ABSENT" : JSON.stringify(node.outerHTML)}`,
      );
      expect(screen.getByTestId("doc-editor")).toHaveAttribute(
        "data-dirty",
        "1",
      );
    });
  }
});

describe("AUDIT: heading with no text, through the renderer", () => {
  it("renders a clickable heading and splices a separator", async () => {
    const source = "######\n\nBody.\n";
    const { saveRevision } = renderEditable(source);
    const node = screen.getByTestId("doc-block-0");
    // eslint-disable-next-line no-console
    console.log(
      `  [empty-heading] outerHTML=${JSON.stringify(node.parentElement?.outerHTML)}`,
    );
    fireEvent.click(node);
    const field = screen.getByTestId("doc-block-0-input");
    expect((field as HTMLInputElement).value).toBe("");
    fireEvent.change(field, { target: { value: "Summary" } });
    fireEvent.keyDown(field, { key: "Enter" });
    const saved = await saveAnd(saveRevision);
    // eslint-disable-next-line no-console
    console.log(`  [empty-heading] saved=${JSON.stringify(saved)}`);
    expect(saved).toBe("###### Summary\n\nBody.\n");
    expect(parseBlocks(saved)[0].kind).toBe("heading");
  });

  it("survives a closing run", async () => {
    const source = "# ###\n\nBody.\n";
    const { saveRevision } = renderEditable(source);
    fireEvent.click(screen.getByTestId("doc-block-0"));
    fireEvent.change(screen.getByTestId("doc-block-0-input"), {
      target: { value: "Summary" },
    });
    fireEvent.keyDown(screen.getByTestId("doc-block-0-input"), {
      key: "Enter",
    });
    const saved = await saveAnd(saveRevision);
    // eslint-disable-next-line no-console
    console.log(`  [closing-run] saved=${JSON.stringify(saved)}`);
    expect(saved).toBe("# Summary ###\n\nBody.\n");
  });

  it("a text-less heading typed then cleared is not left dirty with a stray space", async () => {
    const source = "######\n\nBody.\n";
    const { saveRevision } = renderEditable(source);
    fireEvent.click(screen.getByTestId("doc-block-0"));
    const field = screen.getByTestId("doc-block-0-input");
    fireEvent.change(field, { target: { value: "Summary" } });
    fireEvent.change(field, { target: { value: "" } });
    // eslint-disable-next-line no-console
    console.log(
      `  [empty-heading-clear] dirty=${screen.getByTestId("doc-editor").getAttribute("data-dirty")}`,
    );
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");
    expect(saveRevision).not.toHaveBeenCalled();
  });
});

describe("AUDIT: everything editable at once", () => {
  it("edits every span the renderer offers and never throws to the user", async () => {
    const source = [
      "# Title",
      "",
      "Prose one.",
      "",
      "| A | B |",
      "| --- | --- |",
      "| 1 | 2 |",
      "",
      "- a",
      "- b",
      "",
      "> quote",
      "",
      "```sh",
      "run",
      "```",
      "",
      "Tail.",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);
    // The D28 ban is on PRODUCT code reaching a browser primitive; this file
    // declares itself a deletable scratch harness and sweeps the rendered DOM.
    // eslint-disable-next-line no-restricted-globals
    const ids = Array.from(document.querySelectorAll("[data-testid]"))
      .map((n) => n.getAttribute("data-testid")!)
      .filter(
        (id) =>
          (id.startsWith("doc-block-") ||
            id.startsWith("doc-raw-") ||
            id.startsWith("doc-cell-") ||
            id.startsWith("doc-header-")) &&
          !id.endsWith("-input"),
      );
    // eslint-disable-next-line no-console
    console.log(`  [all] editable ids=${JSON.stringify(ids)}`);
    for (const id of ids) {
      fireEvent.click(screen.getByTestId(id));
      const field = screen.getByTestId(`${id}-input`);
      fireEvent.change(field, { target: { value: `Z-${id}` } });
      fireEvent.keyDown(field, { key: "Enter", metaKey: true });
    }
    expect(screen.getByTestId("doc-editor")).toHaveAttribute(
      "data-dirty",
      String(ids.length),
    );
    const saved = await saveAnd(saveRevision);
    // eslint-disable-next-line no-console
    console.log(`  [all] saved=${JSON.stringify(saved)}`);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
