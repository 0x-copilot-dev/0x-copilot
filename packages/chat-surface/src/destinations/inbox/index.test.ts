import type { InboxItemId } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the inbox registration guards itself
// with `hasItemRefResolver`).
import "./index";

describe("inbox/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `inbox_item`", () => {
    expect(hasItemRefResolver("inbox_item")).toBe(true);
  });

  it("resolves an inbox_item ref to a non-null route + 'Inbox item' label", async () => {
    const resolved = await resolveItemRef({
      kind: "inbox_item",
      id: "inbox_abc" as InboxItemId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Inbox item");
    // P4-B2 / P4-C will introduce a dedicated `{ kind: "inbox-detail",
    // inboxItemId }` route variant and replace this resolver. Until
    // then the workspace route is the stable fallback so <ItemLink>
    // renders a real link rather than the deleted-chip.
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "inbox_abc",
    });
    expect(resolved!.breadcrumb).toBe("Inbox");
  });
});
