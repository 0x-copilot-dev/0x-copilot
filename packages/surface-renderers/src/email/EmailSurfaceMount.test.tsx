/**
 * The mount half of the `email://` surface: what the HOST actually hands this
 * adapter, and whether the composer draws from it.
 *
 * `EmailRenderer.test.tsx` drives `renderCurrent` with a bare `EmailState`,
 * which is the shape a standalone mount and the original Phase-4 spike used. It
 * is **not** the shape the product passes. `ThreadCanvas` resolves a v2 surface
 * through `useSurfacesV2.stateFor` and hands `TcSurfaceMount` the whole surface
 * envelope — `{spec?, source?, data}` — which then calls
 * `adapter.renderCurrent(state ?? {})`.
 *
 * That gap is exactly why this renderer shipped registered and never reached:
 * nothing minted an `email://` URI, and if something had, the adapter would
 * have read `to` / `cc` / `subject` off the envelope and drawn four empty rows.
 * These tests mount the REAL `TcSurfaceMount` over the REAL registry with the
 * REAL envelope the backend now emits, so the two halves cannot drift apart
 * again without a failure here.
 *
 * The fixture below is byte-for-byte what
 * `agent_runtime.capabilities.surfaces.email_surface.EmailDraftSurface` mints
 * for `tools/desktop-journeys/local-mailbox`'s `draft_reply` — its Python
 * sibling is `test_email_surface.py::test_draft_reply_mints_the_email_surface`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  clearRegistry,
  registerAdapter,
  registerGenericStructuredDiff,
  TcSurfaceMount,
} from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";

import { recordAdapter } from "../archetypes";
import { emailStateFrom } from "./EmailRenderer";
import { registerEmailAdapter } from ".";

/** The URI the backend mints: `email://<server-slug>/<tool>/<id>`. */
const EMAIL_URI = "email://local-mailbox/draft_reply/draft-m-1041";

const BODY =
  "Hi Jordan,\n\nConfirming the locked-price block still applies.\n\nBest,\nSam";

/** The whole `SurfaceState` the surfaces endpoint serves for that URI. */
const ENVELOPE = {
  source: { server: "local-mailbox", tool: "draft_reply" },
  spec: {
    spec_version: 1,
    archetype: "record",
    source: { server: "local-mailbox", tool: "draft_reply" },
    title_path: "subject",
    subtitle_path: "to",
    fields: [
      { label: "To", path: "to", format: "user" },
      { label: "Cc", path: "cc", format: "user" },
      { label: "Subject", path: "subject" },
      { label: "Body", path: "body" },
    ],
  },
  data: {
    to: "jordan.reyes@acme.example",
    cc: "",
    subject: "Re: Renewal terms — Q4 wrap and FY27 path",
    body: BODY,
  },
};

/** `TcSurfaceMount` takes a Transport but never calls it on this path — the
 *  v2 hydration happened a layer up. Anything it did call would throw here,
 *  which is the assertion. */
const NEVER_CALLED = new Proxy({} as Transport, {
  get(_target, property) {
    throw new Error(`TcSurfaceMount touched transport.${String(property)}`);
  },
});

function mountSurface(uri: string, state: unknown): void {
  render(<TcSurfaceMount uri={uri} transport={NEVER_CALLED} state={state} />);
}

