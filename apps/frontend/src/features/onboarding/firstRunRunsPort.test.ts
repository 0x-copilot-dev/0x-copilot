// Web FirstRunRunsPort — two-step create over the typed api/agentApi module.
// Parity with the desktop `firstRunRunsPort` unit coverage (adapted to the web
// host binding `createConversation` / `createRun` instead of a raw Transport).

import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ModelSelectionRequest,
  RunAttachmentRequest,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";

vi.mock("../../api/agentApi", () => ({
  createConversation: vi.fn(),
  createRun: vi.fn(),
}));

import { createConversation, createRun } from "../../api/agentApi";
import { createFirstRunRunsPort } from "./firstRunRunsPort";

const IDENTITY: RequestIdentity = { orgId: "org_1", userId: "user_1" };

describe("createFirstRunRunsPort", () => {
  beforeEach(() => {
    vi.mocked(createConversation).mockReset();
    vi.mocked(createRun).mockReset();
  });

  it("creates a conversation (title from prompt) then a run, returning ids", async () => {
    vi.mocked(createConversation).mockResolvedValue({
      conversation_id: "conv_1",
    } as never);
    vi.mocked(createRun).mockResolvedValue({
      run_id: "run_1",
      conversation_id: "conv_1",
    } as never);

    const result = await createFirstRunRunsPort(IDENTITY).createFirstRun({
      userInput: "watch my wallet",
      model: null,
    });

    expect(createConversation).toHaveBeenCalledWith(IDENTITY, {
      title: "watch my wallet",
    });
    expect(createRun).toHaveBeenCalledWith(
      "conv_1",
      "watch my wallet",
      IDENTITY,
      {
        model: null,
        attachments: undefined,
      },
    );
    expect(result).toEqual({ conversationId: "conv_1", runId: "run_1" });
  });

  it("passes model + attachments through only when present", async () => {
    vi.mocked(createConversation).mockResolvedValue({
      conversation_id: "conv_2",
    } as never);
    vi.mocked(createRun).mockResolvedValue({ run_id: "run_2" } as never);

    const model: ModelSelectionRequest = {
      provider: "ollama",
      model_name: "qwen3:4b",
    };
    const attachments: RunAttachmentRequest[] = [
      {
        id: "a1",
        type: "file",
        name: "airdrop-claims.csv",
        content_type: "text/csv",
        size: 10,
        content: [{ type: "text", text: "address,token" }],
      },
    ];

    await createFirstRunRunsPort(IDENTITY).createFirstRun({
      userInput: "explain this csv",
      model,
      attachments,
    });

    expect(createRun).toHaveBeenCalledWith(
      "conv_2",
      "explain this csv",
      IDENTITY,
      { model, attachments },
    );
  });

  it("falls back to a neutral title for an attachment-only (empty text) send", async () => {
    vi.mocked(createConversation).mockResolvedValue({
      conversation_id: "conv_3",
    } as never);
    vi.mocked(createRun).mockResolvedValue({ run_id: "run_3" } as never);

    await createFirstRunRunsPort(IDENTITY).createFirstRun({
      userInput: "   ",
      model: null,
    });

    expect(createConversation).toHaveBeenCalledWith(IDENTITY, {
      title: "First run",
    });
  });

  it("truncates a long prompt to a 60-char conversation title", async () => {
    vi.mocked(createConversation).mockResolvedValue({
      conversation_id: "conv_4",
    } as never);
    vi.mocked(createRun).mockResolvedValue({ run_id: "run_4" } as never);

    const long = "a".repeat(120);
    await createFirstRunRunsPort(IDENTITY).createFirstRun({
      userInput: long,
      model: null,
    });

    expect(createConversation).toHaveBeenCalledWith(IDENTITY, {
      title: "a".repeat(60),
    });
  });

  it("throws (no run create) when the conversation returns no id", async () => {
    vi.mocked(createConversation).mockResolvedValue({
      conversation_id: "",
    } as never);

    await expect(
      createFirstRunRunsPort(IDENTITY).createFirstRun({
        userInput: "hi",
        model: null,
      }),
    ).rejects.toThrow(/conversation create returned no conversation_id/);
    expect(createRun).not.toHaveBeenCalled();
  });
});
