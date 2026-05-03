import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityItem } from "./ActivityItem";

describe("ActivityItem", () => {
  it("renders title and status", () => {
    render(<ActivityItem title="Fetch logs" status="working" />);
    expect(screen.getByText("Fetch logs")).toBeInTheDocument();
    expect(screen.getByText("working")).toBeInTheDocument();
  });
});
