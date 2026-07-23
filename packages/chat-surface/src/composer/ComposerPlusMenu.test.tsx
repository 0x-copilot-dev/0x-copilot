// ComposerPlusMenu — shared `.ui-pop*` recipe migration (punch-list rows 44 + 46).
//
// Pins the two things the migration must not lose: the callbacks/roles the
// hosts and the file-picker tests bind to, and the design's popover idiom
// (one `.ui-pop` panel, `.ui-pop-row` rows, an opt-in `.ui-pop-scrim`).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpServer, Skill } from "@0x-copilot/api-types";

import { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";

function server(overrides: Partial<McpServer> = {}): McpServer {
  return {
    server_id: "seed:sheets",
    name: "Google Sheets",
    display_name: "Google Sheets",
    url: "https://sheets.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as McpServer;
}

function skill(overrides: Partial<Skill> = {}): Skill {
  return {
    skill_id: "skill_1",
    name: "summarize",
    display_name: "Summarize",
    description: "Condense a document",
    enabled: true,
    ...overrides,
  } as Skill;
}

const handlers = () => ({
  onBack: vi.fn(),
  onAttachImage: vi.fn(),
  onAttachFile: vi.fn(),
  onOpenMcp: vi.fn(),
  onOpenSkills: vi.fn(),
  onOpenMcpSettings: vi.fn(),
  onOpenSkillsSettings: vi.fn(),
  onShowConnectors: vi.fn(),
  onUseMcpServer: vi.fn(),
  onUseSkill: vi.fn(),
});

function renderMenu(
  view: ComposerMenuView,
  over: {
    servers?: McpServer[];
    serversLoading?: boolean;
    skills?: Skill[];
    skillsLoading?: boolean;
    onDismiss?: () => void;
  } = {},
) {
  const cb = handlers();
  const { container } = render(
    <ComposerPlusMenu
      view={view}
      connectors={{
        servers: over.servers ?? [],
        loading: over.serversLoading ?? false,
      }}
      skills={{
        skills: over.skills ?? [],
        loading: over.skillsLoading ?? false,
      }}
      onDismiss={over.onDismiss}
      {...cb}
    />,
  );
  return { cb, container };
}

describe("<ComposerPlusMenu> — root view", () => {
  it("renders the design's Attach header on one `.ui-pop` panel", () => {
    const { container } = renderMenu("root");
    const panel = screen.getByRole("menu", {
      name: "Attachment and tools menu",
    });
    expect(panel.classList.contains("ui-pop")).toBe(true);
    expect(container.querySelector(".ui-pop__h")?.textContent).toBe(
      "Attach drag & drop works too",
    );
    // No trace of the retired third idiom.
    expect(container.querySelector(".aui-plus-menu")).toBeNull();
    expect(container.querySelector(".aui-trigger-popover__item")).toBeNull();
  });

  it("keeps every row a `.ui-pop-row` menuitem and fires its callback", () => {
    const { cb, container } = renderMenu("root");
    expect(container.querySelectorAll(".ui-pop-row").length).toBe(4);

    fireEvent.click(screen.getByRole("menuitem", { name: /Attach Image/i }));
    expect(cb.onAttachImage).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("menuitem", { name: /Attach File/i }));
    expect(cb.onAttachFile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("menuitem", { name: /MCP Servers/i }));
    expect(cb.onOpenMcp).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("menuitem", { name: /^Skills/i }));
    expect(cb.onOpenSkills).toHaveBeenCalledTimes(1);
  });
});

describe("<ComposerPlusMenu> — MCP view", () => {
  it("lists servers, and routes the pinned + footer actions", () => {
    const { cb, container } = renderMenu("mcp", { servers: [server()] });
    expect(screen.getByRole("menu", { name: "MCP server menu" })).toBeTruthy();
    expect(
      screen.getByRole("menuitem", { name: /Google Sheets/i }),
    ).toBeTruthy();
    // The "Connect a tool…" affordance is the design's pinned row.
    expect(container.querySelector(".ui-pop-row--pin")).not.toBeNull();

    fireEvent.click(screen.getByRole("menuitem", { name: /Google Sheets/i }));
    expect(cb.onUseMcpServer).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Show connector suggestions/i }),
    );
    expect(cb.onShowConnectors).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle("Back to attachment and tools menu"));
    expect(cb.onBack).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle("Open MCP settings"));
    expect(cb.onOpenMcpSettings).toHaveBeenCalledTimes(1);
  });

  it("shows the loading and empty notes", () => {
    const { container } = renderMenu("mcp", { serversLoading: true });
    expect(container.textContent).toContain("Loading servers...");
    const empty = renderMenu("mcp");
    expect(empty.container.textContent).toContain("No MCP servers configured.");
  });
});

describe("<ComposerPlusMenu> — skills view", () => {
  it("lists only enabled skills and routes back/settings", () => {
    const { cb } = renderMenu("skills", {
      skills: [
        skill(),
        skill({ skill_id: "s2", display_name: "Hidden", enabled: false }),
      ],
    });
    expect(screen.getByRole("menu", { name: "Skills menu" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Summarize/i })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /Hidden/i })).toBeNull();

    fireEvent.click(screen.getByRole("menuitem", { name: /Summarize/i }));
    expect(cb.onUseSkill).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle("Back to attachment and tools menu"));
    expect(cb.onBack).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle("Open skill settings"));
    expect(cb.onOpenSkillsSettings).toHaveBeenCalledTimes(1);
  });
});

// Row 46 — the design's scrim, opt-in so it never doubles up with a host
// wrapper that already owns outside-click dismissal.
describe("<ComposerPlusMenu> — click-out scrim (row 46)", () => {
  it("renders no scrim when the caller owns dismissal", () => {
    const { container } = renderMenu("root");
    expect(container.querySelector(".ui-pop-scrim")).toBeNull();
  });

  it("renders the scrim and closes on mousedown / Escape when onDismiss is given", () => {
    const onDismiss = vi.fn();
    const { container } = renderMenu("root", { onDismiss });
    const scrim = container.querySelector(".ui-pop-scrim");
    expect(scrim).not.toBeNull();

    fireEvent.mouseDown(scrim as Element);
    expect(onDismiss).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(
      screen.getByRole("menu", { name: "Attachment and tools menu" }),
      { key: "Escape" },
    );
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });
});
