import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcWorkspaceStageSurface } from "./TcWorkspaceStageSurface";
import {
  WORKSPACE_STAGE_PLEDGE,
  type WorkspaceStage,
  type WorkspaceStageOperationKind,
} from "./workspaceStageProjection";

const HASH_BEFORE = "a".repeat(64);
const HASH_AFTER = "b".repeat(64);

function stage(overrides: Partial<WorkspaceStage> = {}): WorkspaceStage {
  return {
    stageId: "workspace-stage-1",
    title: "Finance report",
    operation: { kind: "create" },
    target: {
      mountLabel: "Finance workspace",
      virtualPath: "/workspace/Finance/report.csv",
    },
    revision: 3,
    author: "Agent Mira",
    status: "staged",
    preview: null,
    diff: null,
    baseline: null,
    precondition: null,
    revisionHistory: null,
    resolution: null,
    ...overrides,
  };
}

function renderSurface(
  overrides: Partial<React.ComponentProps<typeof TcWorkspaceStageSurface>> = {},
) {
  const callbacks = {
    onApprove: vi.fn(),
    onReject: vi.fn(),
    onRestore: vi.fn(),
    onEdit: vi.fn(),
  };
  const result = render(
    <TcWorkspaceStageSurface stage={stage()} {...callbacks} {...overrides} />,
  );
  return { ...result, ...callbacks };
}

