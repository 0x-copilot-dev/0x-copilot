import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Topbar } from "./Topbar";

describe("Topbar", () => {
  it("renders the breadcrumb destination label for the active destination", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.getByTestId("topbar-breadcrumb")).toHaveTextContent("Chats");
  });

  it("renders the leaf identifier when one is supplied", () => {
    render(<Topbar activeDestination="chats" leaf="c-77" />);
    expect(screen.getByTestId("topbar-breadcrumb-leaf")).toHaveTextContent(
      "c-77",
    );
  });

  it("renders an em-dash leaf when none is supplied", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.getByTestId("topbar-breadcrumb-leaf")).toHaveTextContent("—");
  });

  it("treats an empty-string leaf as 'no leaf'", () => {
    render(<Topbar activeDestination="chats" leaf="" />);
    expect(screen.getByTestId("topbar-breadcrumb-leaf")).toHaveTextContent("—");
  });

  it("re-labels the breadcrumb when the active destination changes", () => {
    const { rerender } = render(<Topbar activeDestination="home" />);
    expect(screen.getByTestId("topbar-breadcrumb")).toHaveTextContent("Home");
    rerender(<Topbar activeDestination="memory" />);
    expect(screen.getByTestId("topbar-breadcrumb")).toHaveTextContent("Memory");
  });
});
