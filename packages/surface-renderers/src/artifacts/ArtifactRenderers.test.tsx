import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CodeArtifactRenderer } from "./CodeArtifactRenderer";
import {
  DATASET_DOM_ROW_BUDGET,
  DATASET_PREVIEW_ROW_BUDGET,
  DatasetArtifactRenderer,
  parseCsv,
  parseLosslessDelimited,
  serializeDelimitedPatch,
  serializeFormulaSafeDelimitedPatch,
} from "./DatasetArtifactRenderer";
import { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
import type { ArtifactRenderState } from "./model";

function artifact(
  kind: ArtifactRenderState["kind"],
  text: string,
): ArtifactRenderState {
  return {
    artifactId: "artifact_test",
    kind,
    title: "Test artifact",
    mediaType: "text/plain",
    filename: "test.txt",
    revision: 1,
    digest: "a".repeat(64),
    byteSize: text.length,
    author: "user",
    createdAt: "2026-01-01T00:00:00Z",
    preview: "ready",
    text,
  };
}

describe("fixed artifact renderers", () => {
  it("round-trips the CSV corpus byte-for-byte and changes only the intended lossless cell", () => {
    const source =
      '\ufeffname,note,amount,amount\r\nAda,"hello, world",=1+1,\r\n李,,"line 1\r\nline 2",';
    const lossless = parseLosslessDelimited(source);
    expect(lossless.roundTripSafe).toBe(true);
    expect(serializeDelimitedPatch(lossless, {})).toBe(source);
    expect(serializeDelimitedPatch(lossless, { "1:1": "changed, note" })).toBe(
      '\ufeffname,note,amount,amount\r\nAda,"changed, note",=1+1,\r\n李,,"line 1\r\nline 2",',
    );
    expect(serializeFormulaSafeDelimitedPatch(lossless, {})).toBe(
      '\ufeffname,note,amount,amount\r\nAda,"hello, world",\'=1+1,\r\n李,,"line 1\r\nline 2",',
    );

    const lf = parseLosslessDelimited('name,note\nAda,"comma, retained"\n');
    expect(serializeDelimitedPatch(lf, {})).toBe(
      'name,note\nAda,"comma, retained"\n',
    );

    const parsed = parseCsv(
      '\ufeffname,note,amount\r\nAda,"hello, world",=1+1\r\n李,,"line 1\r\nline 2"',
    );
    expect(parsed.rows).toEqual([
      ["name", "note", "amount"],
      ["Ada", "hello, world", "=1+1"],
      ["李", "", "line 1\r\nline 2"],
    ]);
    expect(parsed.formulaCells).toBe(1);
    render(
      <DatasetArtifactRenderer artifact={artifact("dataset", "a,b\n1,=2+2")} />,
    );
    expect(
      screen.getByText(
        /Formula-like cells are shown as text and are never evaluated/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("=2+2")).toBeInTheDocument();
  });

  it("holds CSV cell changes in memory, saves a complete lossless revision, and gates formula-safe export", async () => {
    const saveRevision = vi.fn(async () => "saved" as const);
    const source = "\ufeffname,amount\r\nAda,=1+1\r\n";
    const editable = {
      ...artifact("dataset", source),
      mediaType: "text/csv",
      datasetEditor: { disabled: false, saveRevision },
    } as unknown as ArtifactRenderState;
    const { container } = render(
      <DatasetArtifactRenderer artifact={editable} />,
    );

    expect(
      screen.getByRole("grid", { name: "Dataset cell editor" }),
    ).toHaveAttribute("aria-describedby", "dataset-cell-editor-help");
    expect(screen.getByTestId("artifact-dataset-renderer")).toHaveClass(
      "ui-dataset-surface",
    );
    expect(container.querySelector(".ui-card")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save patched revision" }),
    ).toHaveClass("ui-button--primary", "ui-button--sm");
    expect(
      screen.getByRole("button", { name: "Save patched revision" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("amount, row 2"), {
      target: { value: "3" },
    });
    expect(
      screen.getByRole("button", { name: "Save patched revision" }),
    ).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Save patched revision" }),
    );
    await waitFor(() =>
      expect(saveRevision).toHaveBeenCalledWith(
        "\ufeffname,amount\r\nAda,3\r\n",
      ),
    );

    fireEvent.change(screen.getByLabelText("amount, row 2"), {
      target: { value: "=1+1" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create formula-safe revision" }),
    );
    const confirmation = screen.getByRole("alert");
    expect(confirmation).toHaveTextContent("Formula-like cells");
    fireEvent.click(
      within(confirmation).getByRole("button", {
        name: "Create formula-safe revision",
      }),
    );
    await waitFor(() =>
      expect(saveRevision).toHaveBeenLastCalledWith(
        "\ufeffname,amount\r\nAda,'=1+1\r\n",
      ),
    );
  });

  it("disables cell editing and gives a visible fidelity warning for malformed CSV", () => {
    const editable = {
      ...artifact("dataset", 'name,note\nAda,"unterminated'),
      mediaType: "text/csv",
      datasetEditor: { disabled: false, saveRevision: vi.fn() },
    } as unknown as ArtifactRenderState;
    render(<DatasetArtifactRenderer artifact={editable} />);
    expect(screen.getByText(/malformed/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save patched revision" }),
    ).toBeDisabled();
  });

  it("renders TSV and bounded JSON object datasets without treating values as UI", () => {
    const tsv = artifact("dataset", "name\tformula\r\nAda\t=1+1");
    render(
      <DatasetArtifactRenderer
        artifact={{ ...tsv, mediaType: "text/tab-separated-values" }}
      />,
    );
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("=1+1")).toBeInTheDocument();

    const json = artifact(
      "dataset",
      '[{"name":"Ada","note":"<script>nope</script>"}]',
    );
    render(
      <DatasetArtifactRenderer
        artifact={{ ...json, mediaType: "application/json" }}
      />,
    );
    expect(screen.getByText("<script>nope</script>")).toBeInTheDocument();
  });

  it("windows large dataset rows instead of creating an unbounded table DOM", () => {
    const rows = Array.from({ length: 101 }, (_, index) => `row-${index}`);
    render(
      <DatasetArtifactRenderer
        artifact={artifact("dataset", `name\n${rows.join("\n")}`)}
      />,
    );
    expect(screen.getByText("row-0")).toBeInTheDocument();
    expect(screen.queryByText("row-100")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next rows" }));
    expect(screen.getByText("row-100")).toBeInTheDocument();
    expect(screen.getByText("Showing rows 101–101 of 101")).toBeInTheDocument();
  });

  it("keeps a 100k-row CSV within the explicit DOM budget while sorting, filtering, editing, and saving one complete inert revision", async () => {
    const saveRevision = vi.fn(async (_source: string) => "saved" as const);
    const untrusted = "<img src=x onerror=globalThis.pwned>";
    const source =
      "name,rank,note\n" +
      Array.from(
        { length: DATASET_PREVIEW_ROW_BUDGET },
        (_, index) =>
          `row-${index},${DATASET_PREVIEW_ROW_BUDGET - index},${index === 0 ? untrusted : "plain"}`,
      ).join("\n");
    const editable = {
      ...artifact("dataset", source),
      mediaType: "text/csv",
      datasetEditor: { disabled: false, saveRevision },
    } as unknown as ArtifactRenderState;
    const { container } = render(
      <DatasetArtifactRenderer artifact={editable} />,
    );

    // B2 performance contract: data parsing is capped at 100k rows, while a
    // virtual window mounts no more than 100 body rows. This is deterministic
    // and avoids flaky elapsed-time checks in jsdom/CI.
    const virtualWindow = screen.getByTestId("dataset-virtual-window");
    expect(DATASET_PREVIEW_ROW_BUDGET).toBe(100_000);
    expect(DATASET_DOM_ROW_BUDGET).toBe(100);
    expect(virtualWindow).toHaveAttribute("data-dom-row-budget", "100");
    expect(virtualWindow).toHaveAttribute("data-window-row-count", "100");
    expect(container.querySelectorAll("tbody tr")).toHaveLength(
      DATASET_DOM_ROW_BUDGET,
    );
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(screen.getByDisplayValue(untrusted)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next rows" }));
    expect(virtualWindow).toHaveAttribute("data-window-start", "100");
    expect(screen.queryByLabelText("name, row 2")).toBeNull();
    expect(screen.getByLabelText("name, row 102")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter dataset rows"), {
      target: { value: untrusted },
    });
    expect(virtualWindow).toHaveAttribute("data-window-row-count", "1");
    expect(container.querySelectorAll("img")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Filter dataset rows"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Sort dataset rows"), {
      target: { value: "1" },
    });
    expect(screen.getByLabelText("name, row 100001")).toHaveValue("row-99999");
    fireEvent.change(screen.getByLabelText("Filter dataset rows"), {
      target: { value: "row-99999" },
    });
    fireEvent.change(screen.getByLabelText("name, row 100001"), {
      target: { value: "patched-row" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save patched revision" }),
    );
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const saved = saveRevision.mock.calls[0]?.[0];
    expect(saved).toContain("patched-row,1,plain");
    expect(saved).toContain(`row-0,100000,${untrusted}`);
    expect(saved.split("\n")).toHaveLength(DATASET_PREVIEW_ROW_BUDGET + 1);
  });

  it("drops raw document HTML rather than creating a DOM node", () => {
    const { container } = render(
      <DocumentArtifactRenderer
        artifact={artifact(
          "document",
          "# Notes\n<script>window.pwned = true</script>",
        )}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container).not.toHaveTextContent("window.pwned");
  });

  it("renders source as text and windows a very long source without executing it", () => {
    const source = Array.from(
      { length: 1_001 },
      (_, index) => `line ${index}`,
    ).join("\n");
    render(<CodeArtifactRenderer artifact={artifact("code", source)} />);
    expect(screen.getByTestId("artifact-code-renderer")).toHaveAttribute(
      "data-windowed",
      "true",
    );
    expect(
      screen.getByText(/Showing the first 1,000 lines/),
    ).toBeInTheDocument();
    const viewer = screen.getByLabelText("test.txt source");
    expect(viewer).toHaveTextContent("line 0");
    expect(viewer).not.toHaveTextContent("line 1000");
    expect(screen.getByRole("button", { name: "Next lines" })).toBeEnabled();
    expect(screen.getByLabelText("Source viewer controls")).toBeInTheDocument();
  });
});
