import { describe, expect, it, vi } from "vitest";

import { TransportHttpError } from "@0x-copilot/chat-transport";
import type { Transport, TypedRequest } from "@0x-copilot/chat-transport";

import {
  attachConnectorEditor,
  CONNECTOR_EDITOR_FIELD,
  createConnectorSurfaceEditor,
  surfaceWriteBackPath,
} from "./connectorWriteBack";

const SURFACE = "table://linear/list_issues/ENG-4";

function transportWith(
  request: (item: TypedRequest) => Promise<unknown>,
): Transport {
  return {
    request: request as Transport["request"],
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

const EDIT = {
  row_key: "ENG-4",
  title: "Composer drops focus",
  row: { id: "ENG-4", status: "Todo" },
  changes: [{ field: "status", old: "Todo", new: "In Progress" }],
} as const;

function staged(overrides: Record<string, unknown> = {}): unknown {
  return {
    stage_id: "stage-1",
    surface_id: SURFACE,
    run_id: "run-1",
    draft_id: "draft-1",
    target: { connector: "linear", op: "update_issue" },
    latest_rev: 1,
    approved_rev: null,
    status: "staged",
    revisions: [],
    decisions: [],
    rows: [{ row_key: "ENG-4", title: "x", changes: [], stance: "will_apply" }],
    row_counts: { total: 1, will_apply: 1, held: 0, applied: 0, failed: 0 },
    ...overrides,
  };
}

describe("surfaceWriteBackPath", () => {
  it("encodes the surface URI whole, slashes included", () => {
    expect(surfaceWriteBackPath(SURFACE)).toBe(
      "/v1/agent/surfaces/table%3A%2F%2Flinear%2Flist_issues%2FENG-4/write-back",
    );
  });
});

describe("createConnectorSurfaceEditor", () => {
  it("POSTs the batch to the write-back route with the run that owns it", async () => {
    const request = vi.fn(async (_item: TypedRequest) => staged());
    const editor = createConnectorSurfaceEditor({
      transport: transportWith(request),
      runId: "run-1",
      surfaceId: SURFACE,
    });
    const result = await editor?.saveEdits([EDIT]);

    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toEqual({
      method: "POST",
      path: surfaceWriteBackPath(SURFACE),
      body: { run_id: "run-1", edits: [EDIT] },
    });
    expect(result).toEqual({
      status: "staged",
      stageId: "stage-1",
      rowCount: 1,
      heldCount: 0,
    });
  });

  // The whole point of the route: it returns a PROPOSAL. Nothing in this client
  // half may read that as "written" — a save the user believes happened is the
  // failure mode that matters when the target is their real Linear.
  it("never reports an apply — the result names a stage, not an outcome", async () => {
    const editor = createConnectorSurfaceEditor({
      transport: transportWith(async () => staged({ status: "applied" })),
      runId: "run-1",
      surfaceId: SURFACE,
    });
    const result = await editor?.saveEdits([EDIT]);
    expect(result?.status).toBe("staged");
  });

  it("refuses to build a grant with no run to write against", () => {
    const transport = transportWith(async () => staged());
    expect(
      createConnectorSurfaceEditor({
        transport,
        runId: null,
        surfaceId: SURFACE,
      }),
    ).toBeNull();
    expect(
      createConnectorSurfaceEditor({
        transport,
        runId: "",
        surfaceId: SURFACE,
      }),
    ).toBeNull();
    expect(
      createConnectorSurfaceEditor({
        transport,
        runId: "run-1",
        surfaceId: "",
      }),
    ).toBeNull();
  });

  it("sends nothing for an empty batch", async () => {
    const request = vi.fn(async () => staged());
    const editor = createConnectorSurfaceEditor({
      transport: transportWith(request),
      runId: "run-1",
      surfaceId: SURFACE,
    });
    const result = await editor?.saveEdits([]);
    expect(request).not.toHaveBeenCalled();
    expect(result?.status).toBe("error");
  });

  describe("failure is loud, typed, and never a throw", () => {
    const failing = (error: unknown) =>
      createConnectorSurfaceEditor({
        transport: transportWith(async () => {
          throw error;
        }),
        runId: "run-1",
        surfaceId: SURFACE,
      });

    it("names an unwired deployment on a 503", async () => {
      const result = await failing(
        new TransportHttpError(
          503,
          "unavailable",
          "Surface write-back is not configured.",
        ),
      )?.saveEdits([EDIT]);
      expect(result).toEqual({
        status: "error",
        message:
          "Saving to this connector is not configured for this deployment. Nothing was staged.",
      });
    });

    it("keeps a 404 opaque — a surface id is not an authorization capability", async () => {
      const result = await failing(
        new TransportHttpError(404, "not found", "resource not found"),
      )?.saveEdits([EDIT]);
      expect(result).toEqual({
        status: "error",
        message: "This surface is no longer available, so nothing was staged.",
      });
    });

    // A 422 is the case the user can FIX (no provider key, no write op on this
    // connector), so the server's own sentence is the one worth reading.
    it("shows the server's safe sentence on a domain refusal", async () => {
      const result = await failing(
        new TransportHttpError(
          422,
          "unprocessable",
          "This surface did not come from a connector read, so it has no write target.",
        ),
      )?.saveEdits([EDIT]);
      expect(result).toEqual({
        status: "error",
        message:
          "This surface did not come from a connector read, so it has no write target.",
      });
    });

    it("replaces a detail that is not a plain string", async () => {
      const result = await failing(
        new TransportHttpError(422, "unprocessable", { code: "x", trace: "…" }),
      )?.saveEdits([EDIT]);
      expect(result).toEqual({
        status: "error",
        message: "The save could not be prepared. Nothing was staged.",
      });
    });

    it("treats a network error as a failure, not a silent success", async () => {
      const result = await failing(new Error("offline"))?.saveEdits([EDIT]);
      expect(result?.status).toBe("error");
    });

    it("refuses a body it cannot read as a staged write", async () => {
      const editor = createConnectorSurfaceEditor({
        transport: transportWith(async () => ({ ok: true })),
        runId: "run-1",
        surfaceId: SURFACE,
      });
      const result = await editor?.saveEdits([EDIT]);
      expect(result).toEqual({
        status: "error",
        message:
          "The server did not return a staged write. Nothing was staged.",
      });
    });
  });
});

describe("attachConnectorEditor", () => {
  const editor = createConnectorSurfaceEditor({
    transport: transportWith(async () => staged()),
    runId: "run-1",
    surfaceId: SURFACE,
  });

  it("puts the grant on the render state without touching the spec", () => {
    const state = { spec: { archetype: "table" }, data: { issues: [] } };
    const next = attachConnectorEditor(state, editor) as Record<
      string,
      unknown
    >;
    expect(next[CONNECTOR_EDITOR_FIELD]).toBe(editor);
    expect(next.spec).toBe(state.spec);
    expect(next.data).toBe(state.data);
    // The input is not mutated: the hydration map holds it and a grant written
    // into that map would outlive the run it was built for.
    expect(CONNECTOR_EDITOR_FIELD in state).toBe(false);
  });

  it("returns the state untouched when there is no grant", () => {
    const state = { data: {} };
    expect(attachConnectorEditor(state, null)).toBe(state);
    expect(attachConnectorEditor(undefined, editor)).toBeUndefined();
    expect(attachConnectorEditor(null, editor)).toBeNull();
  });
});
