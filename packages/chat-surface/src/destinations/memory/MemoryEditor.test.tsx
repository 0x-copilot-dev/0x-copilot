// MemoryEditor tests (P12-B2).
//
// Covers: create flow (full CreateMemoryRequest body), update flow
// emits only-changed-fields patch, scope/kind toggle works, validation
// disables Save on empty title/body, ARIA roles.

import type {
  CreateMemoryRequest,
  MemoryItem,
  MemoryItemId,
  TenantId,
  UpdateMemoryRequest,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MemoryEditor, type MemoryEditorSavePayload } from "./MemoryEditor";

const asMemoryId = (s: string): MemoryItemId => s as unknown as MemoryItemId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;

function makeMemory(over: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: asMemoryId("mem_1"),
    tenant_id: asTenantId("tnt_1"),
    scope: over.scope ?? "user",
    kind: over.kind ?? "fact",
    title: over.title ?? "Java background",
    body: over.body ?? "Built Hadoop pipelines.",
    tags: over.tags ?? ["work", "history"],
    created_by: over.created_by ?? {
      kind: "user",
      id: asUserId("usr_self") as unknown as string,
    },
    last_used_at: over.last_used_at ?? null,
    created_at: over.created_at ?? "2026-05-01T00:00:00.000Z",
    updated_at: over.updated_at ?? "2026-05-16T00:00:00.000Z",
    project_id: over.project_id ?? null,
  };
}

describe("MemoryEditor — create flow", () => {
  it("emits a CreateMemoryRequest on submit", () => {
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor onSave={onSave} />);

    fireEvent.change(screen.getByTestId("memory-editor-title"), {
      target: { value: "I'm a Python developer" },
    });
    fireEvent.change(screen.getByTestId("memory-editor-body"), {
      target: { value: "Prefer Python 3.13." },
    });
    fireEvent.change(screen.getByTestId("memory-editor-tags"), {
      target: { value: "python, work" },
    });

    fireEvent.click(screen.getByTestId("memory-editor-save"));

    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("create");
    if (payload.mode === "create") {
      const expected: CreateMemoryRequest = {
        scope: "user",
        kind: "fact",
        title: "I'm a Python developer",
        body: "Prefer Python 3.13.",
        tags: ["python", "work"],
      };
      expect(payload.body).toEqual(expected);
    }
  });

  it("disables Save when title or body is empty", () => {
    render(<MemoryEditor onSave={vi.fn()} />);
    const saveBtn = screen.getByTestId("memory-editor-save");
    expect(saveBtn).toHaveAttribute("aria-disabled", "true");

    fireEvent.change(screen.getByTestId("memory-editor-title"), {
      target: { value: "hi" },
    });
    // Still no body → still disabled.
    expect(saveBtn).toHaveAttribute("aria-disabled", "true");

    fireEvent.change(screen.getByTestId("memory-editor-body"), {
      target: { value: "world" },
    });
    expect(saveBtn).toHaveAttribute("aria-disabled", "false");
  });

  it("toggling scope to workspace propagates into the CreateMemoryRequest", () => {
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor onSave={onSave} />);

    fireEvent.change(screen.getByTestId("memory-editor-title"), {
      target: { value: "T" },
    });
    fireEvent.change(screen.getByTestId("memory-editor-body"), {
      target: { value: "B" },
    });
    // Scope toggle — Workspace tab. The kind row uses idPrefix
    // "memory-editor-kind" and scope uses "memory-editor-scope"; we
    // look up scope's workspace tab specifically via the tablist
    // accessible name.
    const scopeTablist = screen.getByRole("tablist", {
      name: /memory scope/i,
    });
    const workspaceTab = scopeTablist.querySelector(
      '[data-testid="filter-tab-workspace"]',
    )!;
    fireEvent.click(workspaceTab);

    fireEvent.click(screen.getByTestId("memory-editor-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("create");
    if (payload.mode === "create") {
      expect(payload.body.scope).toBe("workspace");
    }
  });
});

describe("MemoryEditor — update flow (only-changed-fields patch)", () => {
  it("emits an empty patch when nothing changed", () => {
    const initial = makeMemory();
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor initial={initial} onSave={onSave} />);
    fireEvent.click(screen.getByTestId("memory-editor-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("update");
    if (payload.mode === "update") {
      expect(payload.id).toBe(initial.id);
      expect(payload.patch).toEqual({} as UpdateMemoryRequest);
    }
  });

  it("includes only the title field when only the title changed", () => {
    const initial = makeMemory();
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor initial={initial} onSave={onSave} />);

    fireEvent.change(screen.getByTestId("memory-editor-title"), {
      target: { value: "Python background" },
    });
    fireEvent.click(screen.getByTestId("memory-editor-save"));

    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("update");
    if (payload.mode === "update") {
      expect(payload.patch).toEqual({ title: "Python background" });
      expect(payload.patch.body).toBeUndefined();
      expect(payload.patch.tags).toBeUndefined();
      expect(payload.patch.scope).toBeUndefined();
    }
  });

  it("includes scope only when scope flipped, not when scope unchanged", () => {
    const initial = makeMemory({ scope: "user" });
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor initial={initial} onSave={onSave} />);

    // Flip scope to workspace.
    const scopeTablist = screen.getByRole("tablist", { name: /memory scope/i });
    fireEvent.click(
      scopeTablist.querySelector('[data-testid="filter-tab-workspace"]')!,
    );
    fireEvent.click(screen.getByTestId("memory-editor-save"));

    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("update");
    if (payload.mode === "update") {
      expect(payload.patch).toEqual({ scope: "workspace" });
    }
  });

  it("treats reordered tags as unchanged (multiset compare)", () => {
    const initial = makeMemory({ tags: ["a", "b"] });
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor initial={initial} onSave={onSave} />);

    // Same multiset, different order.
    fireEvent.change(screen.getByTestId("memory-editor-tags"), {
      target: { value: "b, a" },
    });
    fireEvent.click(screen.getByTestId("memory-editor-save"));

    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("update");
    if (payload.mode === "update") {
      expect(payload.patch.tags).toBeUndefined();
    }
  });

  it("emits a tags patch when the multiset differs", () => {
    const initial = makeMemory({ tags: ["a", "b"] });
    const onSave = vi.fn<(p: MemoryEditorSavePayload) => void>();
    render(<MemoryEditor initial={initial} onSave={onSave} />);

    fireEvent.change(screen.getByTestId("memory-editor-tags"), {
      target: { value: "a, b, c" },
    });
    fireEvent.click(screen.getByTestId("memory-editor-save"));

    const payload = onSave.mock.calls[0][0];
    expect(payload.mode).toBe("update");
    if (payload.mode === "update") {
      expect(payload.patch.tags).toEqual(["a", "b", "c"]);
    }
  });

  it("has the correct ARIA roles (form + alert when error)", () => {
    const initial = makeMemory();
    render(
      <MemoryEditor
        initial={initial}
        onSave={vi.fn()}
        error="Something went wrong"
      />,
    );
    expect(
      screen.getByRole("form", { name: /edit memory/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /something went wrong/i,
    );
  });
});
