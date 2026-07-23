// FirstRunSurface — shell + state machine (PRD-P1 §6.3). Top bar (brand + 0x
// accent span + wallet slot + skip), state transitions (choice → dl / ready →
// sent), footer, and the P2/P3 slot injection points.
//
// PRD-P8 D4 narrows what may move the user: only an EXPLICIT gesture advances
// the stage. Download progress arriving on its own must leave the gate mounted
// (otherwise runtime states ③/④ flash for one frame and can never be seen), and
// §7 makes the composer/ack stop claiming a model is landing when it is not.

import {
  fireEvent,
  render,
  screen,
  waitFor,
  type RenderResult,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpServer, ProviderKeySummary } from "@0x-copilot/api-types";

import { FIRST_RUN_ACK_TITLES } from "./Acknowledgment";
import { FirstRunSurface, type FirstRunSurfaceProps } from "./FirstRunSurface";
import { FIRST_RUN_COPY } from "./firstRun";
import { FIRST_RUN_ACK_STALLED } from "./firstRunAckLines";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import type {
  FirstRunProfilePort,
  WalletProfileView,
} from "./ports/FirstRunProfilePort";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

function fakeProfilePort(
  view: Partial<WalletProfileView> = {},
): FirstRunProfilePort {
  return {
    get: vi.fn(() =>
      Promise.resolve({
        walletAddress: null,
        chainId: null,
        chainName: null,
        authMethod: null,
        emailIsPlaceholder: false,
        ...view,
      }),
    ),
  };
}

