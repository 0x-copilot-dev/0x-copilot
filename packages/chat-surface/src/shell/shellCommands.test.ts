import { describe, expect, it } from "vitest";

import { ICON_NAMES } from "../icons/paths";
import { SHELL_COMMANDS, filterShellCommands } from "./shellCommands";

describe("SHELL_COMMANDS", () => {
  it("defines the 13 v3 design commands", () => {
    expect(SHELL_COMMANDS).toHaveLength(13);
    expect(SHELL_COMMANDS[0]).toMatchObject({
      label: "Go to Run",
      icon: "run",
      intent: { type: "navigate", slug: "run" },
    });
  });

  it("every command has a resolvable icon and a keyword", () => {
    for (const cmd of SHELL_COMMANDS) {
      expect(ICON_NAMES).toContain(cmd.icon);
      expect(cmd.keyword.length).toBeGreaterThan(0);
      expect(cmd.label.length).toBeGreaterThan(0);
    }
  });

  it("maps the settings commands to valid section slugs", () => {
    const byLabel = Object.fromEntries(SHELL_COMMANDS.map((c) => [c.label, c]));
    expect(byLabel["Model & behavior"].intent).toEqual({
      type: "settings",
      section: "model-behavior",
    });
    expect(byLabel["Open Settings"].intent).toEqual({
      type: "settings",
      section: "profile",
    });
  });

  it("routes the direct-launch commands through action intents", () => {
    const byLabel = Object.fromEntries(SHELL_COMMANDS.map((c) => [c.label, c]));
    expect(byLabel["New chat"].intent).toEqual({
      type: "action",
      action: "new-chat",
    });
    expect(byLabel["Add a provider key"].intent).toEqual({
      type: "action",
      action: "add-provider-key",
    });
    expect(byLabel["Download a local model"].intent).toEqual({
      type: "action",
      action: "download-local-model",
    });
    expect(byLabel["Connect a tool"].intent).toEqual({
      type: "action",
      action: "connect-tool",
    });
  });

  it("filters by label and keyword, case-insensitive", () => {
    expect(filterShellCommands("proj").map((c) => c.label)).toContain(
      "Go to Projects",
    );
    // matches on keyword ("BYOK"), not just the label.
    expect(filterShellCommands("byok").map((c) => c.label)).toContain(
      "Add a provider key",
    );
    expect(filterShellCommands("")).toHaveLength(13);
    expect(filterShellCommands("nonesuch")).toHaveLength(0);
  });
});