describe("TcWorkspaceStageSurface", () => {
  it("renders compact success status chips for staged and applied workspace work", () => {
    const { rerender } = renderSurface();

    const staged = screen.getByTestId("tc-workspace-stage-status");
    expect(staged).toHaveClass("ui-badge--success");
    expect(staged).toHaveStyle({
      borderRadius: "5px",
      fontSize: "var(--font-size-mono-8-5)",
      padding: "2px 6px",
    });
    expect(screen.getByTestId("tc-workspace-stage")).toHaveStyle({
      borderRadius: "10px",
      boxShadow: "none",
      padding: "0px",
    });

    rerender(
      <TcWorkspaceStageSurface
        stage={stage({ status: "applied" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    const applied = screen.getByTestId("tc-workspace-stage-applied");
    expect(applied).toHaveClass("ui-badge--success");
    expect(applied).toHaveTextContent("Applied");
  });

  it.each<readonly [WorkspaceStageOperationKind, string]>([
    ["create", "create"],
    ["replace", "replace"],
    ["delete", "delete"],
    ["move", "move"],
    ["mkdir", "mkdir"],
  ])("renders the %s operation badge", (kind, label) => {
    renderSurface({ stage: stage({ operation: { kind } }) });
    expect(screen.getByTestId("tc-workspace-stage")).toHaveAttribute(
      "data-operation",
      kind,
    );
    expect(
      screen.getByTestId("tc-workspace-stage-operation"),
    ).toHaveTextContent(label);
  });

  it("renders text preview/diff, baseline, author, and revision history in Studio", () => {
    renderSurface({
      stage: stage({
        preview: {
          kind: "text",
          language: "CSV",
          content: "name,total\nAcme,42",
        },
        diff: {
          kind: "text",
          before: "name,total\nAcme,40",
          after: "name,total\nAcme,42",
        },
        baseline: {
          summary: "The previous report digest was captured.",
          digest: HASH_BEFORE,
        },
        precondition: {
          summary: "The target must still be absent before create.",
        },
        revisionHistory: [
          { revision: 2, author: "Agent Mira", summary: "Initial CSV" },
          { revision: 3, author: "You", summary: "Adjusted totals" },
        ],
      }),
    });

    expect(
      screen.getByTestId("tc-workspace-stage-preview-text"),
    ).toHaveTextContent("Acme,42");
    expect(
      screen.getByTestId("tc-workspace-stage-diff-text"),
    ).toHaveTextContent("Acme,40");
    expect(
      screen.getByTestId("tc-workspace-stage-preconditions"),
    ).toHaveTextContent("previous report digest");
    expect(screen.getByTestId("tc-workspace-stage-history")).toHaveTextContent(
      "Adjusted totals",
    );
    expect(screen.getByTestId("tc-workspace-stage-history")).toHaveTextContent(
      "You",
    );
    expect(screen.getByTestId("tc-workspace-stage-revision")).toHaveTextContent(
      "rev 3 · Agent Mira",
    );
  });

  it("renders a CSV data preview and row-count diff", () => {
    renderSurface({
      stage: stage({
        preview: {
          kind: "csv",
          columns: ["name", "total"],
          rows: [["Acme", "42"]],
        },
        diff: { kind: "csv", beforeRows: 5, afterRows: 6, changedRows: 2 },
      }),
    });

    expect(
      screen.getByTestId("tc-workspace-stage-preview-csv"),
    ).toHaveTextContent("Acme");
    expect(screen.getByTestId("tc-workspace-stage-diff-csv")).toHaveTextContent(
      "5 → 6",
    );
  });

  it("renders binary metadata and hash diffs without exposing arbitrary refs", () => {
    renderSurface({
      stage: stage({
        preview: {
          kind: "binary",
          metadata: {
            mediaType: "application/vnd.ms-excel",
            byteSize: 2048,
            sha256: HASH_AFTER,
          },
        },
        diff: {
          kind: "binary",
          before: { byteSize: 1024, sha256: HASH_BEFORE },
          after: { byteSize: 2048, sha256: HASH_AFTER },
        },
      }),
    });

    expect(
      screen.getAllByTestId("tc-workspace-stage-binary-metadata"),
    ).toHaveLength(3);
    expect(
      screen.getByTestId("tc-workspace-stage-diff-binary"),
    ).toHaveTextContent(HASH_AFTER);
  });

  it("renders hostile title and virtual-path text without interpreting markup", () => {
    const hostileTitle = '<img src=x onerror="alert(1)">';
    const hostilePath = "/workspace/<svg/onload=alert(1)>/report.csv";
    const { container } = renderSurface({
      stage: stage({
        title: hostileTitle,
        target: {
          mountLabel: "<script>alert(1)</script>",
          virtualPath: hostilePath,
        },
      }),
    });

    expect(screen.getByTestId("tc-workspace-stage-title")).toHaveTextContent(
      hostileTitle,
    );
    expect(screen.getByTestId("tc-workspace-stage-path")).toHaveTextContent(
      hostilePath,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("uses an honest safe state when no preview or diff payload is supplied", () => {
    renderSurface({ stage: stage({ preview: null, diff: null }) });

    expect(
      screen.getByTestId("tc-workspace-stage-preview-empty"),
    ).toHaveTextContent("No safely previewable artifact payload was supplied.");
    expect(
      screen.getByTestId("tc-workspace-stage-diff-empty"),
    ).toHaveTextContent("No safely previewable diff payload was supplied.");
  });

  it("gives delete stronger treatment and delegates the exact decision callbacks", () => {
    const { onApprove, onReject, onEdit } = renderSurface({
      stage: stage({ operation: { kind: "delete" } }),
    });

    expect(screen.getByTestId("tc-workspace-stage")).toHaveAttribute(
      "data-destructive",
      "true",
    );
    expect(screen.getByTestId("tc-workspace-stage")).toHaveClass(
      "ui-card--danger",
    );
    expect(
      screen.getByTestId("tc-workspace-stage-destructive"),
    ).toHaveTextContent("Destructive");
    expect(screen.getByTestId("tc-workspace-stage-approve")).toHaveClass(
      "ui-button--danger",
    );

    fireEvent.click(screen.getByTestId("tc-workspace-stage-approve"));
    fireEvent.click(screen.getByTestId("tc-workspace-stage-reject"));
    fireEvent.click(screen.getByTestId("tc-workspace-stage-edit"));
    expect(onApprove).toHaveBeenCalledWith("workspace-stage-1", 3);
    expect(onReject).toHaveBeenCalledWith("workspace-stage-1", 3);
    expect(onEdit).toHaveBeenCalledWith("workspace-stage-1", 3);
  });

  it("uses Restore for a rejected stage", () => {
    const { onRestore } = renderSurface({
      stage: stage({ status: "rejected" }),
    });

    expect(screen.queryByTestId("tc-workspace-stage-approve")).toBeNull();
    fireEvent.click(screen.getByTestId("tc-workspace-stage-restore"));
    expect(onRestore).toHaveBeenCalledWith("workspace-stage-1");
  });

  it("pins the exact C3 approval pledge", () => {
    renderSurface();
    expect(screen.getByTestId("tc-workspace-stage-pledge")).toHaveTextContent(
      WORKSPACE_STAGE_PLEDGE,
    );
    expect(WORKSPACE_STAGE_PLEDGE).toBe(
      "Only this revision and target will be applied.",
    );
  });

  it("does not leak a physical path or permit token from unsafe host data", () => {
    const physicalPath = "/Users/alice/secret/finance.csv";
    const permitToken = "permit-super-secret-value";
    const unsafeStage = {
      ...stage({
        title: `Review ${physicalPath}`,
        author: `permitToken=${permitToken}`,
        target: {
          mountLabel: `file://${physicalPath}`,
          virtualPath: physicalPath,
        },
        preview: {
          kind: "text",
          content: `physical_path=${physicalPath}\npermit_token=${permitToken}`,
        },
        diff: {
          kind: "text",
          before: physicalPath,
          after: `permitToken=${permitToken}`,
        },
        baseline: {
          summary: `physical_path=${physicalPath}`,
          digest: permitToken,
        },
        resolution: {
          state: "precondition_drift" as const,
          summary: `permit_token=${permitToken}`,
        },
      }),
      physicalPath,
      permitToken,
    } as WorkspaceStage;
    const { container } = renderSurface({ stage: unsafeStage });

    expect(container).not.toHaveTextContent(physicalPath);
    expect(container).not.toHaveTextContent(permitToken);
    expect(screen.getByTestId("tc-workspace-stage-path")).toHaveTextContent(
      "Virtual target unavailable",
    );
    expect(screen.getByTestId("tc-workspace-stage-approve")).toBeDisabled();
    expect(screen.getByTestId("tc-workspace-stage-target")).toHaveTextContent(
      "Workspace",
    );
  });

  it("fails safely when a host supplies incomplete preview or diff payloads", () => {
    renderSurface({
      stage: stage({
        preview: { kind: "text" } as WorkspaceStage["preview"],
        diff: { kind: "binary" } as WorkspaceStage["diff"],
      }),
    });

    expect(
      screen.getByTestId("tc-workspace-stage-preview-text"),
    ).toHaveTextContent("No text payload.");
    expect(
      screen.getByTestId("tc-workspace-stage-diff-binary"),
    ).toHaveTextContent("No binary metadata was supplied.");
  });

  it.each([
    ["grant_revoked", "Workspace access changed"],
    ["precondition_drift", "Conflict"],
    ["reconciling", "Reconciling"],
    ["indeterminate", "Outcome unknown"],
    ["recovery_proposed", "Recovery proposed"],
    ["recovery_conflict", "Recovery conflict"],
    ["unsupported", "Workspace unavailable"],
    ["upload_mismatch", "Content verification failed"],
  ] as const)("renders the %s C3 recovery state", (state, label) => {
    const { onApprove } = renderSurface({
      stage: stage({ resolution: { state } }),
    });

    expect(
      screen.getByTestId("tc-workspace-stage-resolution"),
    ).toHaveTextContent(label);
    const approve = screen.getByTestId("tc-workspace-stage-approve");
    expect(approve).toBeDisabled();
    fireEvent.click(approve);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("uses a compact Focus card and a full Studio surface", () => {
    const props = {
      stage: stage({
        preview: { kind: "text" as const, content: "safe preview" },
        diff: { kind: "text" as const, before: "before", after: "after" },
      }),
      onApprove: vi.fn(),
      onReject: vi.fn(),
      onRestore: vi.fn(),
      onEdit: vi.fn(),
    };
    const { rerender } = render(
      <TcWorkspaceStageSurface {...props} mode="focus" />,
    );

    expect(screen.getByTestId("tc-workspace-stage")).toHaveAttribute(
      "data-presentation",
      "compact",
    );
    expect(screen.queryByTestId("tc-workspace-stage-preview")).toBeNull();
    expect(screen.getByTestId("tc-workspace-stage-pledge")).toHaveTextContent(
      WORKSPACE_STAGE_PLEDGE,
    );

    rerender(<TcWorkspaceStageSurface {...props} mode="studio" />);
    expect(screen.getByTestId("tc-workspace-stage")).toHaveAttribute(
      "data-presentation",
      "full",
    );
    expect(
      screen.getByTestId("tc-workspace-stage-preview"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("tc-workspace-stage-diff")).toBeInTheDocument();
  });
});
