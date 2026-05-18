import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { NavigateOptions, Router } from "../routing/router";

import { DestinationPlaceholder } from "./DestinationPlaceholder";
import type { ShellDestinationSlug } from "./destinations";

// The bridge buttons call router.navigate with a host-shaped route
// ({ screen: "chat"; destination: slug }) so the web app routes the
// click into a real destination. Tests use a minimal router with a
// vi.fn navigate so we can assert on the exact payload — same pattern
// as the existing chat-surface destination tests.
type AnyRoute = unknown;

function makeRouter(): Router<AnyRoute> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn<(route: AnyRoute, opts?: NavigateOptions) => void>();
  return {
    current(): AnyRoute {
      return { screen: "chat", destination: "home" } as AnyRoute;
    },
    navigate,
    subscribe: () => () => {},
  };
}

function renderPlaceholder(
  ui: React.ReactElement,
  router = makeRouter(),
): {
  readonly router: ReturnType<typeof makeRouter>;
} {
  render(<RouterProvider router={router}>{ui}</RouterProvider>);
  return { router };
}

describe("DestinationPlaceholder", () => {
  it("renders the title, description, and phase label", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        title="Manage your agents"
        description="Browse, customize, and configure the AI agents that work on your behalf."
        phaseLabel="Coming in Phase 8"
      />,
    );
    expect(
      screen.getByTestId("destination-placeholder-title"),
    ).toHaveTextContent("Manage your agents");
    expect(
      screen.getByTestId("destination-placeholder-description"),
    ).toHaveTextContent(
      /Browse, customize, and configure the AI agents that work on your behalf\./,
    );
    expect(
      screen.getByTestId("destination-placeholder-phase"),
    ).toHaveTextContent("Coming in Phase 8");
  });

  it("uses role=region with the title as the aria-label", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        title="Workspace memories"
        description="A central place for what the agent remembers about you."
        phaseLabel="Coming in Phase 11"
      />,
    );
    const region = screen.getByRole("region", { name: "Workspace memories" });
    expect(region).toBeInTheDocument();
  });

  it("renders the supplied hero icon when one is provided", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        icon={<svg data-testid="hero-icon" />}
        title="With icon"
        description="Description."
        phaseLabel="Soon"
      />,
    );
    const wrapper = screen.getByTestId("destination-placeholder-icon");
    // The supplied icon renders inside the icon wrapper. We assert on
    // the supplied element being mounted rather than on inner DOM
    // structure so the wrapper layout can change without breaking
    // tests.
    expect(wrapper.querySelector('[data-testid="hero-icon"]')).not.toBeNull();
  });

  it("falls back to a neutral icon when no icon prop is supplied", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        title="Fallback icon test"
        description="No icon supplied."
        phaseLabel="Soon"
      />,
    );
    // The wrapper is still rendered; it just contains the fallback
    // SVG. The wrapper's presence (with the aria-hidden marker) is
    // the contract.
    const wrapper = screen.getByTestId("destination-placeholder-icon");
    expect(wrapper).toBeInTheDocument();
    expect(wrapper.getAttribute("aria-hidden")).toBe("true");
    // Sanity: there's an svg in there (the fallback).
    expect(wrapper.querySelector("svg")).not.toBeNull();
  });

  it("renders bridge cards and navigates on click", () => {
    const { router } = renderPlaceholder(
      <DestinationPlaceholder
        title="With bridges"
        description="Description."
        phaseLabel="Soon"
        bridges={[
          { label: "See recent activity in Home", slug: "home" },
          { label: "View past runs in Chats", slug: "chats" },
        ]}
      />,
    );
    const homeBridge = screen.getByTestId(
      "destination-placeholder-bridge-home",
    );
    const chatsBridge = screen.getByTestId(
      "destination-placeholder-bridge-chats",
    );
    expect(homeBridge).toHaveTextContent("See recent activity in Home");
    expect(chatsBridge).toHaveTextContent("View past runs in Chats");

    fireEvent.click(homeBridge);
    expect(router.navigate).toHaveBeenLastCalledWith({
      screen: "chat",
      destination: "home" as ShellDestinationSlug,
    });

    fireEvent.click(chatsBridge);
    expect(router.navigate).toHaveBeenLastCalledWith({
      screen: "chat",
      destination: "chats" as ShellDestinationSlug,
    });
    expect(router.navigate).toHaveBeenCalledTimes(2);
  });

  it("omits the bridges section when no bridges are supplied", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        title="No bridges"
        description="Description."
        phaseLabel="Soon"
      />,
    );
    expect(
      screen.queryByTestId("destination-placeholder-bridges"),
    ).not.toBeInTheDocument();
  });

  it("omits the bridges section when the bridges array is empty", () => {
    renderPlaceholder(
      <DestinationPlaceholder
        title="Empty bridges"
        description="Description."
        phaseLabel="Soon"
        bridges={[]}
      />,
    );
    expect(
      screen.queryByTestId("destination-placeholder-bridges"),
    ).not.toBeInTheDocument();
  });

  it("renders the roadmap link only when a roadmap href is supplied", () => {
    const { rerender } = render(
      <RouterProvider router={makeRouter()}>
        <DestinationPlaceholder
          title="No roadmap"
          description="Description."
          phaseLabel="Soon"
        />
      </RouterProvider>,
    );
    expect(
      screen.queryByTestId("destination-placeholder-roadmap"),
    ).not.toBeInTheDocument();

    rerender(
      <RouterProvider router={makeRouter()}>
        <DestinationPlaceholder
          title="With roadmap"
          description="Description."
          phaseLabel="Soon"
          roadmapHref="https://example.com/roadmap"
        />
      </RouterProvider>,
    );
    const link = screen.getByTestId("destination-placeholder-roadmap");
    expect(link).toHaveAttribute("href", "https://example.com/roadmap");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
