// Tests for <RoutineDetail /> (P5-B3).
//
// Covers every tab + the webhook URL copy-once reveal flow per
// routines-prd.md §3.4 + cross-audit §2.4 + §9.7 Q6.

import type { ItemRef, RoutineId, RunId, UserId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ClipboardPort } from "../../ports/ClipboardPort";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../refs/registry";
import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  RoutineDetail,
  type RoutineDetailItem,
  type RoutineDetailProps,
  type RoutineDetailRunHistoryState,
  type RoutineDetailTrigger,
  type RoutineDetailWebhookReveal,
} from "./RoutineDetail";

afterEach(() => {
  __resetItemRefRegistryForTests();
  vi.useRealTimers();
});

// ----- harness ---------------------------------------------------------------

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({} as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function makeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
    navigate: vi.fn(),
    subscribe: () => () => undefined,
  };
}

function harness(ui: ReactElement): ReactElement {
  return (
    <TransportProvider transport={makeTransport()}>
      <RouterProvider router={makeRouter()}>{ui}</RouterProvider>
    </TransportProvider>
  );
}

function makeClipboard(): {
  port: ClipboardPort;
  copyText: ReturnType<typeof vi.fn>;
} {
  const copyText = vi
    .fn<(text: string) => Promise<void>>()
    .mockResolvedValue(undefined);
  return { port: { copyText }, copyText };
}

// ----- fixture ---------------------------------------------------------------

const SCHEDULE_TRIGGER: RoutineDetailTrigger = {
  kind: "schedule",
  triggerId: "trg_sched_1",
  cron: "0 18 * * 1-5",
  tz: "Asia/Kolkata",
  humanPreview: "Runs weekdays at 18:00 GMT+5:30",
  nextFireSummary: "next: in 2h",
};

const WEBHOOK_TRIGGER: RoutineDetailTrigger = {
  kind: "webhook",
  triggerId: "trg_wh_1",
  urlMasked: "https://api.example.com/v1/webhook/routines/****…trg_wh_1",
  secretMasked: "****…abcd",
  secretRotatedAt: "2026-05-10T09:00:00Z",
  secretGraceUntil: null,
  ipAllowlist: ["10.0.0.0/8"],
};

const MANUAL_TRIGGER: RoutineDetailTrigger = {
  kind: "manual",
  triggerId: "trg_manual_1",
};

const EVENT_TRIGGER: RoutineDetailTrigger = {
  kind: "event",
  triggerId: "trg_evt_1",
  eventSource: "inbox.item_created",
  filterSummary: "priority = high",
};

function makeRoutine(
  overrides: Partial<RoutineDetailItem> = {},
): RoutineDetailItem {
  return {
    id: "rt_001" as RoutineId,
    name: "Daily Acme briefing",
    status: "active",
    owner: { userId: "usr_owner" as UserId, label: "Alex" },
    lastFireAt: "2026-05-17T08:00:00Z",
    lastFireStatus: "succeeded",
    triggers: [
      SCHEDULE_TRIGGER,
      WEBHOOK_TRIGGER,
      EVENT_TRIGGER,
      MANUAL_TRIGGER,
    ],
    instructionsPreview: "Summarize Acme activity since yesterday.",
    permissionsSummary: [
      "Scope: read_only",
      "Max tool calls / fire: 200",
      "Data residency: us_only",
    ],
    auditEntries: [
      {
        id: "audit_1",
        at: "2026-05-17T08:00:00Z",
        message: "Routine fired (schedule)",
      },
      {
        id: "audit_2",
        at: "2026-05-10T09:00:00Z",
        message: "Alex rotated webhook secret",
      },
    ],
    ...overrides,
  };
}

function makeProps(
  overrides: Partial<RoutineDetailProps> = {},
): RoutineDetailProps {
  const { port } = makeClipboard();
  return {
    routine: makeRoutine(),
    runHistory: { kind: "idle" },
    clipboard: port,
    ...overrides,
  };
}

// ----- tests -----------------------------------------------------------------

