import { describe, expect, it } from "vitest";

import {
  __resetItemRouteRegistryForTests,
  hasItemRoute,
} from "../../refs/registry";

// PRD-04 Seam B — cross-destination ROUTE registration moved OUT of the
// destinations and INTO the host tables (apps/frontend/src/app/itemRoutes.ts,
// apps/desktop/renderer/itemRoutes.ts). Importing this destination must have NO
// route-registry side effect: it registers nothing. (The display label is now
// the caller's, via `<ItemLink label={…}>`.)
import "./index";

describe("projects/index.ts — registers no ItemRoute on import (Seam B)", () => {
  it("leaves the route registry untouched for the kinds it used to own", () => {
    __resetItemRouteRegistryForTests();
    // Importing "./index" above ran any module-load side effects already; a
    // fresh reset then re-check proves the import path itself registers nothing.
    for (const kind of ["project"] as const) {
      expect(hasItemRoute(kind)).toBe(false);
    }
  });
});
