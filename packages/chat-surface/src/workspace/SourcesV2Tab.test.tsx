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
    // The compact list uppercases its eyebrow label.
    expect(screen.getByText("ARTIFACTS · 1")).toBeInTheDocument();
    expect(screen.getByText("LINEAR · 1")).toBeInTheDocument();
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

    const rows = screen.getAllByTestId("sources-v2-row");
    expect(rows).toHaveLength(2);
    // Openability is a row attribute now, not a trailing glyph — only the
    // artifact fact is owner-routed openable.
    expect(
      rows.filter((r) => r.getAttribute("data-openable") === "true"),
    ).toHaveLength(1);
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

// ── citationsSlot: cited documents compose with ledger facts ─────────────────
//
// The v2 fold only knows ledger events, so a run whose sources came from a
// citing tool (web_search) produced a correct citation registry AND an empty
// Sources panel. These pin the composition that fixes it.

describe("SourcesV2Tab — citationsSlot", () => {
  const CITED = <p data-testid="cited-rows">cited rows</p>;

  it("renders cited documents even when the ledger fold is empty", () => {
    // THE BUG: web_search registers sources but emits no `read.executed`, so
    // `presentSourcesV2` totals zero. The panel must not claim there is nothing.
    render(
      <SourcesV2Tab
        sources={{ v: 2, run_id: "run-1", latest_sequence_no: 0, facts: [] }}
        citationsSlot={CITED}
      />,
    );
    expect(screen.queryByTestId("sources-v2-empty")).toBeNull();
    expect(screen.getByTestId("sources-v2-citations")).toBeInTheDocument();
    expect(screen.getByTestId("cited-rows")).toBeInTheDocument();
  });

  it("keeps the no-sources empty state when there is nothing at all", () => {
    render(
      <SourcesV2Tab
        sources={{ v: 2, run_id: "run-1", latest_sequence_no: 0, facts: [] }}
      />,
    );
    expect(screen.getByTestId("sources-v2-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("sources-v2-citations")).toBeNull();
  });

  it("omits the Cited section entirely when no slot is supplied", () => {
    // No empty "Cited" header over nothing.
    render(<SourcesV2Tab sources={sources} />);
    expect(screen.queryByTestId("sources-v2-citations")).toBeNull();
    expect(screen.getByTestId("sources-v2-tab")).toBeInTheDocument();
  });
});

describe("SourcesV2Tab — fact rows use the compact source card", () => {
  it("renders v2 fact rows as compact list rows, not tall cards", () => {
    // One Sources rail, one row language: the same dense card that used to sit
    // under each web_search tool call. The previous `.atlas-source-row` cards
    // stacked a badge row, a snippet and a footnote per source and ate the panel.
    render(<SourcesV2Tab sources={sources} />);
    const row = screen.getAllByTestId("sources-v2-row")[0];
    expect(row).not.toHaveClass("atlas-source-row");
    expect(row.getAttribute("role")).toBe("listitem");
  });
});