describe("<RoutineDetail>", () => {
  it("renders header with name, status pill, owner, and last-fired", () => {
    render(harness(<RoutineDetail {...makeProps()} />));
    expect(screen.getByTestId("routine-detail-name")).toHaveTextContent(
      "Daily Acme briefing",
    );
    expect(screen.getByTestId("routine-detail-owner")).toHaveTextContent(
      "Alex",
    );
    expect(screen.getByTestId("routine-detail-last-fired")).toHaveTextContent(
      /Last fired/,
    );
    // status pill (active) + last-fire-status pill (succeeded) present
    const pills = screen.getAllByTestId("status-pill");
    expect(pills.length).toBeGreaterThanOrEqual(2);
  });

  it("renders 'Never fired' when lastFireAt is null", () => {
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            routine: makeRoutine({
              lastFireAt: null,
              lastFireStatus: null,
            }),
          })}
        />,
      ),
    );
    expect(screen.getByTestId("routine-detail-last-fired")).toHaveTextContent(
      "Never fired",
    );
  });

  it("shows Run now / Pause buttons only when their callbacks + status match", () => {
    const onRunNow = vi.fn();
    const onPause = vi.fn();
    render(harness(<RoutineDetail {...makeProps({ onRunNow, onPause })} />));
    fireEvent.click(screen.getByTestId("routine-detail-run-now"));
    expect(onRunNow).toHaveBeenCalledWith("rt_001");
    fireEvent.click(screen.getByTestId("routine-detail-pause"));
    expect(onPause).toHaveBeenCalledWith("rt_001");
    // active routine: no Activate button
    expect(screen.queryByTestId("routine-detail-activate")).toBeNull();
  });

  it("shows Activate (not Pause) when status=paused", () => {
    const onActivate = vi.fn();
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            routine: makeRoutine({ status: "paused" }),
            onActivate,
          })}
        />,
      ),
    );
    expect(screen.queryByTestId("routine-detail-pause")).toBeNull();
    fireEvent.click(screen.getByTestId("routine-detail-activate"));
    expect(onActivate).toHaveBeenCalledWith("rt_001");
  });

  it("renders all five tabs with role=tab and aria-selected reflects active", () => {
    render(harness(<RoutineDetail {...makeProps()} />));
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(5);
    expect(screen.getByTestId("routine-detail-tab-overview")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByTestId("routine-detail-tab-run-history"),
    ).toHaveAttribute("aria-selected", "false");
  });

  it("Overview tab shows instructions + a row per trigger kind", () => {
    render(harness(<RoutineDetail {...makeProps()} />));
    expect(
      screen.getByTestId("routine-overview-instructions"),
    ).toHaveTextContent("Summarize Acme activity");
    const triggers = screen.getByTestId("routine-overview-triggers");
    expect(triggers).toHaveTextContent("Runs weekdays at 18:00");
    expect(triggers).toHaveTextContent("inbox.item_created");
    expect(triggers).toHaveTextContent("Manual fire only");
  });

  it("switches to the Run history tab and renders idle by default", () => {
    render(harness(<RoutineDetail {...makeProps()} />));
    fireEvent.click(screen.getByTestId("routine-detail-tab-run-history"));
    expect(screen.getByTestId("routine-run-history")).toHaveTextContent(
      "not yet loaded",
    );
  });

  it("Run history tab surfaces loading / error / ready states; error retry fires", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "run-history",
            runHistory: { kind: "loading" },
            onRetryRunHistory: onRetry,
          })}
        />,
      ),
    );
    expect(screen.getByText(/Loading run history/)).toBeInTheDocument();

    rerender(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "run-history",
            runHistory: { kind: "error", message: "boom" },
            onRetryRunHistory: onRetry,
          })}
        />,
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByTestId("routine-run-history-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);

    // Empty ready state.
    rerender(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "run-history",
            runHistory: { kind: "ready", runs: [] },
          })}
        />,
      ),
    );
    expect(screen.getByTestId("routine-run-history")).toHaveTextContent(
      "No runs yet",
    );
  });

  it("Run history rows render through <ItemLink> (cross-audit §3.3)", async () => {
    registerItemRefResolver("run", async (id) => ({
      label: `Run ${id}`,
      icon: null,
      route: { kind: "run", runId: id } as ArtifactRoute,
      breadcrumb: "Runs",
    }));
    const runs: RoutineDetailRunHistoryState = {
      kind: "ready",
      runs: [
        {
          id: "run_abc" as RunId,
          ref: { kind: "run", id: "run_abc" as RunId } as ItemRef,
          status: "succeeded",
          startedAt: "2026-05-17T08:00:00Z",
          triggerLabel: "schedule",
        },
      ],
    };
    render(
      harness(
        <RoutineDetail
          {...makeProps({ initialTab: "run-history", runHistory: runs })}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("item-link")).toBeInTheDocument();
    });
    expect(screen.getByTestId("routine-run-history-row")).toHaveAttribute(
      "data-run-id",
      "run_abc",
    );
  });

  it("Triggers tab renders one card per trigger kind with kind-specific affordances", () => {
    render(
      harness(<RoutineDetail {...makeProps({ initialTab: "triggers" })} />),
    );
    expect(screen.getByTestId("routine-trigger-schedule")).toHaveTextContent(
      "0 18 * * 1-5",
    );
    expect(screen.getByTestId("routine-trigger-schedule")).toHaveTextContent(
      "Asia/Kolkata",
    );
    expect(screen.getByTestId("routine-trigger-webhook")).toBeInTheDocument();
    expect(screen.getByTestId("routine-trigger-event")).toHaveTextContent(
      "inbox.item_created",
    );
    expect(screen.getByTestId("routine-trigger-manual")).toBeInTheDocument();
  });

  it("webhook URL is masked by default and there is no clear secret in the DOM", () => {
    render(
      harness(<RoutineDetail {...makeProps({ initialTab: "triggers" })} />),
    );
    expect(screen.getByTestId("routine-webhook-url-masked")).toHaveTextContent(
      "****",
    );
    expect(screen.queryByTestId("routine-webhook-url-clear")).toBeNull();
    expect(screen.queryByTestId("routine-webhook-secret-clear")).toBeNull();
    expect(
      screen.getByTestId("routine-webhook-secret-masked"),
    ).toHaveTextContent("****");
    expect(screen.getByTestId("routine-trigger-webhook")).toHaveAttribute(
      "data-reveal-active",
      "false",
    );
  });

  it("copying the masked URL goes through ClipboardPort (cross-audit §1.2)", async () => {
    const { port, copyText } = makeClipboard();
    render(
      harness(
        <RoutineDetail
          {...makeProps({ initialTab: "triggers", clipboard: port })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("routine-webhook-copy-url"));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledTimes(1);
    });
    expect(copyText).toHaveBeenCalledWith(
      WEBHOOK_TRIGGER.kind === "webhook" ? WEBHOOK_TRIGGER.urlMasked : "",
    );
  });

  it("reveal window: full URL + secret + Copy button (uses ClipboardPort), then dismiss clears", async () => {
    const { port, copyText } = makeClipboard();
    const onDismiss = vi.fn();
    const reveal: RoutineDetailWebhookReveal = {
      triggerId: "trg_wh_1",
      url: "https://api.example.com/v1/webhook/routines/trg_wh_1?secret=ROT_NEW",
      secret: "ROT_NEW_CLEARTEXT_SECRET",
    };
    const { rerender } = render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            clipboard: port,
            webhookReveal: reveal,
            onDismissWebhookReveal: onDismiss,
          })}
        />,
      ),
    );

    expect(screen.getByTestId("routine-webhook-url-clear")).toHaveTextContent(
      reveal.url,
    );
    expect(
      screen.getByTestId("routine-webhook-secret-clear"),
    ).toHaveTextContent(reveal.secret);
    expect(screen.getByTestId("routine-trigger-webhook")).toHaveAttribute(
      "data-reveal-active",
      "true",
    );

    // Copy URL through ClipboardPort.
    fireEvent.click(screen.getByTestId("routine-webhook-copy-url"));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(reveal.url);
    });
    // After copy, the button reads "Copied".
    expect(screen.getByTestId("routine-webhook-copy-url")).toHaveTextContent(
      "Copied",
    );

    // Copy secret through ClipboardPort.
    fireEvent.click(screen.getByTestId("routine-webhook-copy-secret"));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(reveal.secret);
    });

    // Dismiss banner.
    fireEvent.click(screen.getByTestId("routine-webhook-reveal-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // Host re-renders with webhookReveal=null → secret goes back to masked.
    rerender(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            clipboard: port,
            webhookReveal: null,
          })}
        />,
      ),
    );
    expect(screen.queryByTestId("routine-webhook-url-clear")).toBeNull();
    expect(screen.queryByTestId("routine-webhook-secret-clear")).toBeNull();
    expect(
      screen.getByTestId("routine-webhook-secret-masked"),
    ).toBeInTheDocument();
  });

  it("rotate button fires onRotate(routineId, triggerId) and shows pending state", () => {
    const onRotate = vi.fn();
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            onRotateWebhookSecret: onRotate,
          })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("routine-webhook-rotate"));
    expect(onRotate).toHaveBeenCalledWith("rt_001", "trg_wh_1");
  });

  it("rotate button shows 'Rotating…' when pending set includes the trigger key", () => {
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            onRotateWebhookSecret: vi.fn(),
            pending: new Set(["rotate:trg_wh_1"]),
          })}
        />,
      ),
    );
    const btn = screen.getByTestId("routine-webhook-rotate");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Rotating…");
  });

  it("grace-window chip renders when secret_grace_until is in the future (cross-audit §2.4)", () => {
    const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 3).toISOString();
    const graced: RoutineDetailTrigger = {
      kind: "webhook",
      triggerId: "trg_wh_1",
      urlMasked: "https://api.example.com/v1/webhook/routines/****",
      secretMasked: "****abcd",
      secretRotatedAt: new Date().toISOString(),
      secretGraceUntil: future,
      ipAllowlist: [],
    };
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            routine: makeRoutine({ triggers: [graced] }),
          })}
        />,
      ),
    );
    const card = screen.getByTestId("routine-trigger-webhook");
    expect(card).toHaveTextContent(/grace until/);
  });

  it("ClipboardPort failures route to onCopyError (no uncaught rejection)", async () => {
    const onCopyError = vi.fn();
    const failing: ClipboardPort = {
      copyText: () => Promise.reject(new Error("denied")),
    };
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "triggers",
            clipboard: failing,
            onCopyError,
          })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("routine-webhook-copy-url"));
    await waitFor(() => {
      expect(onCopyError).toHaveBeenCalledWith("denied");
    });
  });

  it("Permissions tab renders one row per summary line", () => {
    render(
      harness(<RoutineDetail {...makeProps({ initialTab: "permissions" })} />),
    );
    expect(screen.getAllByTestId("routine-permissions-row")).toHaveLength(3);
    expect(screen.getByTestId("routine-permissions")).toHaveTextContent(
      "read_only",
    );
  });

  it("Permissions tab shows empty state when summary is empty", () => {
    render(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "permissions",
            routine: makeRoutine({ permissionsSummary: [] }),
          })}
        />,
      ),
    );
    expect(screen.getByTestId("routine-permissions")).toHaveTextContent(
      "No permissions configured",
    );
  });

  it("Audit tab renders rows with time + message; empty state otherwise", () => {
    const { rerender } = render(
      harness(<RoutineDetail {...makeProps({ initialTab: "audit" })} />),
    );
    const rows = screen.getAllByTestId("routine-audit-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Routine fired (schedule)");

    rerender(
      harness(
        <RoutineDetail
          {...makeProps({
            initialTab: "audit",
            routine: makeRoutine({ auditEntries: [] }),
          })}
        />,
      ),
    );
    expect(screen.getByTestId("routine-audit")).toHaveTextContent(
      "No audit entries yet",
    );
  });

  it("controlled tab mode: activeTab + onTabChange round-trip", () => {
    const onTabChange = vi.fn();
    const { rerender } = render(
      harness(
        <RoutineDetail
          {...makeProps({
            activeTab: "overview",
            onTabChange,
          })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("routine-detail-tab-audit"));
    expect(onTabChange).toHaveBeenCalledWith("audit");
    // Internal state did NOT advance because the host owns the value.
    expect(screen.getByTestId("routine-detail-tab-overview")).toHaveAttribute(
      "aria-selected",
      "true",
    );

    rerender(
      harness(
        <RoutineDetail
          {...makeProps({
            activeTab: "audit",
            onTabChange,
          })}
        />,
      ),
    );
    expect(screen.getByTestId("routine-detail-tab-audit")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
