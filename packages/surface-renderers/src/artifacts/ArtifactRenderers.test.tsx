import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeArtifactRenderer } from "./CodeArtifactRenderer";
import { DatasetArtifactRenderer, parseCsv } from "./DatasetArtifactRenderer";
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
  it("preserves RFC4180 CSV fidelity including BOM, CRLF, quotes, empty cells, Unicode and formula-like cells", () => {
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
        "Formula-like cells are shown as text and are never evaluated.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("=2+2")).toBeInTheDocument();
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
