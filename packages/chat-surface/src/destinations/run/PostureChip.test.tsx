import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PostureChip } from "./PostureChip";

describe("PostureChip", () => {
  it("shows the normal posture when bypass is off", () => {
    render(<PostureChip bypassOn={false} />);
    const chip = screen.getByTestId("posture-chip");
    expect(chip).toHaveTextContent("Writes wait for you");
    expect(chip.getAttribute("data-bypass")).toBe("off");
  });

  it("shows the amber bypass posture when bypass is on", () => {
    render(<PostureChip bypassOn />);
    const chip = screen.getByTestId("posture-chip");
    expect(chip).toHaveTextContent("Bypass on");
    expect(chip.getAttribute("data-bypass")).toBe("on");
  });
});
