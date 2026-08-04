import { act, render, screen } from "@testing-library/react";
import { type ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import type { ShellWidthClass } from "../../shell/layout";
import type { KeyValueStore } from "../../storage/key-value-store";

import {
  threadSwitcherOpenKey,
  useThreadSwitcherOpen,
} from "./useThreadSwitcherOpen";

function memoryStore(seed: Record<string, string> = {}): KeyValueStore {
  const map = new Map(Object.entries(seed));
  return {
    get: (k) => map.get(k) ?? null,
    set: (k, v) => {
      if (v === null) map.delete(k);
      else map.set(k, v);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (k) => prefix === undefined || k.startsWith(prefix),
      ),
  };
}

let toggleFn: () => void;

function Probe({ widthClass }: { widthClass: ShellWidthClass }): ReactElement {
  const { open, toggle } = useThreadSwitcherOpen(widthClass);
  toggleFn = toggle;
  return <span data-testid="open">{open ? "open" : "closed"}</span>;
}

function mount(widthClass: ShellWidthClass, store: KeyValueStore) {
  return render(
    <KeyValueStoreProvider store={store}>
      <Probe widthClass={widthClass} />
    </KeyValueStoreProvider>,
  );
}

const openState = () => screen.getByTestId("open").textContent;

describe("useThreadSwitcherOpen", () => {
  it("defaults closed at every width class", () => {
    const store = memoryStore();
    // `wide` included: the cockpit opens on the run, not on a list of runs.
    for (const widthClass of ["wide", "regular", "compact"] as const) {
      const view = mount(widthClass, store);
      expect(openState(), widthClass).toBe("closed");
      view.unmount();
    }
    // A default is not a preference — nothing is written until someone toggles.
    expect(store.keys("")).toEqual([]);
  });

  it("persists per width class, not globally (FR-1.4)", () => {
    const store = memoryStore();

    // Open it at wide.
    const a = mount("wide", store);
    act(() => toggleFn());
    expect(openState()).toBe("open");
    a.unmount();

    // regular is a SEPARATE decision — it keeps its own default.
    const b = mount("regular", store);
    expect(openState()).toBe("closed");
    b.unmount();

    // …and wide still remembers the open, which regular never learned.
    mount("wide", store);
    expect(openState()).toBe("open");
    expect(store.get(threadSwitcherOpenKey("wide"))).toBe("1");
    expect(store.get(threadSwitcherOpenKey("regular"))).toBeNull();
  });

  it("never persists compact and always opens it closed (FR-1.5)", () => {
    const store = memoryStore();
    const a = mount("compact", store);
    act(() => toggleFn());
    expect(openState()).toBe("open");
    // Opening an overlay is a moment, not a preference: nothing is written…
    expect(store.get(threadSwitcherOpenKey("compact"))).toBeNull();
    a.unmount();

    // …and a fresh mount is closed again, so the overlay never restores itself
    // over the transcript you came back to read.
    mount("compact", store);
    expect(openState()).toBe("closed");
  });

  it("adopts the landed class's state when the width class changes", () => {
    const store = memoryStore({
      [threadSwitcherOpenKey("wide")]: "1",
      [threadSwitcherOpenKey("regular")]: "0",
    });
    const view = mount("wide", store);
    expect(openState()).toBe("open");

    view.rerender(
      <KeyValueStoreProvider store={store}>
        <Probe widthClass="regular" />
      </KeyValueStoreProvider>,
    );
    // Crossing a breakpoint must adopt the state stored for where you landed,
    // not carry the previous class's over.
    expect(openState()).toBe("closed");
  });
});
