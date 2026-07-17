// Tests for <LibraryDetailView /> (P7-B2).
//
// Covers the unified detail dispatch (file / page / dataset), the
// header chip row, the action row callbacks, and audit history with
// embedded <ItemLink> cross-refs.

import type {
  ConversationId,
  ItemRef,
  LibraryFileId,
  LibraryPageId,
  LibraryDatasetId,
} from "@0x-copilot/api-types";
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

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../refs/registry";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  LibraryDetailView,
  type LibraryDatasetDetailItem,
  type LibraryDetailViewProps,
  type LibraryFileDetailItem,
  type LibraryPageDetailItem,
} from "./LibraryDetailView";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({} as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => undefined }),
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

function makeFileItem(
  overrides: Partial<LibraryFileDetailItem> = {},
): LibraryFileDetailItem {
  return {
    kind: "file",
    id: "lib_f_1",
    title: "Retention deep dive — Acme.pdf",
    source: {
      kind: "agent_save",
      label: "Saved from a chat 6d ago",
    },
    project: { projectId: "proj_acme", label: "Acme renewal" },
    tags: ["retention", "acme"],
    indexStatus: "indexed",
    indexError: null,
    createdAt: "2026-05-12T09:00:00Z",
    updatedAt: "2026-05-17T10:00:00Z",
    updatedRelative: "updated 6h ago",
    sizeLabel: "2.4 MB",
    auditEntries: [
      {
        id: "a1",
        at: "2026-05-12T09:00:00Z",
        message: "Sarah saved this from a chat",
      },
    ],
    mimeLabel: "PDF document",
    fileKind: "pdf",
    ...overrides,
  };
}

function makePageItem(
  overrides: Partial<LibraryPageDetailItem> = {},
): LibraryPageDetailItem {
  return {
    kind: "page",
    id: "lib_p_1",
    title: "Cohort retention — Q3 product review",
    source: { kind: "agent_save", label: "Saved from a chat 2w ago" },
    project: null,
    tags: [],
    indexStatus: "indexed",
    indexError: null,
    createdAt: "2026-05-01T09:00:00Z",
    updatedAt: "2026-05-12T09:00:00Z",
    updatedRelative: "updated 5d ago",
    sizeLabel: "1.2k words",
    auditEntries: [],
    markdown: "# Heading\n\nBody text.",
    version: 3,
    versionEtag: "etag-3",
    ...overrides,
  };
}

function makeDatasetItem(
  overrides: Partial<LibraryDatasetDetailItem> = {},
): LibraryDatasetDetailItem {
  return {
    kind: "dataset",
    id: "lib_d_1",
    title: "Q3 forecast",
    source: { kind: "user_upload", label: "You uploaded 3w ago" },
    project: null,
    tags: ["forecast"],
    indexStatus: "indexed",
    indexError: null,
    createdAt: "2026-04-25T09:00:00Z",
    updatedAt: "2026-04-25T09:00:00Z",
    updatedRelative: "updated 3w ago",
    sizeLabel: "200 rows",
    auditEntries: [],
    schema: [
      { name: "region", type: "string", nullable: false },
      { name: "amount", type: "float", nullable: true },
    ],
    rowCount: 200,
    format: "csv",
    ...overrides,
  };
}

function makeProps(
  overrides: Partial<LibraryDetailViewProps>,
): LibraryDetailViewProps {
  return {
    item: makeFileItem(),
    ...overrides,
  };
}