describe("emailStateFrom", () => {
  it("reads the composer state out of the surface envelope", () => {
    expect(emailStateFrom(ENVELOPE)).toEqual({
      to: "jordan.reyes@acme.example",
      cc: "",
      subject: "Re: Renewal terms — Q4 wrap and FY27 path",
      body: BODY,
    });
  });

  it("still accepts a bare EmailState (the standalone-mount shape)", () => {
    const bare = { to: "a@b.c", cc: "", subject: "s", body: "b" };
    expect(emailStateFrom(bare)).toEqual(bare);
  });

  it("carries autoSavedLabel through, and omits it when absent", () => {
    expect(
      emailStateFrom({ data: { ...ENVELOPE.data, autoSavedLabel: "Saved" } }),
    ).toHaveProperty("autoSavedLabel", "Saved");
    expect(emailStateFrom(ENVELOPE)).not.toHaveProperty("autoSavedLabel");
  });

  it("empties every slot it cannot read rather than printing undefined", () => {
    // A Cc the user never saw is the failure this surface must not have, so the
    // degradation is toward EMPTY, never toward a value from somewhere else.
    for (const hostile of [null, undefined, [], "text", 7, { data: null }]) {
      expect(emailStateFrom(hostile)).toEqual({
        to: "",
        cc: "",
        subject: "",
        body: "",
      });
    }
  });

  it("drops a non-string slot instead of stringifying it", () => {
    const state = emailStateFrom({ data: { to: ["a@b.c"], subject: 7 } });
    expect(state.to).toBe("");
    expect(state.subject).toBe("");
  });
});

describe("the email:// surface reaches EmailRenderer through TcSurfaceMount", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("mounts the composer with To / Cc / Subject / Body bound", () => {
    registerEmailAdapter();
    mountSurface(EMAIL_URI, ENVELOPE);

    // The adapter drew, not the tier-3 floor and not the placeholder.
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-tier",
      "primary",
    );
    expect(screen.getByTestId("email-renderer")).toBeInTheDocument();

    expect(screen.getByLabelText("To:")).toHaveValue(
      "jordan.reyes@acme.example",
    );
    expect(screen.getByLabelText("Cc:")).toHaveValue("");
    expect(screen.getByLabelText("Subject:")).toHaveValue(
      "Re: Renewal terms — Q4 wrap and FY27 path",
    );
    expect(screen.getByText(/Confirming the locked-price block/)).toBeVisible();
  });

  it("resolves the ember identity hue off the scheme", () => {
    // `surfaceHue.ts` reserved `email → ember` long before anything could mint
    // one. This is the assertion that the reservation is now live.
    registerEmailAdapter();
    mountSurface(EMAIL_URI, ENVELOPE);
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-surface-hue",
      "ember",
    );
  });

  it("does not steal a record:// surface from the record renderer", () => {
    registerEmailAdapter();
    registerAdapter(recordAdapter as never);
    mountSurface("record://local-mailbox/get_message/m-1041", ENVELOPE);
    expect(screen.getByTestId("record-renderer")).toBeInTheDocument();
    expect(screen.queryByTestId("email-renderer")).toBeNull();
  });

  it("degrades to the tier-3 generic view when no email adapter is registered", () => {
    // Worth pinning because the answer is NOT the one the spec suggests. The
    // registry resolves by SCHEME, so `recordAdapter` — which matches only
    // `record://` — cannot catch an `email://` surface no matter how correct
    // the spec's archetype is. The real safety net is the tier-3 wildcard both
    // hosts register FIRST (`registerGenericStructuredDiff`, before
    // `registerAll`), and without it an `email://` surface would draw "No
    // adapter registered for email". Register only tier-3 here, exactly as an
    // older host that predates the composer would have.
    registerGenericStructuredDiff();
    registerAdapter(recordAdapter as never);
    mountSurface(EMAIL_URI, ENVELOPE);
    expect(screen.queryByTestId("email-renderer")).toBeNull();
    expect(screen.getByTestId("generic-structured-diff")).toBeInTheDocument();
    // `data-tier` reads "primary", not "tier3": `resolveAdapter` falls through
    // to the wildcard bucket and RETURNS the tier-3 adapter as the primary
    // resolution, so the label describes which slot rendered, not which tier
    // the adapter belongs to. Assert on the renderer's own testid instead.
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-tier",
      "primary",
    );
  });

  it("renders an empty composer, not a crash, over an unreadable payload", () => {
    registerEmailAdapter();
    mountSurface(EMAIL_URI, { data: "not a draft at all" });
    expect(screen.getByTestId("email-renderer")).toBeInTheDocument();
    expect(screen.getByLabelText("To:")).toHaveValue("");
    expect(screen.getByLabelText("Subject:")).toHaveValue("");
  });
});
