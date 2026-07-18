// FR-5.19 / FR-5.20 — Privacy & retention (Data & privacy group).
//
//   * "Keep run history for" reports each change through `onRetentionChange`.
//   * "Open Activity" + "Review N memories →" route through host nav callbacks.
//   * Memory toggle reports through `onMemoryToggle`.
//   * "Export everything" fires the host callback then a toast (one-shot, no
//     savebar).
//   * "Delete all history" is DESTRUCTIVE: `onDeleteAll` is never invoked until
//     the confirm phrase is typed exactly (case-insensitive, trimmed).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  PRIVACY_DELETE_CONFIRM_PHRASE,
  PRIVACY_EXPORT_PATH,
  PrivacyPage,
  RETENTION_OPTIONS,
  type RetentionChoice,
} from "./PrivacyPage";

function renderPage(
  overrides: Partial<ComponentProps<typeof PrivacyPage>> = {},
) {
  const props = {
    retention: "forever" as RetentionChoice,
    onRetentionChange: vi.fn(),
    memoryEnabled: true,
    onMemoryToggle: vi.fn(),
    memoryCount: 3,
    onReviewMemories: vi.fn(),
    onOpenActivity: vi.fn(),
    onExport: vi.fn().mockResolvedValue(undefined),
    onDeleteAll: vi.fn().mockResolvedValue(undefined),
    onToast: vi.fn(),
    ...overrides,
  };
  render(<PrivacyPage {...props} />);
  return props;
}

describe("<PrivacyPage>", () => {
  it("records to local history with an Activity jump", () => {
    const props = renderPage();
    expect(screen.getByTestId("privacy-history-note")).toHaveTextContent(
      /local history/i,
    );
    expect(screen.getByTestId("privacy-history-note")).toHaveTextContent(
      /Activity/i,
    );
    fireEvent.click(screen.getByTestId("privacy-open-activity"));
    expect(props.onOpenActivity).toHaveBeenCalledTimes(1);
  });

  it("offers Forever / 90 / 30 / 7 days and persists the retention change", () => {
    const props = renderPage();
    const select = screen.getByTestId(
      "privacy-retention-select",
    ) as HTMLSelectElement;
    const values = RETENTION_OPTIONS.map((o) => o.value);
    expect(values).toEqual(["forever", "90d", "30d", "7d"]);
    expect(select.value).toBe("forever");

    fireEvent.change(select, { target: { value: "30d" } });
    expect(props.onRetentionChange).toHaveBeenCalledWith("30d");
  });

  it("reflects the current retention value", () => {
    renderPage({ retention: "7d" });
    expect(
      (screen.getByTestId("privacy-retention-select") as HTMLSelectElement)
        .value,
    ).toBe("7d");
  });

  it("toggles memory and routes 'Review N memories →'", () => {
    const props = renderPage({ memoryEnabled: false, memoryCount: 5 });
    const toggle = screen.getByTestId("privacy-memory-toggle");
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(props.onMemoryToggle).toHaveBeenCalledWith(true);

    const review = screen.getByTestId("privacy-review-memories");
    expect(review).toHaveTextContent("Review 5 memories →");
    fireEvent.click(review);
    expect(props.onReviewMemories).toHaveBeenCalledTimes(1);
  });

  it("singularizes one memory and shows an empty state at zero", () => {
    const { rerender } = render(
      <PrivacyPage
        retention="forever"
        onRetentionChange={vi.fn()}
        memoryEnabled
        onMemoryToggle={vi.fn()}
        memoryCount={1}
        onReviewMemories={vi.fn()}
        onOpenActivity={vi.fn()}
        onExport={vi.fn()}
        onDeleteAll={vi.fn()}
      />,
    );
    expect(screen.getByTestId("privacy-review-memories")).toHaveTextContent(
      "Review 1 memory →",
    );

    rerender(
      <PrivacyPage
        retention="forever"
        onRetentionChange={vi.fn()}
        memoryEnabled
        onMemoryToggle={vi.fn()}
        memoryCount={0}
        onReviewMemories={vi.fn()}
        onOpenActivity={vi.fn()}
        onExport={vi.fn()}
        onDeleteAll={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("privacy-review-memories")).toBeNull();
    expect(screen.getByTestId("privacy-memories-empty")).toBeInTheDocument();
  });

  it("exports to ~/copilot/export and fires a toast, not a savebar", async () => {
    const props = renderPage();
    expect(screen.getByTestId("privacy-export-card")).toHaveTextContent(
      PRIVACY_EXPORT_PATH,
    );
    fireEvent.click(screen.getByTestId("privacy-export"));
    expect(props.onExport).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(props.onToast).toHaveBeenCalledWith(
        `Export queued to ${PRIVACY_EXPORT_PATH}.`,
      ),
    );
  });

  it("surfaces an export error without a toast", async () => {
    const onExport = vi.fn().mockRejectedValue(new Error("disk full"));
    const props = renderPage({ onExport });
    fireEvent.click(screen.getByTestId("privacy-export"));
    await waitFor(() =>
      expect(screen.getByTestId("privacy-export-error")).toHaveTextContent(
        "disk full",
      ),
    );
    expect(props.onToast).not.toHaveBeenCalled();
  });

  it("blocks delete until the confirm phrase is typed exactly", () => {
    const props = renderPage();
    const button = screen.getByTestId("privacy-delete-all");
    const input = screen.getByTestId("privacy-delete-confirm");

    // Disabled by default — nothing typed.
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(props.onDeleteAll).not.toHaveBeenCalled();

    // Wrong phrase — still gated.
    fireEvent.change(input, { target: { value: "delete" } });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(props.onDeleteAll).not.toHaveBeenCalled();

    // Exact phrase (case/space-insensitive) arms the danger action.
    fireEvent.change(input, {
      target: { value: `  ${PRIVACY_DELETE_CONFIRM_PHRASE.toUpperCase()}  ` },
    });
    expect(button).toBeEnabled();
  });

  it("deletes then resets the confirm field and toasts", async () => {
    const props = renderPage();
    const input = screen.getByTestId("privacy-delete-confirm");
    fireEvent.change(input, {
      target: { value: PRIVACY_DELETE_CONFIRM_PHRASE },
    });
    fireEvent.click(screen.getByTestId("privacy-delete-all"));
    expect(props.onDeleteAll).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(props.onToast).toHaveBeenCalledWith("All history deleted."),
    );
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("surfaces a delete error and keeps the confirm field armed", async () => {
    const onDeleteAll = vi.fn().mockRejectedValue(new Error("locked"));
    renderPage({ onDeleteAll });
    const input = screen.getByTestId("privacy-delete-confirm");
    fireEvent.change(input, {
      target: { value: PRIVACY_DELETE_CONFIRM_PHRASE },
    });
    fireEvent.click(screen.getByTestId("privacy-delete-all"));
    await waitFor(() =>
      expect(screen.getByTestId("privacy-delete-error")).toHaveTextContent(
        "locked",
      ),
    );
    expect((input as HTMLInputElement).value).toBe(
      PRIVACY_DELETE_CONFIRM_PHRASE,
    );
  });
});
