// BYOK — Provider keys panel.
//
// Behaviours under test:
//   1. Rows render from the list response — saved providers show the
//      masked hint + Replace/Remove; unsaved providers show the input.
//   2. Save flow PUTs the drafted key and flips the row to saved state.
//   3. Remove flow DELETEs and flips the row back to input state.
//   4. Server-side validation errors surface verbatim on the row.
//   5. Replace → Cancel round-trips without losing the saved hint.
//
// Fixture keys are deliberately non-real placeholders — never commit
// real-looking provider secrets (repo AST pre-commit gate).

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type {
  ListProviderKeysResponse,
  ProviderKeyProvider,
  ProviderKeySummary,
  PutProviderKeyRequest,
} from "@0x-copilot/api-types";

const mockList = vi.fn<() => Promise<ListProviderKeysResponse>>();
const mockPut =
  vi.fn<
    (
      provider: ProviderKeyProvider,
      request: PutProviderKeyRequest,
    ) => Promise<ProviderKeySummary>
  >();
const mockDelete = vi.fn<(provider: ProviderKeyProvider) => Promise<void>>();

vi.mock("../../../api/providerKeysApi", () => ({
  listProviderKeys: () => mockList(),
  putProviderKey: (
    provider: ProviderKeyProvider,
    request: PutProviderKeyRequest,
  ) => mockPut(provider, request),
  deleteProviderKey: (provider: ProviderKeyProvider) => mockDelete(provider),
}));

import { ProviderKeys } from "./ProviderKeys";

const SAVED_ANTHROPIC: ProviderKeySummary = {
  provider: "anthropic",
  key_hint: "…7890",
  updated_at: "2026-07-01T10:00:00Z",
};

// Clearly-fake placeholder — passes the client's non-empty check only.
const FAKE_OPENAI_DRAFT = "sk-unit-test-placeholder-not-a-real-key";

beforeEach(() => {
  mockList.mockReset();
  mockPut.mockReset();
  mockDelete.mockReset();
});

describe("ProviderKeys", () => {
  it("renders saved hint + Replace/Remove for stored providers, inputs for the rest", async () => {
    mockList.mockResolvedValue({ keys: [SAVED_ANTHROPIC] });
    render(<ProviderKeys />);

    await waitFor(() => {
      expect(screen.getByText("…7890")).toBeTruthy();
    });

    // Helper copy is always visible.
    expect(
      screen.getByText(
        /encrypted at rest and only used to run your own agents/i,
      ),
    ).toBeTruthy();

    // Saved row: action buttons instead of an input.
    expect(
      screen.getByRole("button", { name: /replace anthropic key/i }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /remove anthropic key/i }),
    ).toBeTruthy();
    expect(screen.queryByPlaceholderText("sk-ant-…")).toBeNull();

    // Unsaved rows: masked password inputs.
    const openaiInput = screen.getByPlaceholderText("sk-…") as HTMLInputElement;
    expect(openaiInput.type).toBe("password");
    expect(screen.getByPlaceholderText("AIza…")).toBeTruthy();
    // OpenRouter row is present with its own placeholder.
    expect(screen.getByPlaceholderText("sk-or-v1-…")).toBeTruthy();
  });

  it("saves an OpenRouter key via PUT to the openrouter provider", async () => {
    mockList.mockResolvedValue({ keys: [] });
    mockPut.mockResolvedValue({
      provider: "openrouter",
      key_hint: "…9876",
      updated_at: "2026-07-17T09:00:00Z",
    });
    render(<ProviderKeys />);

    const input = await screen.findByPlaceholderText("sk-or-v1-…");
    expect((input as HTMLInputElement).type).toBe("password");
    fireEvent.change(input, {
      target: { value: "sk-or-v1-unit-test-placeholder-not-real" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /save openrouter key/i }),
    );

    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledWith("openrouter", {
        api_key: "sk-or-v1-unit-test-placeholder-not-real",
      });
    });
  });

  it("saves a drafted key via PUT and flips the row to saved state", async () => {
    mockList.mockResolvedValue({ keys: [] });
    mockPut.mockResolvedValue({
      provider: "openai",
      key_hint: "…l-key",
      updated_at: "2026-07-17T09:00:00Z",
    });
    render(<ProviderKeys />);

    const input = await screen.findByPlaceholderText("sk-…");
    fireEvent.change(input, { target: { value: FAKE_OPENAI_DRAFT } });
    fireEvent.click(screen.getByRole("button", { name: /save openai key/i }));

    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledTimes(1);
    });
    expect(mockPut).toHaveBeenCalledWith("openai", {
      api_key: FAKE_OPENAI_DRAFT,
    });

    // Row flipped to saved state: hint visible, input gone.
    await waitFor(() => {
      expect(screen.getByText("…l-key")).toBeTruthy();
    });
    expect(screen.queryByPlaceholderText("sk-…")).toBeNull();
    expect(
      screen.getByRole("button", { name: /remove openai key/i }),
    ).toBeTruthy();
  });

  it("save button stays disabled while the draft is empty", async () => {
    mockList.mockResolvedValue({ keys: [] });
    render(<ProviderKeys />);

    await screen.findByPlaceholderText("sk-…");
    const save = screen.getByRole("button", {
      name: /save openai key/i,
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("sk-…"), {
      target: { value: FAKE_OPENAI_DRAFT },
    });
    expect(save.disabled).toBe(false);
  });

  it("removes a stored key and flips the row back to input state", async () => {
    mockList.mockResolvedValue({ keys: [SAVED_ANTHROPIC] });
    mockDelete.mockResolvedValue(undefined);
    render(<ProviderKeys />);

    const remove = await screen.findByRole("button", {
      name: /remove anthropic key/i,
    });
    fireEvent.click(remove);

    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledWith("anthropic");
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("sk-ant-…")).toBeTruthy();
    });
    expect(screen.queryByText("…7890")).toBeNull();
  });

  it("surfaces the server's validation error verbatim and stays editable", async () => {
    mockList.mockResolvedValue({ keys: [] });
    mockPut.mockRejectedValue(
      new Error("API key format doesn't match provider openai."),
    );
    render(<ProviderKeys />);

    const input = await screen.findByPlaceholderText("sk-…");
    fireEvent.change(input, { target: { value: FAKE_OPENAI_DRAFT } });
    fireEvent.click(screen.getByRole("button", { name: /save openai key/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/format doesn't match provider openai/i),
      ).toBeTruthy();
    });
    // Row stays in input mode so the user can correct the draft.
    expect(screen.getByPlaceholderText("sk-…")).toBeTruthy();
  });

  it("Replace opens the input and Cancel returns to the saved hint", async () => {
    mockList.mockResolvedValue({ keys: [SAVED_ANTHROPIC] });
    render(<ProviderKeys />);

    const replace = await screen.findByRole("button", {
      name: /replace anthropic key/i,
    });
    fireEvent.click(replace);
    expect(screen.getByPlaceholderText("sk-ant-…")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /cancel replacing anthropic key/i }),
    );
    expect(screen.queryByPlaceholderText("sk-ant-…")).toBeNull();
    expect(screen.getByText("…7890")).toBeTruthy();
  });

  it("renders the load error when the list call fails", async () => {
    mockList.mockRejectedValue(new Error("provider keys unavailable"));
    render(<ProviderKeys />);
    await waitFor(() => {
      expect(screen.getByText(/provider keys unavailable/i)).toBeTruthy();
    });
  });
});