describe("<LibraryDetailView>", () => {
  it("renders header with title, kind/source/project/size chips", () => {
    render(
      harness(<LibraryDetailView {...makeProps({ item: makeFileItem() })} />),
    );
    expect(screen.getByText("Retention deep dive — Acme.pdf")).toBeTruthy();
    const chips = screen.getByTestId("library-detail-chips");
    expect(chips.textContent).toContain("File");
    expect(chips.textContent).toContain("From agent");
    expect(chips.textContent).toContain("Acme renewal");
    expect(chips.textContent).toContain("2.4 MB");
    expect(chips.textContent).toContain("updated 6h ago");
  });

  it("dispatches to FilePreview for kind=file", () => {
    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makeFileItem(),
            filePreview: {
              state: {
                kind: "ready",
                signedUrl: "https://signed.example/pdf",
              },
            },
          })}
        />,
      ),
    );
    expect(screen.getByTestId("library-file-preview")).toBeTruthy();
    expect(screen.getByTestId("library-file-preview-pdf")).toBeTruthy();
  });

  it("dispatches to PagePreview for kind=page", () => {
    render(
      harness(<LibraryDetailView {...makeProps({ item: makePageItem() })} />),
    );
    expect(screen.getByTestId("library-page-preview")).toBeTruthy();
  });

  it("dispatches to DatasetPreview for kind=dataset", () => {
    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makeDatasetItem(),
            datasetPreview: {
              state: {
                kind: "ready",
                rows: [
                  { region: "US", amount: 100.5 },
                  { region: "EU", amount: null },
                ],
                totalRows: 200,
              },
            },
          })}
        />,
      ),
    );
    expect(screen.getByTestId("library-dataset-preview")).toBeTruthy();
    expect(screen.getAllByTestId("library-dataset-preview-row").length).toBe(2);
  });

  it("renders pageEditor in place of preview when supplied", () => {
    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makePageItem(),
            pageEditor: <div data-testid="custom-editor">EDITOR</div>,
          })}
        />,
      ),
    );
    expect(screen.getByTestId("custom-editor")).toBeTruthy();
    // PagePreview should NOT render when editor is supplied.
    expect(screen.queryByTestId("library-page-preview")).toBeNull();
  });

  it("calls onDownload only for file/dataset and onEdit only for page", () => {
    const onDownload = vi.fn();
    const onEdit = vi.fn();

    // Page: shows Edit, hides Download.
    const { unmount } = render(
      harness(
        <LibraryDetailView
          {...makeProps({ item: makePageItem(), onEdit, onDownload })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("library-detail-action-edit"));
    expect(onEdit).toHaveBeenCalledWith("lib_p_1");
    expect(screen.queryByTestId("library-detail-action-download")).toBeNull();
    unmount();

    // File: shows Download, hides Edit.
    render(
      harness(
        <LibraryDetailView
          {...makeProps({ item: makeFileItem(), onEdit, onDownload })}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("library-detail-action-download"));
    expect(onDownload).toHaveBeenCalledWith("lib_f_1");
    expect(screen.queryByTestId("library-detail-action-edit")).toBeNull();
  });

  it("shows index-failed banner with retry callback", () => {
    const onRetryIndex = vi.fn();
    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makeFileItem({
              indexStatus: "failed",
              indexError: "embedding model timeout",
            }),
            onRetryIndex,
          })}
        />,
      ),
    );
    expect(
      screen.getByText(/Indexing failed: embedding model timeout/),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("library-detail-retry-index"));
    expect(onRetryIndex).toHaveBeenCalledWith("lib_f_1");
  });

  it("renders <ItemLink> chips for audit-row cross-refs", async () => {
    const ref: ItemRef = {
      kind: "chat",
      id: "conv_42" as ConversationId,
    };
    registerItemRefResolver("chat", async (id) => ({
      label: `Chat ${id}`,
      icon: null,
      route: { kind: "chat", conversationId: id } as ArtifactRoute,
    }));

    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makeFileItem({
              auditEntries: [
                {
                  id: "audit_1",
                  at: "2026-05-12T09:00:00Z",
                  message: "Saved from chat",
                  refs: [ref],
                },
              ],
            }),
          })}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("item-link")).toBeTruthy();
    });
    expect(screen.getByTestId("item-link").getAttribute("data-item-id")).toBe(
      "conv_42",
    );
  });

  it("renders crossRefs section with <ItemLink> rows", async () => {
    const ref: ItemRef = {
      kind: "chat",
      id: "conv_7" as ConversationId,
    };
    registerItemRefResolver("chat", async (id) => ({
      label: `Chat ${id}`,
      icon: null,
      route: { kind: "chat", conversationId: id } as ArtifactRoute,
    }));

    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makePageItem({
              crossRefs: { summary: "Cited in 1 chat", refs: [ref] },
            }),
          })}
        />,
      ),
    );
    expect(
      screen.getByTestId("library-detail-cross-refs").textContent,
    ).toContain("Cited in 1 chat");
    await waitFor(() => {
      expect(screen.getByTestId("item-link")).toBeTruthy();
    });
  });

  it("calls onBack when provided", () => {
    const onBack = vi.fn();
    render(harness(<LibraryDetailView {...makeProps({ onBack })} />));
    fireEvent.click(screen.getByTestId("library-detail-back"));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("disables a button while its pending key is in the pending set", () => {
    const onDownload = vi.fn();
    render(
      harness(
        <LibraryDetailView
          {...makeProps({
            item: makeFileItem(),
            onDownload,
            pending: new Set(["download" as const]),
          })}
        />,
      ),
    );
    const btn = screen.getByTestId(
      "library-detail-action-download",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain("Preparing");
  });
});
