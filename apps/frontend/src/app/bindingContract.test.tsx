// Web host binding conformance (PRD-03).
//
// The web analog of the desktop `bindingContract.test.tsx`: the web binding
// answers every field the shell contract's manifest declares with a
// non-`undefined` value, and its opt-outs are literal `null`. Adding a field to
// the manifest turns this test red until web answers it.

import { SHELL_BINDING_FIELDS } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import { buildWebShellBinding } from "./shellBinding";

describe("web shell binding — manifest conformance", () => {
  it("answers every SHELL_BINDING_FIELDS entry with a non-undefined value", () => {
    const binding = buildWebShellBinding("Sarah Chen", false);
    for (const field of SHELL_BINDING_FIELDS) {
      expect(binding[field]).not.toBeUndefined();
    }
  });

  it("declares its opt-outs literally and derives the rail identity from the name", () => {
    const binding = buildWebShellBinding("Sarah Chen", false);
    expect(binding.walletChip).toBeNull();
    expect(binding.topbarLeaf).toBeNull();
    expect(binding.railIdentity).toEqual({ displayName: "Sarah Chen" });
    // Blank / absent name → the neutral glyph (null), never an empty initial.
    expect(buildWebShellBinding("   ", false).railIdentity).toBeNull();
    expect(buildWebShellBinding(undefined, false).railIdentity).toBeNull();
  });
});
