import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AttachmentPill } from "./AttachmentPill";

describe("AttachmentPill", () => {
  it("renders the attachment name and type", () => {
    render(
      <AttachmentPill attachment={{ name: "report.pdf", type: "document" }} />,
    );
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("document")).toBeInTheDocument();
  });
});
