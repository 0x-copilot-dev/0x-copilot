import { describe, expect, it } from "vitest";

import type {
  LocalModelsStatus,
  LocalRuntimeState,
} from "@0x-copilot/api-types";

import {
  deriveLocalRuntimeState,
  deriveRuntimeManaged,
} from "./localRuntimeState";

function status(over: Partial<LocalModelsStatus> = {}): LocalModelsStatus {
  return {
    enabled: true,
    ollama_running: false,
    ollama_version: null,
    ...over,
  };
}

describe("deriveLocalRuntimeState", () => {
  const trusted: readonly {
    readonly declared: LocalRuntimeState;
    readonly running: boolean;
  }[] = [
    { declared: "unknown", running: false },
    { declared: "unknown", running: true },
    { declared: "not_installed", running: false },
    { declared: "not_installed", running: true },
    { declared: "stopped", running: false },
    { declared: "stopped", running: true },
    { declared: "running", running: false },
    { declared: "running", running: true },
  ];

  for (const { declared, running } of trusted) {
    it(`trusts runtime_state="${declared}" (ollama_running=${String(running)})`, () => {
      expect(
        deriveLocalRuntimeState(
          status({ runtime_state: declared, ollama_running: running }),
        ),
      ).toBe(declared);
    });
  }

  it("falls back to running when the field is absent and Ollama answers", () => {
    expect(
      deriveLocalRuntimeState(
        status({ ollama_running: true, ollama_version: "0.6.2" }),
      ),
    ).toBe("running");
  });

  it("falls back to unknown — never not_installed — when the field is absent", () => {
    // The lie D2 exists to prevent: an older server that cannot report
    // runtime_state must not make the card claim Ollama is missing.
    expect(deriveLocalRuntimeState(status({ ollama_running: false }))).toBe(
      "unknown",
    );
    expect(deriveLocalRuntimeState(status({ enabled: false }))).toBe("unknown");
  });

  const garbage: readonly unknown[] = [
    null,
    "",
    "RUNNING",
    "Running",
    "installed",
    "not-installed",
    0,
    1,
    true,
    {},
  ];

  for (const value of garbage) {
    it(`treats unrecognised runtime_state ${JSON.stringify(value)} as absent`, () => {
      const down = {
        ...status({ ollama_running: false }),
        runtime_state: value,
      } as unknown as LocalModelsStatus;
      const up = {
        ...status({ ollama_running: true }),
        runtime_state: value,
      } as unknown as LocalModelsStatus;
      expect(deriveLocalRuntimeState(down)).toBe("unknown");
      expect(deriveLocalRuntimeState(up)).toBe("running");
    });
  }
});

describe("deriveRuntimeManaged", () => {
  it("is true only when the server explicitly says so", () => {
    expect(deriveRuntimeManaged(status({ runtime_managed: true }))).toBe(true);
  });

  it("is false when absent, false, or a non-boolean", () => {
    expect(deriveRuntimeManaged(status())).toBe(false);
    expect(deriveRuntimeManaged(status({ runtime_managed: false }))).toBe(
      false,
    );
    for (const value of [null, "true", 1, {}]) {
      const s = {
        ...status(),
        runtime_managed: value,
      } as unknown as LocalModelsStatus;
      expect(deriveRuntimeManaged(s)).toBe(false);
    }
  });
});
