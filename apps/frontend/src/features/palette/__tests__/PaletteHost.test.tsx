import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement, ReactNode } from "react";

import type { PaletteSearchResponse } from "@enterprise-search/api-types";
import {
  RouterProvider,
  type ArtifactRoute,
  type Router,
} from "@enterprise-search/chat-surface";

// CommandPalette (rendered inside PaletteHost) consumes useRouter — the
// host normally provides a real HashRouter via ChatShell. In tests we
// supply a minimal stub Router so the component can mount in isolation.
function StubRouterProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const router: Router<ArtifactRoute | null> = {
    current: () => null,
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
  return <RouterProvider router={router}>{children}</RouterProvider>;
}

const paletteApiMocks = vi.hoisted(() => ({
  searchPalette: vi.fn(),
}));
vi.mock("../../../api/paletteApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../api/paletteApi")
  >("../../../api/paletteApi");
  return {
    ...actual,
    searchPalette: paletteApiMocks.searchPalette,
  };
});

import { createWebPaletteSearchPort, PaletteHost } from "../PaletteHost";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function fixtureResponse(): PaletteSearchResponse {
  return {
    hits: [
      {
        id: "hit_1",
        kind: "navigation",
        title: "Open Inbox",
        route: "/inbox",
        score: 0.95,
      },
    ],
    took_ms: 12,
  };
}

describe("createWebPaletteSearchPort", () => {
  beforeEach(() => {
    paletteApiMocks.searchPalette.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("threads the identity into searchPalette() calls", async () => {
    paletteApiMocks.searchPalette.mockResolvedValueOnce(fixtureResponse());
    const port = createWebPaletteSearchPort(IDENTITY);

    const res = await port.search({ q: "inbox" });

    expect(res.hits).toHaveLength(1);
    expect(paletteApiMocks.searchPalette).toHaveBeenCalledWith(IDENTITY, {
      q: "inbox",
    });
  });
});

describe("PaletteHost", () => {
  beforeEach(() => {
    paletteApiMocks.searchPalette.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("mounts with the palette closed and does NOT pre-flight a search", () => {
    const { getByTestId } = render(
      <StubRouterProvider>
        <PaletteHost identity={IDENTITY} />
      </StubRouterProvider>,
    );

    expect(getByTestId("palette-host")).toBeDefined();
    expect(getByTestId("palette-host").getAttribute("data-palette-open")).toBe(
      "false",
    );
    // Canonical CommandPalette is search-on-open + debounced — the host
    // never calls the port at mount time.
    expect(paletteApiMocks.searchPalette).not.toHaveBeenCalled();
  });
});
