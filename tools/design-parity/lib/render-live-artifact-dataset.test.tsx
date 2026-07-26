/* design-parity · live artifact dataset editor render
 *
 * B2 has no matching table/editor mock in the supplied Chat & Tool Calls
 * design source. This produces the deterministic live side and its stable
 * anchors so a design baseline can be plugged in without changing production
 * renderer markup. See surfaces/artifact-dataset/SOURCE-GAP.md.
 */
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

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

function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · artifact dataset · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      #artifact-dataset { box-sizing: border-box; max-width: 960px; padding: 24px; }
      #artifact-dataset .ui-card { max-width: 912px; }
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
  const markup = renderToStaticMarkup(h(DatasetArtifactRenderer, { artifact }));

  expect(markup).toContain('data-testid="artifact-dataset-renderer"');
  expect(markup).toContain('data-testid="dataset-virtual-window"');
  expect(markup).not.toContain("<img");
  writeFileSync(LIVE("editor.html"), shell(markup));
});
