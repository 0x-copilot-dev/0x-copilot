import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SourcesProjectionV2 } from "@0x-copilot/api-types";

import { SourcesV2Tab } from "./SourcesV2Tab";

const sources: SourcesProjectionV2 = {
  v: 2,
  run_id: "run_sources_v2_01",
  latest_sequence_no: 4,
  facts: [
    {
      source_id: "source:v2:004:artifact",
      kind: "artifact",
      sequence_no: 4,
      ledger_id: "rabc·004",
      connector: null,
      tool: null,
      origin: null,
      artifact_id: "art_sources_v2_01",
      artifact_revision: 2,
      artifact_source_ref: "artifact://art_sources_v2_01/revisions/2",
      workspace_grant_label: null,
      workspace_virtual_path_key: null,
      browser_origin: null,
      sandbox_operation: null,
      subagent_task: null,
      external_receipt_ref: null,
    },
    {
      source_id: "source:v2:003:connector",
      kind: "connector",
      sequence_no: 3,
      ledger_id: "rabc·003",
      connector: "linear",
      tool: "get_issue",
      origin: "https://linear.app",
      artifact_id: null,
      artifact_revision: null,
      artifact_source_ref: null,
      workspace_grant_label: null,
      workspace_virtual_path_key: null,
      browser_origin: null,
      sandbox_operation: null,
      subagent_task: null,
      external_receipt_ref: null,
    },
  ],
};

describe("SourcesV2Tab", () => {
  it("renders the designed grouped source rows and sends only an opaque source id", () => {
    const onOpenSource = vi.fn();
    render(<SourcesV2Tab sources={sources} onOpenSource={onOpenSource} />);

    expect(
      screen.getByText(
        "Everything the agent read or fetched this run — the receipts behind each surface.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Artifacts · 1")).toBeInTheDocument();
    expect(screen.getByText("Linear · 1")).toBeInTheDocument();
    expect(screen.getByText("Generated Artifact")).toBeInTheDocument();
    expect(screen.getByText("Get issue")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Open Generated Artifact"));
    expect(onOpenSource).toHaveBeenCalledWith("source:v2:004:artifact");
  });

  it("does not render refs, paths, raw body, cookie, or provider token fields", () => {
    render(<SourcesV2Tab sources={sources} />);

    const rendered = screen.getByTestId("sources-v2-tab").textContent ?? "";
    for (const forbidden of [
      "artifact://art_sources_v2_01/revisions/2",
      "art_sources_v2_01",
      "rabc·004",
      "linear.app",
      "cookie",
      "provider",
    ]) {
      expect(rendered).not.toContain(forbidden);
    }
  });

  it("keeps non-artifact provenance honestly non-openable", () => {
    render(<SourcesV2Tab sources={sources} onOpenSource={vi.fn()} />);

    expect(screen.getAllByTestId("sources-v2-row")).toHaveLength(2);
    expect(screen.getAllByTestId("sources-v2-open-artifact")).toHaveLength(1);
  });

  it("renders the supplied v3 empty-state contract", () => {
    render(
      <SourcesV2Tab
        sources={{ ...sources, facts: [] }}
        onOpenSource={vi.fn()}
      />,
    );

    expect(
      screen.getByText("No sources yet — the run hasn't read anything."),
    ).toBeInTheDocument();
  });
});
