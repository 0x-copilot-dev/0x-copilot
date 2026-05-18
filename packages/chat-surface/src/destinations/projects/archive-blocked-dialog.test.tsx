import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ArchiveBlockedDialog,
  type LivenessReport,
} from "./archive-blocked-dialog";

function makeReport(overrides: Partial<LivenessReport> = {}): LivenessReport {
  const base: LivenessReport = {
    project_id: "proj_abc",
    tenant_id: "tenant_acme",
    is_alive: true,
    active_runs: 2,
    pending_approvals: 1,
    active_routines: 3,
    in_flight_inbox: 5,
    details: [
      {
        source: "ai_backend.runs",
        count: 2,
        is_alive: true,
        error: null,
        fetched_at: "2026-05-17T12:00:00Z",
      },
      {
        source: "ai_backend.approvals",
        count: 1,
        is_alive: true,
        error: null,
        fetched_at: "2026-05-17T12:00:00Z",
      },
      {
        source: "backend.routines",
        count: 3,
        is_alive: true,
        error: null,
        fetched_at: "2026-05-17T12:00:00Z",
      },
      {
        source: "backend.inbox",
        count: 5,
        is_alive: true,
        error: null,
        fetched_at: "2026-05-17T12:00:00Z",
      },
    ],
    computed_at: "2026-05-17T12:00:00Z",
    cache_hit: false,
  };
  return { ...base, ...overrides };
}

describe("ArchiveBlockedDialog", () => {
  it("renders nothing when closed", () => {
    render(
      <ArchiveBlockedDialog
        open={false}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    expect(
      screen.queryByTestId("archive-blocked-dialog"),
    ).not.toBeInTheDocument();
  });

  it("renders title with project name and headline counts", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute(
      "aria-labelledby",
      "archive-blocked-title",
    );
    expect(screen.getByText(/Acme Renewal/)).toBeInTheDocument();
    expect(screen.getByTestId("archive-blocked-headline")).toHaveTextContent(
      "2 active runs / 1 pending approval / 3 active routines / 5 in-flight inbox items active",
    );
  });

  it("renders one breakdown chip per component with the count", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    expect(screen.getByTestId("archive-blocked-breakdown")).toBeInTheDocument();
    expect(
      screen.getByTestId("filter-tab-count-active_runs"),
    ).toHaveTextContent("2");
    expect(
      screen.getByTestId("filter-tab-count-pending_approvals"),
    ).toHaveTextContent("1");
    expect(
      screen.getByTestId("filter-tab-count-active_routines"),
    ).toHaveTextContent("3");
    expect(
      screen.getByTestId("filter-tab-count-in_flight_inbox"),
    ).toHaveTextContent("5");
  });

  it("singularises chip labels when the count is exactly 1", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({
          active_runs: 1,
          pending_approvals: 1,
          active_routines: 1,
          in_flight_inbox: 1,
        })}
      />,
    );
    expect(screen.getByTestId("filter-tab-active_runs")).toHaveTextContent(
      "active run",
    );
    expect(
      screen.getByTestId("filter-tab-pending_approvals"),
    ).toHaveTextContent("pending approval");
    expect(screen.getByTestId("filter-tab-active_routines")).toHaveTextContent(
      "active routine",
    );
    expect(screen.getByTestId("filter-tab-in_flight_inbox")).toHaveTextContent(
      "in-flight inbox item",
    );
  });

  it("shows a partial-failure StatusPill when any detail row has an error", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({
          details: [
            {
              source: "ai_backend.runs",
              count: 0,
              is_alive: false,
              error: "upstream timeout",
              fetched_at: "2026-05-17T12:00:00Z",
            },
            {
              source: "ai_backend.approvals",
              count: 1,
              is_alive: true,
              error: null,
              fetched_at: "2026-05-17T12:00:00Z",
            },
            {
              source: "backend.routines",
              count: 3,
              is_alive: true,
              error: null,
              fetched_at: "2026-05-17T12:00:00Z",
            },
            {
              source: "backend.inbox",
              count: 5,
              is_alive: true,
              error: null,
              fetched_at: "2026-05-17T12:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(
      screen.getByTestId("archive-blocked-partial-failure"),
    ).toBeInTheDocument();
  });

  it("omits the partial-failure pill when all detail rows succeeded", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    expect(
      screen.queryByTestId("archive-blocked-partial-failure"),
    ).not.toBeInTheDocument();
  });

  it("renders the View active runs button only when the host wires it AND active_runs > 0", () => {
    const onView = vi.fn();
    const { rerender } = render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({ active_runs: 0 })}
        onViewActiveRuns={onView}
      />,
    );
    // Wired but no active runs → hide the button.
    expect(
      screen.queryByTestId("archive-blocked-view-runs"),
    ).not.toBeInTheDocument();

    rerender(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({ active_runs: 2 })}
        onViewActiveRuns={onView}
      />,
    );
    fireEvent.click(screen.getByTestId("archive-blocked-view-runs"));
    expect(onView).toHaveBeenCalledTimes(1);
  });

  it("never renders the View active runs button when the host did not wire it", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({ active_runs: 2 })}
      />,
    );
    expect(
      screen.queryByTestId("archive-blocked-view-runs"),
    ).not.toBeInTheDocument();
  });

  it("Cancel button closes the dialog", () => {
    const onClose = vi.fn();
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={onClose}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    fireEvent.click(screen.getByTestId("archive-blocked-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking the backdrop closes the dialog", async () => {
    const onClose = vi.fn();
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={onClose}
        projectName="Acme Renewal"
        livenessReport={makeReport()}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("archive-blocked-dialog"));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders EmptyState (not breakdown chips) when every count is zero", () => {
    render(
      <ArchiveBlockedDialog
        open={true}
        onClose={() => {}}
        projectName="Acme Renewal"
        livenessReport={makeReport({
          active_runs: 0,
          pending_approvals: 0,
          active_routines: 0,
          in_flight_inbox: 0,
          is_alive: false,
        })}
      />,
    );
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(
      screen.queryByTestId("archive-blocked-breakdown"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("archive-blocked-headline"),
    ).not.toBeInTheDocument();
  });
});
