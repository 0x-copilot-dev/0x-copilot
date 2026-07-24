import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactEditor } from "./ArtifactEditor";

describe("ArtifactEditor", () => {
  it("retains the local buffer after a 409-style conflict and never auto-merges", async () => {
    const onSave = vi.fn(async () => "conflict" as const);
    render(
      <ArtifactEditor
        initialText="base"
        revision={2}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByLabelText("Edit revision 2"), {
      target: { value: "my local edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save new revision" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "local edits are preserved",
      ),
    );
    expect(screen.getByLabelText("Edit revision 2")).toHaveValue(
      "my local edit",
    );
    expect(onSave).toHaveBeenCalledWith("my local edit");
  });
});
