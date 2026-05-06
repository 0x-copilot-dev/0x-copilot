/**
 * PR 4.5 — SharePopover behavioural tests.
 *
 * Verifies copy / Slack / email handlers and the disabled-fieldset tooltip.
 * `@testing-library/user-event` drives clicks so the Radix popover opens
 * with the right pointer-event sequence.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SharePopover } from "./SharePopover";

const _writeText = vi.fn();
const _windowOpen = vi.fn();

const ORIGINAL_LOCATION_HREF = window.location.href;

beforeEach(() => {
  _writeText.mockReset();
  _windowOpen.mockReset();
  Object.assign(navigator, {
    clipboard: { writeText: _writeText },
  });
  window.open = _windowOpen as typeof window.open;
  // jsdom guards `window.location.href` setter — stub via assigning the whole
  // object so the email handler's `window.location.href = …` doesn't throw.
  Object.defineProperty(window, "location", {
    writable: true,
    value: {
      ...window.location,
      href: ORIGINAL_LOCATION_HREF,
      assign: () => undefined,
      reload: () => undefined,
      replace: () => undefined,
    },
  });
});

afterEach(() => {
  window.location.href = ORIGINAL_LOCATION_HREF;
});

async function openPopover(): Promise<void> {
  const trigger = screen.getByRole("button", {
    name: /share this conversation/i,
  });
  await userEvent.click(trigger);
  await screen.findByTestId("share-popover");
}

describe("SharePopover", () => {
  it("copies the chat URL on click", async () => {
    _writeText.mockResolvedValue(undefined);
    const onStatus = vi.fn();
    render(
      <SharePopover
        chatTitle="Demo"
        chatUrl="https://app.example.com/c/abc"
        onStatus={onStatus}
      />,
    );
    await openPopover();
    fireEvent.click(screen.getByText(/copy link/i));
    await waitFor(() => expect(_writeText).toHaveBeenCalled());
    expect(_writeText).toHaveBeenCalledWith("https://app.example.com/c/abc");
    expect(onStatus).toHaveBeenCalledWith("Chat link copied.");
    await screen.findByText(/copied/i);
  });

  it("opens Slack with a deep-link", async () => {
    _writeText.mockResolvedValue(undefined);
    render(
      <SharePopover chatTitle="Demo" chatUrl="https://app.example.com/c/abc" />,
    );
    await openPopover();
    fireEvent.click(screen.getByText(/share to slack/i));
    expect(_windowOpen).toHaveBeenCalledTimes(1);
    const url = String(_windowOpen.mock.calls[0]?.[0] ?? "");
    expect(url.startsWith("slack://share?")).toBe(true);
    expect(url).toContain(encodeURIComponent("https://app.example.com/c/abc"));
  });

  it("opens email with a mailto link", async () => {
    render(
      <SharePopover chatTitle="Demo" chatUrl="https://app.example.com/c/abc" />,
    );
    await openPopover();
    fireEvent.click(screen.getByText(/share to email/i));
    expect(window.location.href.startsWith("mailto:")).toBe(true);
    expect(window.location.href).toContain("Demo");
  });

  it("renders a hint alongside the disabled fieldset", async () => {
    render(
      <SharePopover chatTitle="Demo" chatUrl="https://app.example.com/c/abc" />,
    );
    await openPopover();
    expect(
      screen.getByText(/send a message to share this chat/i),
    ).toBeVisible();
    // Both view-access radios are disabled.
    const radios = screen.getAllByRole("radio");
    expect(radios.length).toBeGreaterThan(0);
    radios.forEach((radio) => expect(radio).toBeDisabled());
  });

  it("falls back to a status message when clipboard is unavailable", async () => {
    Object.assign(navigator, { clipboard: undefined });
    const onStatus = vi.fn();
    render(
      <SharePopover
        chatTitle="Demo"
        chatUrl="https://app.example.com/c/abc"
        onStatus={onStatus}
      />,
    );
    await openPopover();
    fireEvent.click(screen.getByText(/copy link/i));
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith(
        "Copy this page URL to share the chat.",
      ),
    );
  });
});