function connectedServer(): McpServer {
  return {
    server_id: "seed:sheets",
    name: "Google Sheets",
    display_name: "Google Sheets",
    url: "https://sheets.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    scopes_summary: "read & write workbooks",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function fakeConnectorsPort(
  over: Partial<FirstRunConnectorsPort> = {},
): FirstRunConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([connectedServer()]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn().mockResolvedValue(connectedServer()),
    addCustomServer: vi.fn().mockResolvedValue(connectedServer()),
    beginAuth: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

function fakePort(save?: ProviderKeysPort["save"]): ProviderKeysPort {
  return {
    list: vi.fn(() => Promise.resolve([])),
    save:
      save ??
      vi.fn((provider: string) =>
        Promise.resolve({
          provider: provider as ProviderKeySummary["provider"],
          key_hint: "…zzzz",
          updated_at: new Date(0).toISOString(),
        }),
      ),
    remove: vi.fn(() => Promise.resolve()),
  };
}

function renderSurface(overrides = {}) {
  const onSkip = vi.fn();
  const onComplete = vi.fn();
  render(
    <FirstRunSurface
      providerKeys={fakePort()}
      onSkip={onSkip}
      onComplete={onComplete}
      {...overrides}
    />,
  );
  return { onSkip, onComplete };
}

describe("<FirstRunSurface>", () => {
  it("renders the brand with the 0x lead in an accent span + skip link", () => {
    renderSurface();
    const brand = screen.getByTestId("first-run-brand");
    const zx = brand.querySelector(".fr-brand__zx");
    expect(zx?.textContent).toBe(FIRST_RUN_COPY.topbar.brandLead); // "0x"
    expect(brand.textContent).toContain(FIRST_RUN_COPY.topbar.brandRest); // "Copilot"
    expect(screen.getByTestId("first-run-skip").textContent).toBe(
      FIRST_RUN_COPY.topbar.skip,
    );
  });

  it("renders an injected wallet chip slot (P4) when provided", () => {
    renderSurface({
      walletChipSlot: <span data-testid="p4-chip">0x7f3C…a92C</span>,
    });
    expect(screen.getByTestId("first-run-wallet-slot")).not.toBeNull();
    expect(screen.getByTestId("p4-chip")).not.toBeNull();
  });

  it("shows the footer version (default) + privacy line", () => {
    renderSurface();
    const foot = screen.getByTestId("first-run-footer");
    expect(foot.textContent).toContain(FIRST_RUN_COPY.footer.left);
    // Pre-choice gate promises "nothing leaves this machine" (design default);
    // the "keys in OS keychain" line only appears once a key engine is chosen.
    expect(foot.textContent).toContain(FIRST_RUN_COPY.footer.rightLocal);
    expect(foot.textContent).not.toContain(FIRST_RUN_COPY.footer.right);
  });

  it("honors a custom appVersion in the footer", () => {
    renderSurface({ appVersion: "v9.9.9 · custom" });
    expect(screen.getByTestId("first-run-footer").textContent).toContain(
      "v9.9.9 · custom",
    );
  });

  it("skip calls onSkip", () => {
    const { onSkip } = renderSurface();
    fireEvent.click(screen.getByTestId("first-run-skip"));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("Start download advances to the dl composer slot (P3 placeholder)", () => {
    renderSurface();
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    // Gate is gone; the dl body (placeholder) shows.
    expect(screen.queryByTestId("first-run-gate")).toBeNull();
    expect(screen.getByTestId("first-run-composer-placeholder")).not.toBeNull();
  });

  it("KeyForm connect advances to ready with engine.kind==='key'", async () => {
    let composerEngineKind: string | undefined;
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={() => undefined}
        renderComposer={(ctx) => {
          composerEngineKind = ctx.engine?.kind;
          return (
            <div data-testid="p3-composer" data-stage={ctx.stage}>
              ready:{String(ctx.modelReady)}
            </div>
          );
        }}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-add-key"));
    fireEvent.change(screen.getByTestId("first-run-key-input"), {
      target: { value: "sk-ant-unit-test-placeholder-not-real" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));

    await waitFor(() =>
      expect(screen.getByTestId("p3-composer")).not.toBeNull(),
    );
    expect(screen.getByTestId("p3-composer").getAttribute("data-stage")).toBe(
      "ready",
    );
    expect(composerEngineKind).toBe("key");
    // A BYOK engine is model-ready immediately.
    expect(screen.getByTestId("p3-composer").textContent).toContain(
      "ready:true",
    );
  });

  it("renders injected composer + acknowledgment slots (P3)", async () => {
    const onComplete = vi.fn();
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={onComplete}
        initialStage="ready"
        renderComposer={(ctx) => (
          <button type="button" data-testid="p3-send" onClick={ctx.onSent}>
            send
          </button>
        )}
        renderAcknowledgment={(ctx) => (
          <div data-testid="p3-ack">
            <button
              type="button"
              data-testid="p3-handoff"
              onClick={ctx.onComplete}
            >
              done
            </button>
          </div>
        )}
      />,
    );

    // Composer slot shows for stage=ready.
    expect(screen.getByTestId("p3-send")).not.toBeNull();
    // Sending flips to the ack slot; the slot owns the handoff timing.
    fireEvent.click(screen.getByTestId("p3-send"));
    expect(screen.getByTestId("p3-ack")).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("p3-handoff"));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("P1 placeholder ack fires onComplete once on send (no P3 slot)", async () => {
    const { onComplete } = renderSurface({ initialStage: "ready" });
    // Placeholder composer → send.
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    await waitFor(() =>
      expect(screen.getByTestId("first-run-ack-placeholder")).not.toBeNull(),
    );
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
  });

  it("honors initialStage for tests", () => {
    renderSurface({ initialStage: "dl" });
    // Straight to the composer body — no gate.
    expect(screen.queryByTestId("first-run-gate")).toBeNull();
    expect(screen.getByTestId("first-run-composer-placeholder")).not.toBeNull();
  });

  // --- P4 integration ---------------------------------------------------

  it("renders the connected FirstRunWalletChip from an injected profilePort (P4)", async () => {
    renderSurface({
      profilePort: fakeProfilePort({
        walletAddress: "0x7f3C0000000000000000000000000000000000a92C",
        chainName: "Ethereum",
      }),
    });
    await waitFor(() =>
      expect(screen.queryByTestId("first-run-wallet-chip")).not.toBeNull(),
    );
    // The chip truncates the full EIP-55 address to the SPEC `0x{4}…{4}` form.
    expect(screen.getByTestId("first-run-wallet-chip").textContent).toContain(
      "0x7f3C…a92C",
    );
  });

  it("profilePort with no wallet (email/Google) renders no chip (P4)", async () => {
    const port = fakeProfilePort({ walletAddress: null });
    renderSurface({ profilePort: port });
    await waitFor(() => expect(port.get).toHaveBeenCalled());
    expect(screen.queryByTestId("first-run-wallet-chip")).toBeNull();
  });

  it("mounts the Tools trigger into the composer ctx when connectorsPort is set (P4)", () => {
    let sawTrigger = false;
    let ctxWebSearch: boolean | undefined;
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={() => undefined}
        initialStage="ready"
        connectorsPort={fakeConnectorsPort()}
        renderComposer={(ctx) => {
          sawTrigger = ctx.toolsTrigger !== undefined;
          ctxWebSearch = ctx.webSearchEnabled;
          return <div data-testid="p3-composer">{ctx.toolsTrigger}</div>;
        }}
      />,
    );
    expect(sawTrigger).toBe(true);
    // Web search defaults ON in the surface state.
    expect(ctxWebSearch).toBe(true);
    // The composer trigger button rendered.
    expect(screen.getByTestId("first-run-tools-button")).not.toBeNull();
  });

  it("no connectorsPort ⇒ composer ctx toolsTrigger is undefined (pre-P4 shape)", () => {
    let trigger: unknown = "sentinel";
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={() => undefined}
        initialStage="ready"
        renderComposer={(ctx) => {
          trigger = ctx.toolsTrigger;
          return <div data-testid="p3-composer">composer</div>;
        }}
      />,
    );
    expect(trigger).toBeUndefined();
  });

  it("web-search toggle flips the composer ctx webSearchEnabled (P4)", () => {
    let lastWebSearch: boolean | undefined;
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={() => undefined}
        initialStage="ready"
        connectorsPort={fakeConnectorsPort()}
        renderComposer={(ctx) => {
          lastWebSearch = ctx.webSearchEnabled;
          return <div data-testid="p3-composer">{ctx.toolsTrigger}</div>;
        }}
      />,
    );
    // Open the popover, then toggle web search OFF.
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    fireEvent.click(screen.getByTestId("first-run-tools-websearch"));
    expect(lastWebSearch).toBe(false);
  });

  it("footer-right is engine-keyed: key engine → keychain line, else → 'nothing leaves this machine' (P4)", async () => {
    renderSurface();
    const foot = () => screen.getByTestId("first-run-footer").textContent;
    // choice stage (no engine) → privacy line, not the keychain line.
    expect(foot()).toContain(FIRST_RUN_COPY.footer.rightLocal);
    expect(foot()).not.toContain(FIRST_RUN_COPY.footer.right);
    // Local download → local engine → still privacy line.
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    expect(foot()).toContain(FIRST_RUN_COPY.footer.rightLocal);
    expect(foot()).not.toContain(FIRST_RUN_COPY.footer.right);
  });

  it("footer-right shows the keychain line once a key engine is connected (P4)", async () => {
    renderSurface();
    fireEvent.click(screen.getByTestId("first-run-add-key"));
    fireEvent.change(screen.getByTestId("first-run-key-input"), {
      target: { value: "sk-ant-unit-test-placeholder-not-real" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));
    await waitFor(() =>
      expect(screen.getByTestId("first-run-footer").textContent).toContain(
        FIRST_RUN_COPY.footer.right,
      ),
    );
  });

  it("renders no raw hex in the surface except provider dot swatches", () => {
    renderSurface();
    // Reveal the KeyForm so the swatches are in the DOM.
    fireEvent.click(screen.getByTestId("first-run-add-key"));
    const root = screen.getByTestId("first-run-surface");
    const swatches = new Set(
      Array.from(root.querySelectorAll("[data-swatch]")).map(
        (el) => (el as HTMLElement).style.backgroundColor,
      ),
    );
    // Every inline style with a hex color must be a declared provider swatch.
    const withInlineHex = Array.from(
      root.querySelectorAll<HTMLElement>("[style]"),
    ).filter((el) => /#[0-9a-f]{3,8}/i.test(el.getAttribute("style") ?? ""));
    for (const el of withInlineHex) {
      expect(el.hasAttribute("data-swatch")).toBe(true);
    }
    // BrandMark's fixed sky gradient stops (#9bd4ff/#4593d8) live in an <svg>,
    // not inline style attributes — so the assertion above stays swatch-only.
    expect(swatches.size).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// PRD-P8 — D4 (only an explicit gesture advances) + §7 (no permanent "Queued")
// ---------------------------------------------------------------------------

describe("<FirstRunSurface> — PRD-P8 stage + honesty rules", () => {
  type Props = Partial<FirstRunSurfaceProps>;

  function renderP8(props: Props = {}): RenderResult & {
    readonly update: (next: Props) => void;
  } {
    const base: FirstRunSurfaceProps = {
      providerKeys: fakePort(),
      onSkip: () => undefined,
      onComplete: () => undefined,
    };
    const view = render(<FirstRunSurface {...base} {...props} />);
    return {
      ...view,
      update: (next) =>
        view.rerender(<FirstRunSurface {...base} {...props} {...next} />),
    };
  }

  /** A slot card that exposes D4a's "Continue →" as a clickable affordance. */
  const continueCard = (ctx: { readonly onContinue?: () => void }) => (
    <button type="button" data-testid="p8-continue" onClick={ctx.onContinue}>
      Continue
    </button>
  );

  it("an auto-started download never advances the stage — the gate stays mounted (D4)", () => {
    const onStartLocalDownload = vi.fn();
    const { update } = renderP8({
      onStartLocalDownload,
      localModelPct: null,
      renderLocalCard: continueCard,
    });
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();

    // The hook detected Ollama and started pulling on its own. Progress alone
    // is not a gesture: moving the user here is what made ③/④ unreachable.
    update({ localModelPct: 12 });
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();
    update({ localModelPct: 87 });
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();
    update({ localModelPct: 100 });
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();
    expect(screen.queryByTestId("first-run-composer-placeholder")).toBeNull();
    expect(onStartLocalDownload).not.toHaveBeenCalled();
  });

  it("Continue → advances to the composer WITHOUT restarting the pull (D4a)", () => {
    const onStartLocalDownload = vi.fn();
    let stage: string | undefined;
    renderP8({
      onStartLocalDownload,
      localModelPct: 40,
      renderLocalCard: continueCard,
      renderComposer: (ctx) => {
        stage = ctx.stage;
        return <div data-testid="p8-composer">composer</div>;
      },
    });

    fireEvent.click(screen.getByTestId("p8-continue"));
    expect(screen.queryByTestId("first-run-gate")).toBeNull();
    expect(screen.getByTestId("p8-composer")).not.toBeNull();
    expect(stage).toBe("dl");
    // The whole point of the second seam: the host's download starter is NOT
    // re-fired, so an in-flight pull is not torn down and restarted.
    expect(onStartLocalDownload).not.toHaveBeenCalled();
  });

  it("an explicit Start download still both starts the pull and advances", () => {
    const onStartLocalDownload = vi.fn();
    let stage: string | undefined;
    renderP8({
      onStartLocalDownload,
      renderComposer: (ctx) => {
        stage = ctx.stage;
        return <div data-testid="p8-composer">composer</div>;
      },
    });

    fireEvent.click(screen.getByTestId("first-run-start-download"));
    expect(onStartLocalDownload).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("p8-composer")).not.toBeNull();
    expect(stage).toBe("dl");
  });

  it("continuing onto an already-landed model lands on `ready`, not a download body", () => {
    let stage: string | undefined;
    renderP8({
      localModelPct: 100,
      renderLocalCard: continueCard,
      renderComposer: (ctx) => {
        stage = ctx.stage;
        return <div data-testid="p8-composer">composer</div>;
      },
    });

    fireEvent.click(screen.getByTestId("p8-continue"));
    expect(stage).toBe("ready");
  });

  it("localModelInstalled makes a local engine model-ready with no pct at all (§6)", () => {
    // The already-installed short-circuit issues no pull, so `localModelPct`
    // legitimately stays null — without this the send would queue forever
    // behind a download that will never run.
    let stage: string | undefined;
    let modelReady: boolean | undefined;
    renderP8({
      localModelInstalled: true,
      localModelPct: null,
      renderLocalCard: continueCard,
      renderComposer: (ctx) => {
        stage = ctx.stage;
        modelReady = ctx.modelReady;
        return <div data-testid="p8-composer">composer</div>;
      },
    });

    fireEvent.click(screen.getByTestId("p8-continue"));
    expect(stage).toBe("ready");
    expect(modelReady).toBe(true);
  });

  it("a local engine mid-download is NOT model-ready", () => {
    let modelReady: boolean | undefined;
    renderP8({
      localModelPct: 40,
      renderLocalCard: continueCard,
      renderComposer: (ctx) => {
        modelReady = ctx.modelReady;
        return <div data-testid="p8-composer">composer</div>;
      },
    });
    fireEvent.click(screen.getByTestId("p8-continue"));
    expect(modelReady).toBe(false);
  });

  it("threads localModelBlocked onto the composer and ack ctx (§7)", () => {
    let composerBlocked: boolean | undefined;
    let ackBlocked: boolean | undefined;
    renderP8({
      initialStage: "dl",
      localModelBlocked: true,
      renderComposer: (ctx) => {
        composerBlocked = ctx.modelBlocked;
        return (
          <button type="button" data-testid="p8-send" onClick={ctx.onSent}>
            send
          </button>
        );
      },
      renderAcknowledgment: (ctx) => {
        ackBlocked = ctx.modelBlocked;
        return <div data-testid="p8-ack">ack</div>;
      },
    });

    expect(composerBlocked).toBe(true);
    fireEvent.click(screen.getByTestId("p8-send"));
    expect(ackBlocked).toBe(true);
  });

  it("the ack stops claiming the model is landing once the download is blocked (§7)", () => {
    renderP8({ initialStage: "dl", localModelBlocked: true });
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));

    const ack = screen.getByTestId("first-run-ack-placeholder");
    expect(ack.textContent).toContain(FIRST_RUN_ACK_STALLED.title);
    expect(ack.textContent).not.toContain(FIRST_RUN_ACK_TITLES.queued);
  });

  it("the stalled ack carries a note AND an action, so it is not a nicer-worded dead end (§7)", () => {
    renderP8({ initialStage: "dl", localModelBlocked: true });
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));

    expect(screen.getByTestId("first-run-ack-note").textContent).toBe(
      FIRST_RUN_ACK_STALLED.note,
    );
    const action = screen.getByTestId("first-run-ack-back");
    expect(action.textContent).toBe(FIRST_RUN_ACK_STALLED.action);

    // And the action really returns the user to the composer, where
    // `useFirstRunLaunch.launch()` accepts a re-submit from `blocked`.
    fireEvent.click(action);
    expect(screen.getByTestId("first-run-composer-placeholder")).not.toBeNull();
  });

  it("a queued ack renders NO action — only the stalled state has one", () => {
    renderP8({ initialStage: "dl" });
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    expect(screen.queryByTestId("first-run-ack-note")).toBeNull();
    expect(screen.queryByTestId("first-run-ack-back")).toBeNull();
  });

  it("a stalled ack does NOT hand off, and resumes the handoff once the block clears (§7)", () => {
    // Completing here would drop the user into the workspace on the strength
    // of a run that never started — the same lie as the old queued title, told
    // by the navigation instead of the copy.
    const onComplete = vi.fn();
    const { update } = renderP8({
      initialStage: "dl",
      localModelBlocked: true,
      onComplete,
    });
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    expect(screen.getByTestId("first-run-ack-placeholder")).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();

    // The runtime came back and the pull resumed: the ack is a normal wait
    // again and the held send proceeds with no further gesture.
    update({ localModelBlocked: false });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("an unblocked queued send still reads as queued (§7 regression guard)", () => {
    renderP8({ initialStage: "dl" });
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    expect(screen.getByTestId("first-run-ack-placeholder").textContent).toBe(
      FIRST_RUN_ACK_TITLES.queued,
    );
  });

  it("a connected BYOK engine still reads as starting even while a local block is set", async () => {
    // The block describes the LOCAL download; a user who fell back to a key is
    // ready and must not be told their model is held.
    renderP8({ localModelBlocked: true });
    fireEvent.click(screen.getByTestId("first-run-add-key"));
    fireEvent.change(screen.getByTestId("first-run-key-input"), {
      target: { value: "sk-ant-unit-test-placeholder-not-real" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));
    await waitFor(() =>
      expect(
        screen.getByTestId("first-run-composer-placeholder"),
      ).not.toBeNull(),
    );

    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    expect(screen.getByTestId("first-run-ack-placeholder").textContent).toBe(
      FIRST_RUN_ACK_TITLES.starting,
    );
  });

  it("ack onBack returns the user to the composer so a stalled send can be re-tried (§7)", () => {
    renderP8({
      initialStage: "dl",
      localModelBlocked: true,
      renderComposer: (ctx) => (
        <button type="button" data-testid="p8-send" onClick={ctx.onSent}>
          send
        </button>
      ),
      renderAcknowledgment: (ctx) => (
        <button type="button" data-testid="p8-back" onClick={ctx.onBack}>
          back
        </button>
      ),
    });

    fireEvent.click(screen.getByTestId("p8-send"));
    expect(screen.getByTestId("p8-back")).not.toBeNull();
    expect(screen.queryByTestId("p8-send")).toBeNull();

    fireEvent.click(screen.getByTestId("p8-back"));
    expect(screen.getByTestId("p8-send")).not.toBeNull();
    expect(screen.queryByTestId("p8-back")).toBeNull();
  });
});
