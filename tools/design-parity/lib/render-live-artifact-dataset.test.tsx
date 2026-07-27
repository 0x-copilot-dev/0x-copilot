/* design-parity · live artifact dataset editor render
 *
 * B2 has no matching table/editor mock in the supplied Chat & Tool Calls
 * design source. This produces the deterministic live side and its stable
 * anchors so a design baseline can be plugged in without changing production
 * renderer markup. See surfaces/artifact-dataset/SOURCE-GAP.md.
 */
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { ArtifactFrame } from "../../../packages/chat-surface/src/artifacts/ArtifactFrame";
import { ArtifactRevisionHistory } from "../../../packages/chat-surface/src/artifacts/ArtifactRevisionHistory";
import type { Transport } from "../../../packages/chat-transport/src";
import { DatasetArtifactRenderer } from "../../../packages/surface-renderers/src/artifacts/DatasetArtifactRenderer";
import type { ArtifactRenderState } from "../../../packages/surface-renderers/src/artifacts/model";

const HERE = (path: string): string =>
  fileURLToPath(new URL(path, import.meta.url));
const REPO = (path: string): string => HERE("../../../" + path);
const LIVE = (path: string): string =>
  HERE("../surfaces/artifact-dataset/live/" + path);

const text =
  "name,owner,status\n" +
  "Quarterly forecast,Ada,review\n" +
  "<img src=x onerror=globalThis.pwned>,Sam,blocked\n" +
  "Regional forecast,Lee,ready\n";

const artifact = {
  artifactId: "artifact_parity_dataset",
  kind: "dataset",
  title: "forecast.csv",
  mediaType: "text/csv",
  filename: "forecast.csv",
  revision: 7,
  digest: "a".repeat(64),
  byteSize: text.length,
  author: "Ada",
  createdAt: "2026-07-26T00:00:00Z",
  preview: "ready",
  text,
  datasetEditor: {
    disabled: false,
    saveRevision: async () => "saved" as const,
  },
} as unknown as ArtifactRenderState;

const transport = {
  getArtifactContent: async () => {
    throw new Error("not invoked by the static parity fixture");
  },
  createArtifactRevision: async () => {
    throw new Error("not invoked by the static parity fixture");
  },
} as unknown as Transport;

const downloadPort = {
  saveArtifact: async () => undefined,
};

function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · artifact dataset · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      #artifact-dataset { box-sizing: border-box; display: flex; height: 620px; max-width: 960px; padding: 22px; }
    </style>
  </head>
  <body><main id="artifact-dataset">${inner}</main></body>
</html>`;
}

it("renders a deterministic fixed dataset editor target", () => {
  mkdirSync(LIVE(""), { recursive: true });
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );
  const { container } = render(
    <ArtifactFrame
      artifact={artifact}
      status="ready"
      transport={transport}
      downloadPort={downloadPort}
    >
      <DatasetArtifactRenderer artifact={artifact} />
      <ArtifactRevisionHistory
        revisions={[
          {
            revision: 7,
            author: "Ada",
            created_at: "2026-07-26T00:00:00Z",
            byte_size: text.length,
            content_digest: "a".repeat(64),
            source_ref: "artifact://fixture",
          },
        ]}
        activeRevision={7}
        latestRevision={7}
        onSelect={() => undefined}
        onCompareToCurrent={() => undefined}
        onRestore={() => undefined}
        restoreDisabled={false}
        hasOlderHistory={false}
        onLoadOlder={() => undefined}
      />
    </ArtifactFrame>,
  );
  fireEvent.change(screen.getByLabelText("owner, row 2"), {
    target: { value: "Avery" },
  });
  const markup = container.innerHTML;

  expect(markup).toContain('data-testid="artifact-frame"');
  expect(markup).toContain('data-testid="artifact-dataset-renderer"');
  expect(markup).toContain('data-testid="dataset-virtual-window"');
  expect(container.querySelectorAll("img")).toHaveLength(0);
  writeFileSync(LIVE("editor.html"), shell(markup));
});
