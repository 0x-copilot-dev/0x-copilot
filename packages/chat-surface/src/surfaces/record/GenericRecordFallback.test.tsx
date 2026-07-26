import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { GenericRecordFallback } from "./GenericRecordFallback";

describe("GenericRecordFallback", () => {
  it("renders a loading record surface without presenting a missing renderer as an error", () => {
    render(<GenericRecordFallback title="ENG-142" state={undefined} />);

    const surface = screen.getByTestId("surface-placeholder");
    expect(surface).toHaveAttribute("data-record-state", "hydrating");
    expect(surface).toHaveTextContent("Connected record");
    expect(
      screen.getByTestId("surface-record-fallback-title"),
    ).toHaveTextContent("ENG-142");
    expect(surface).not.toHaveTextContent(/no adapter registered/i);
  });

  it("renders only bounded scalar source fields as text", () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const { container } = render(
      <GenericRecordFallback
        title="ENG-142"
        state={{
          data: {
            identifier: "ENG-142",
            state: "In progress",
            owner: hostile,
            nested: { internal: "not a displayed field" },
          },
        }}
      />,
    );

    expect(screen.getByTestId("surface-placeholder")).toHaveAttribute(
      "data-record-state",
      "ready",
    );
    expect(
      screen.getByTestId("surface-record-fallback-fields"),
    ).toHaveTextContent("ENG-142");
    expect(
      screen.getByTestId("surface-record-fallback-fields"),
    ).toHaveTextContent(hostile);
    expect(screen.getByTestId("surface-record-fallback-raw")).toHaveTextContent(
      "In progress",
    );
    expect(container.querySelector("img")).toBeNull();
  });
});
