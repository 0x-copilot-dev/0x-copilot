import type { Skill } from "@enterprise-search/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { ToolsDestination } from "./ToolsDestination";

type RequestHandler = (req: TypedRequest) => Promise<unknown>;

function makeTransport(handler: RequestHandler): Transport {
  return {
    async request<TRes>(req: TypedRequest): Promise<TRes> {
      return (await handler(req)) as TRes;
    },
    subscribeServerSentEvents(_opts: SseSubscribeOptions): SseSubscription {
      return { close: () => undefined };
    },
    getSession(): Session {
      return { bearer: null };
    },
    capabilities(): TransportCapabilities {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
  };
}

function makeRouter(): Router<ArtifactRoute> {
  let current: ArtifactRoute | null = null;
  const subs = new Set<(r: ArtifactRoute) => void>();
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate: vi.fn((r: ArtifactRoute) => {
      current = r;
      for (const s of subs) s(r);
    }),
    subscribe(handler) {
      subs.add(handler);
      return () => subs.delete(handler);
    },
  };
}

function makeSkill(overrides: Partial<Skill>): Skill {
  return {
    skill_id: overrides.skill_id ?? "sk-1",
    name: overrides.name ?? "summarize",
    display_name: overrides.display_name ?? "Summarize",
    description: overrides.description ?? "Summarize a document concisely.",
    markdown: overrides.markdown ?? "",
    virtual_path: overrides.virtual_path ?? "summarize.md",
    enabled: overrides.enabled ?? true,
    scope: overrides.scope ?? "user",
    source_type: overrides.source_type ?? "user",
    version: overrides.version ?? 1,
    allowed_tools: overrides.allowed_tools ?? [],
    compatibility: overrides.compatibility ?? [],
    metadata: overrides.metadata ?? {},
    created_at: overrides.created_at ?? "2026-05-01T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-15T00:00:00Z",
  };
}

const SAMPLE_SKILLS: readonly Skill[] = [
  makeSkill({ skill_id: "sk-1", display_name: "Summarize", enabled: true }),
  makeSkill({
    skill_id: "sk-2",
    name: "draft-email",
    display_name: "Draft email",
    description: "Draft outbound email from notes.",
    enabled: false,
  }),
];

function renderWith(handler: RequestHandler): {
  router: Router<ArtifactRoute>;
} {
  const router = makeRouter();
  render(
    <TransportProvider transport={makeTransport(handler)}>
      <RouterProvider router={router}>
        <ToolsDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { router };
}

describe("ToolsDestination", () => {
  it("renders the skeleton while the initial request is in flight", async () => {
    let resolve!: (v: { skills: readonly Skill[] }) => void;
    const pending = new Promise<{ skills: readonly Skill[] }>((r) => {
      resolve = r;
    });
    renderWith(() => pending);
    expect(screen.getAllByTestId("tools-skeleton-card").length).toBeGreaterThan(
      0,
    );
    await act(async () => {
      resolve({ skills: [] });
      await pending;
    });
  });

  it("renders skill cards once the request resolves", async () => {
    renderWith(async () => ({ skills: SAMPLE_SKILLS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("tools-card")).toHaveLength(2);
    });
    expect(screen.getByText("Summarize")).toBeInTheDocument();
    expect(screen.getByText("Draft email")).toBeInTheDocument();
  });

  it("renders Install for disabled skills and Manage for enabled skills", async () => {
    renderWith(async () => ({ skills: SAMPLE_SKILLS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("tools-card")).toHaveLength(2);
    });
    expect(screen.getByTestId("tools-install")).toBeInTheDocument();
    expect(screen.getByTestId("tools-manage")).toBeInTheDocument();
  });

  it("renders the empty state when the skills list is empty", async () => {
    renderWith(async () => ({ skills: [] }));
    await waitFor(() => {
      expect(screen.getByTestId("tools-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("tools-card")).toBeNull();
  });

  it("renders the error state and recovers on retry", async () => {
    let calls = 0;
    renderWith(async () => {
      calls += 1;
      if (calls === 1) throw new Error("upstream 500");
      return { skills: SAMPLE_SKILLS };
    });
    await waitFor(() => {
      expect(screen.getByTestId("tools-error")).toBeInTheDocument();
    });
    expect(screen.getByText("upstream 500")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tools-retry"));
    await waitFor(() => {
      expect(screen.getAllByTestId("tools-card")).toHaveLength(2);
    });
    expect(calls).toBe(2);
  });

  it("clicking a card navigates with {kind:'skill', skillId}", async () => {
    const { router } = renderWith(async () => ({ skills: SAMPLE_SKILLS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("tools-card")).toHaveLength(2);
    });
    const card = screen.getAllByTestId("tools-card")[0];
    fireEvent.click(card);
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "skill",
      skillId: "sk-1",
    });
  });

  it("clicking the Manage button does not also navigate", async () => {
    const { router } = renderWith(async () => ({ skills: SAMPLE_SKILLS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("tools-card")).toHaveLength(2);
    });
    fireEvent.click(screen.getByTestId("tools-manage"));
    expect(router.navigate).not.toHaveBeenCalled();
  });
});
