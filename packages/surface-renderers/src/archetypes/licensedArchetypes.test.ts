// AC16 — the spec generator may only be licensed for archetypes something here
// can actually draw.
//
// The SurfaceSpec contract licenses ten archetypes; this package implements
// five. The other five (`event | timeline | dashboard | file | form`) collapse
// to the tier-3 generic view, and until the shared constant existed nothing
// told the generator which was which. These tests are the pin: the shipped
// adapters and `implemented_archetypes.json` must agree, so removing an adapter
// fails here until that one file is updated — and updating that one file is the
// only edit that relicenses the generator. No prompt, no schema, no code
// elsewhere.
import { afterEach, describe, expect, it } from "vitest";

import {
  clearRegistry,
  resolveAdapter,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";
// The frozen contract vocabulary (all ten), for the subset assertions below.
import { SURFACE_ARCHETYPES } from "@0x-copilot/api-types";

// The shared, Python-primary constant, imported by relative path for the same
// reason `packages/api-types/src/adapterAllowlist.ts` does it: one on-disk file
// feeds both runtimes, so there is no mirror to keep in sync.
import sharedContract from "../../../service-contracts/src/copilot_service_contracts/implemented_archetypes.json";

import {
  ARCHETYPE_ADAPTERS,
  IMPLEMENTED_ARCHETYPES,
  registerArchetypeAdapters,
} from ".";

const LICENSED: readonly string[] = sharedContract.implemented;

const schemesOf = (adapters: readonly SaaSRendererAdapter[]): string[] =>
  adapters.map((adapter) => adapter.scheme);

const missingFrom = (
  expected: readonly string[],
  actual: readonly string[],
): string[] => expected.filter((name) => !actual.includes(name));

afterEach(() => {
  clearRegistry();
});

describe("the licensed archetype set", () => {
  it("names every shipped adapter", () => {
    // Fails when an adapter is ADDED without relicensing the generator.
    expect(missingFrom(schemesOf(ARCHETYPE_ADAPTERS), LICENSED)).toEqual([]);
  });

  it("names nothing that has no adapter", () => {
    // Fails when an adapter is REMOVED without delicensing it (AC16).
    expect(missingFrom(LICENSED, schemesOf(ARCHETYPE_ADAPTERS))).toEqual([]);
  });

  it("lists the archetypes in registry order", () => {
    // Order is part of the file: it keeps diffs readable and keeps the
    // generator's prompt byte-stable between runs.
    expect(schemesOf(ARCHETYPE_ADAPTERS)).toEqual([...LICENSED]);
  });

  it("is what IMPLEMENTED_ARCHETYPES exports", () => {
    expect([...IMPLEMENTED_ARCHETYPES]).toEqual([...LICENSED]);
  });

  it("only names members of the SurfaceSpec contract vocabulary", () => {
    const vocabulary: readonly string[] = SURFACE_ARCHETYPES;

    expect(missingFrom(LICENSED, vocabulary)).toEqual([]);
  });

  it("is a strict subset — the contract stays wider than the renderers", () => {
    // The contract must keep accepting archetypes nobody draws, because a spec
    // replayed from an older run may carry one. Licensing is the narrower act.
    expect(LICENSED.length).toBeLessThan(SURFACE_ARCHETYPES.length);
  });

  it("never licenses `form`, which the contract still accepts", () => {
    // Model-authored write forms are a deliberate non-goal: SurfaceSpec is
    // read-only by design, so `form` must never reach the generator's licence
    // even though a replayed spec may legally name it.
    expect(SURFACE_ARCHETYPES).toContain("form");
    expect(LICENSED).not.toContain("form");
  });
});

describe("the licensed set resolves at runtime", () => {
  it("gives every licensed archetype a registered adapter", () => {
    registerArchetypeAdapters();

    for (const archetype of LICENSED) {
      expect(resolveAdapter(`${archetype}://a`)).not.toBeNull();
    }
  });

  it("leaves an unlicensed archetype with no adapter", () => {
    registerArchetypeAdapters();

    // The honest consequence of licensing `form` today: nothing would draw it.
    expect(resolveAdapter("form://a")).toBeNull();
  });
});
