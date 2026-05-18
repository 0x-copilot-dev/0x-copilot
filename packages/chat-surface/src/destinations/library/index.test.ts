import type {
  LibraryDatasetId,
  LibraryFileId,
  LibraryPageId,
} from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (each library registration guards itself
// with `hasItemRefResolver`).
import "./index";

describe("library/index.ts — ItemRef resolver registration", () => {
  it("registers resolvers for all three library kinds", () => {
    expect(hasItemRefResolver("library_file")).toBe(true);
    expect(hasItemRefResolver("library_page")).toBe(true);
    expect(hasItemRefResolver("library_dataset")).toBe(true);
  });

  it("resolves library_file to a non-null route + 'File' label", async () => {
    const resolved = await resolveItemRef({
      kind: "library_file",
      id: "lf_abc" as unknown as LibraryFileId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("File");
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.breadcrumb).toBe("Library");
  });

  it("resolves library_page to a non-null route + 'Page' label", async () => {
    const resolved = await resolveItemRef({
      kind: "library_page",
      id: "lp_abc" as unknown as LibraryPageId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Page");
    expect(resolved!.route).not.toBeNull();
  });

  it("resolves library_dataset to a non-null route + 'Dataset' label", async () => {
    const resolved = await resolveItemRef({
      kind: "library_dataset",
      id: "ld_abc" as unknown as LibraryDatasetId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Dataset");
    expect(resolved!.route).not.toBeNull();
  });
});
