// @vitest-environment node
import { mkdtempSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdapterLayoutTemplate } from "@0x-copilot/api-types";

import {
  classifyAdapterReview,
  createFileConsentAckStore,
  createInstallReviewGate,
  type ConsentAckStore,
  type InstallConsentRequest,
} from "./review-gate";

let tmpDir: string;
beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "review-gate-"));
});

async function readUtf8(path: string, _encoding: "utf8"): Promise<string> {
  return readFile(path, "utf8");
}

describe("classifyAdapterReview", () => {
  it("classifies 'form' as write", () => {
    expect(classifyAdapterReview("form")).toBe("write");
  });

  it.each<AdapterLayoutTemplate>(["table", "kanban", "definition-list"])(
    "classifies '%s' as read",
    (layout) => {
      expect(classifyAdapterReview(layout)).toBe("read");
    },
  );
});

describe("createFileConsentAckStore", () => {
  function store(
    name = "consent.json",
  ): ReturnType<typeof createFileConsentAckStore> {
    return createFileConsentAckStore({
      filePath: join(tmpDir, "adapters", name),
      fs: { readFile: readUtf8, writeFile, mkdir },
    });
  }

  it("reports not-acknowledged when the file is missing", async () => {
    expect(await store().isAcknowledged("email")).toBe(false);
  });

  it("persists an acknowledgment and reports it across a fresh store", async () => {
    const path = "consent.json";
    await store(path).recordAcknowledged("email");
    // A fresh store instance reads the same file (survives 'restart').
    expect(await store(path).isAcknowledged("email")).toBe(true);
    expect(await store(path).isAcknowledged("slack")).toBe(false);
  });

  it("is idempotent — recording twice keeps a single entry", async () => {
    const path = "consent.json";
    const s = store(path);
    await s.recordAcknowledged("email");
    await s.recordAcknowledged("email");
    const raw = await readFile(join(tmpDir, "adapters", path), "utf8");
    expect(JSON.parse(raw)).toEqual({ acknowledged: ["email"] });
  });

  it("tolerates a corrupt file (treats it as empty)", async () => {
    const path = join(tmpDir, "adapters", "consent.json");
    await mkdir(join(tmpDir, "adapters"), { recursive: true });
    await writeFile(path, "not json{{{");
    const s = createFileConsentAckStore({
      filePath: path,
      fs: { readFile: readUtf8, writeFile, mkdir },
    });
    expect(await s.isAcknowledged("email")).toBe(false);
  });
});

describe("createInstallReviewGate", () => {
  function fakeStore(initial: string[] = []): ConsentAckStore & {
    recorded: string[];
  } {
    const acked = new Set(initial);
    const recorded: string[] = [];
    return {
      recorded,
      async isAcknowledged(scheme) {
        return acked.has(scheme);
      },
      async recordAcknowledged(scheme) {
        acked.add(scheme);
        recorded.push(scheme);
      },
    };
  }

  const request: InstallConsentRequest = {
    scheme: "email",
    version: 1,
    generatorModel: "render-adapter-generator/v1",
  };

  it("auto-approves an already-acknowledged scheme without prompting", async () => {
    const prompt = vi.fn(async () => true);
    const gate = createInstallReviewGate({
      store: fakeStore(["email"]),
      prompt,
    });
    expect(await gate.requireConsent(request)).toBe(true);
    expect(prompt).not.toHaveBeenCalled();
  });

  it("prompts on a miss and records the acknowledgment when granted", async () => {
    const store = fakeStore();
    const prompt = vi.fn(async () => true);
    const gate = createInstallReviewGate({ store, prompt });
    expect(await gate.requireConsent(request)).toBe(true);
    expect(prompt).toHaveBeenCalledOnce();
    expect(store.recorded).toEqual(["email"]);
  });

  it("returns false and records nothing when the prompt is declined", async () => {
    const store = fakeStore();
    const prompt = vi.fn(async () => false);
    const gate = createInstallReviewGate({ store, prompt });
    expect(await gate.requireConsent(request)).toBe(false);
    expect(prompt).toHaveBeenCalledOnce();
    expect(store.recorded).toEqual([]);
  });
});
