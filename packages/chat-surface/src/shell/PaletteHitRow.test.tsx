// PaletteHitRow — entity rows go through <ItemLink>; non-entity rows
// fire onActivate.

import type { ConversationId, PaletteHit } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import {
  __resetItemRouteRegistryForTests,
  registerItemRoute,
} from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";

import { PaletteHitRow } from "./PaletteHitRow";

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

function makeRouter(): {
  router: Router<ArtifactRoute>;
  navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn();
  return {
    router: {
      current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
      navigate,
      subscribe: () => () => undefined,
    },
    navigate,
  };
}

function renderRow(
  hit: PaletteHit,
  opts?: { isSelected?: boolean },
): {
  onActivate: ReturnType<typeof vi.fn>;
  onHover: ReturnType<typeof vi.fn>;
  navigate: ReturnType<typeof vi.fn>;
} {
  const onActivate = vi.fn();
  const onHover = vi.fn();
  const { router, navigate } = makeRouter();
  render(
    <RouterProvider router={router}>
      <ul>
        <PaletteHitRow
          hit={hit}
          isSelected={opts?.isSelected ?? false}
          id={`row-${hit.id}`}
          onActivate={onActivate}
          onHover={onHover}
        />
      </ul>
    </RouterProvider>,
  );
  return { onActivate, onHover, navigate };
}

describe("<PaletteHitRow>", () => {
  it("renders a navigation hit as a button and fires onActivate on click", () => {
    const hit: PaletteHit = {
      id: "hit_nav_1",
      kind: "navigation",
      title: "Go to Inbox",
      route: "/inbox",
      score: 0.9,
    };
    const { onActivate } = renderRow(hit);
    const btn = screen.getByTestId("palette-hit-button");
    fireEvent.click(btn);
    expect(onActivate).toHaveBeenCalledWith(hit);
  });

  it("renders the kind chip per hit kind", () => {
    const hit: PaletteHit = {
      id: "hit_act_1",
      kind: "action",
      title: "Make this a routine?",
      action_token: "atlas.routine.from_chat",
      score: 0.7,
    };
    renderRow(hit);
    expect(screen.getByTestId("palette-hit-chip")).toHaveTextContent("Action");
  });

  it("renders a command hit (slash command) as a button", () => {
    const hit: PaletteHit = {
      id: "hit_cmd_1",
      kind: "command",
      title: "/help",
      action_token: "/help",
      score: 0.5,
    };
    const { onActivate } = renderRow(hit);
    const btn = screen.getByTestId("palette-hit-button");
    fireEvent.click(btn);
    expect(onActivate).toHaveBeenCalledWith(hit);
  });

  it("fires onHover when the mouse enters the row", () => {
    const hit: PaletteHit = {
      id: "hit_nav_h",
      kind: "navigation",
      title: "Go to Team",
      route: "/team",
      score: 0.9,
    };
    const { onHover } = renderRow(hit);
    fireEvent.mouseEnter(screen.getByTestId("palette-hit-row"));
    expect(onHover).toHaveBeenCalledTimes(1);
  });

  it("renders an entity hit through <ItemLink> (label = hit.title) and routes via the registry", () => {
    registerItemRoute("chat", (id) => ({ kind: "chat", conversationId: id }));
    const hit: PaletteHit = {
      id: "hit_ent_1",
      kind: "entity",
      title: "Acme renewal",
      target: { kind: "chat", id: "conv_001" as ConversationId },
      score: 0.95,
    };
    renderRow(hit);
    const link = screen.getByTestId("item-link");
    expect(link).toHaveTextContent("Acme renewal");
    expect(screen.queryByTestId("palette-hit-button")).toBeNull();
  });

  it("applies the selected style/aria when isSelected=true", () => {
    const hit: PaletteHit = {
      id: "hit_nav_s",
      kind: "navigation",
      title: "Go to Inbox",
      route: "/inbox",
      score: 0.9,
    };
    renderRow(hit, { isSelected: true });
    const row = screen.getByTestId("palette-hit-row");
    expect(row).toHaveAttribute("aria-selected", "true");
  });
});
