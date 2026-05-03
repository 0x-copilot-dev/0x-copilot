import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityCard } from "./ActivityCard";

describe("ActivityCard", () => {
  it("renders title and status", () => {
    render(<ActivityCard title="Read file" status="running" />);
    expect(screen.getByText("Read file")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });
  it("renders params when provided", () => {
    render(
      <ActivityCard
        title="Search"
        status="complete"
        params={[{ label: "query", value: "react" }]}
      />,
    );
    expect(screen.getByText("query")).toBeInTheDocument();
    expect(screen.getByText("react")).toBeInTheDocument();
  });
  it("renders the details disclosure when details are provided", () => {
    render(
      <ActivityCard
        title="Tool"
        status="done"
        details={<span>extra</span>}
        detailsLabel="More info"
      />,
    );
    expect(screen.getByText("More info")).toBeInTheDocument();
  });
});
