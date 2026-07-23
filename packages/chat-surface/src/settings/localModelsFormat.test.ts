import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatBytesPair,
  formatEta,
  humanStatus,
  placementLabel,
} from "./localModelsFormat";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [-1, "0 B"],
    [512, "512 B"],
    [1024, "1.0 KB"],
    [808_000_000, "771 MB"], // binary MB — the docstring's "808 MB" was SI
  ])("formats %s as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatBytesPair", () => {
  // The headline contract (PRD-P8 §5): ONE unit, chosen from the total, both
  // halves in it — never "2400 MB / 4.3 GB".
  it("renders the design's byte line in one shared unit", () => {
    expect(formatBytesPair(2_400_000_000, 4_300_000_000)).toBe("2.4 / 4.3 GB");
  });

  it("keeps the pair's unit even when the halves are scales apart", () => {
    // `formatBytes` would answer "2.2 MB" for the completed half here; the
    // pair stays in the total's unit so the two numbers are comparable.
    expect(formatBytesPair(2_400_000, 4_300_000_000)).toBe("0.0 / 4.3 GB");
  });

  it("agrees with the card's frozen 4.3 GB header meta (D5)", () => {
    // The verified Qwen3-4B Q8_0 size. The foot must not read "4.0 GB" under a
    // header that reads "4.3 GB" — this is why the pair is SI-based.
    expect(formatBytesPair(0, 4_280_404_704)).toBe("0 / 4.3 GB");
  });

  it.each([
    // unit boundaries — 999 B stays in B, 1000 B rolls to KB
    [500, 900, "500 / 900 B"],
    [999, 999, "999 / 999 B"],
    [512, 1_000, "0.5 / 1.0 KB"],
    [512, 2_000, "0.5 / 2.0 KB"],
    [1_000, 1_000_000, "0.0 / 1.0 MB"],
    [500_000_000, 1_000_000_000, "0.5 / 1.0 GB"],
    [1_500_000_000_000, 3_000_000_000_000, "1.5 / 3.0 TB"],
    // >= 10 in the chosen unit drops the decimal (formatBytes' own rule)
    [6_000_000_000, 12_000_000_000, "6.0 / 12 GB"],
    // petabyte-scale saturates at the last unit rather than inventing one
    [1_000_000_000_000_000, 2_000_000_000_000_000, "1000 / 2000 TB"],
  ])("formats %s of %s as %s", (completed, total, expected) => {
    expect(formatBytesPair(completed, total)).toBe(expected);
  });

  it.each([
    // zero completed prints "0", never "0.0"
    [0, 4_300_000_000, "0 / 4.3 GB"],
    [null, 4_300_000_000, "0 / 4.3 GB"],
    [undefined, 4_300_000_000, "0 / 4.3 GB"],
    // a negative/NaN half is clamped, never rendered
    [-5, 4_300_000_000, "0 / 4.3 GB"],
    [Number.NaN, 4_300_000_000, "0 / 4.3 GB"],
  ])(
    "clamps a missing/invalid completed half (%s)",
    (completed, total, expected) => {
      expect(formatBytesPair(completed, total)).toBe(expected);
    },
  );

  it.each([
    // no total → the lone completed size, in its own unit
    [2_400_000_000, null, "2.4 GB"],
    [2_400_000_000, undefined, "2.4 GB"],
    [2_400_000_000, 0, "2.4 GB"],
    [2_400_000_000, -1, "2.4 GB"],
    [2_400_000_000, Number.NaN, "2.4 GB"],
    [808_000_000, null, "808 MB"],
  ])(
    "falls back to the completed size when the total is unknown (%s/%s)",
    (completed, total, expected) => {
      expect(formatBytesPair(completed, total)).toBe(expected);
    },
  );

  it.each([
    [null, null],
    [undefined, undefined],
    [0, null],
    [0, 0],
    [-1, -1],
  ])("answers null when nothing is known (%s/%s)", (completed, total) => {
    expect(formatBytesPair(completed, total)).toBeNull();
  });

  it("clamps a completed half that overshoots the total", () => {
    // Ollama can briefly report more completed than total across layers;
    // "4.5 / 4.3 GB" reads as broken.
    expect(formatBytesPair(4_500_000_000, 4_300_000_000)).toBe("4.3 / 4.3 GB");
  });
});

describe("formatEta", () => {
  it.each([
    [40, "40s"],
    [40.2, "41s"],
    [125, "2m 5s"],
    [3_600, "1h 0m"],
    [7_380, "2h 3m"],
  ])("formats %s seconds as %s", (seconds, expected) => {
    expect(formatEta(seconds)).toBe(expected);
  });
});

describe("humanStatus", () => {
  it.each([
    ["starting", "Starting…"],
    ["resolving", "Checking size…"],
    ["pulling 5a1b2c", "Downloading…"],
    ["downloading", "Downloading…"],
    ["verifying sha256 digest", "Verifying…"],
    ["writing manifest", "Finishing…"],
    ["success", "success"],
  ])("maps %s to %s", (status, expected) => {
    expect(humanStatus(status)).toBe(expected);
  });
});

describe("placementLabel", () => {
  it.each([
    ["gpu", "GPU"],
    ["cpu", "CPU — slower"],
    ["partial", "GPU + CPU — slower"],
  ] as const)("labels %s as %s", (placement, expected) => {
    expect(placementLabel(placement)).toBe(expected);
  });
});
