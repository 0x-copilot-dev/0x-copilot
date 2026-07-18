// @vitest-environment jsdom
import {
  RouterProvider,
  type ArtifactRoute,
  type Router,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";
import { cleanup, render } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DestinationOutlet } from "./DestinationOutlet";

// The desktop vitest config runs with `globals: false`, so testing-library's
// automatic afterEach cleanup does not self-register — do it explicitly.
afterEach(() => {
  cleanup();
});

// DestinationPlaceholder (rendered inside the outlet) reads the Router port for
// its bridge navigation. The outlet never passes bridges, so `navigate` is
// never called — a no-op fake satisfies the hook contract.
function fakeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "chat", conversationId: "c1" }),
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
}

function renderOutlet(destination: ShellDestinationSlug) {
  const ui: ReactElement = (
    <RouterProvider router={fakeRouter()}>
      <DestinationOutlet destination={destination} />
    </RouterProvider>
  );
  return render(ui);
}

function titleOf(container: HTMLElement): string | null | undefined {
  return container.querySelector(
    "[data-testid='destination-placeholder-title']",
  )?.textContent;
}

describe("DestinationOutlet", () => {
  it("renders an honest placeholder for each solo destination", () => {
    const cases: ReadonlyArray<[ShellDestinationSlug, string]> = [
      ["run", "Run"],
      ["chats", "Chats"],
      ["projects", "Projects"],
      ["activity", "Activity"],
      // Tools (relabelled) keeps the underlying `connectors` slug.
      ["connectors", "Tools"],
      // Skills (relabelled) keeps the underlying `tools` slug.
      ["tools", "Skills"],
    ];
    for (const [slug, expectedTitle] of cases) {
      const { container, unmount } = renderOutlet(slug);
      expect(
        container.querySelector("[data-testid='destination-outlet']"),
      ).not.toBeNull();
      expect(titleOf(container)).toBe(expectedTitle);
      // No fake data / spinner / retry — deterministic placeholder only.
      expect(
        container.querySelector("[data-testid='destination-placeholder']"),
      ).not.toBeNull();
      unmount();
    }
  });

  it("folds the deprecated agents slug onto the Activity surface", () => {
    const { container } = renderOutlet("agents");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(titleOf(container)).toBe("Activity");
  });

  it("folds the deprecated inbox slug onto the Activity surface", () => {
    const { container } = renderOutlet("inbox");
    expect(
      container
        .querySelector("[data-testid='destination-outlet']")
        ?.getAttribute("data-destination"),
    ).toBe("activity");
    expect(titleOf(container)).toBe("Activity");
  });

  it("renders a generic honest placeholder for an unmapped slug", () => {
    const { container } = renderOutlet("memory");
    expect(
      container.querySelector("[data-testid='destination-placeholder']"),
    ).not.toBeNull();
    expect(titleOf(container)).toBe("Memory");
  });
});
