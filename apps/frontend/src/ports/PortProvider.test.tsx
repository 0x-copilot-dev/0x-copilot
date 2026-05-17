import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import {
  PortProvider,
  usePort,
  usePorts,
  type PortBundle,
} from "./PortProvider";

const stubPorts: PortBundle = {
  badge: { setBadge: () => undefined },
  notification: {
    notify: () => undefined,
    isAvailable: () => false,
  },
  filePicker: { pick: async () => [] },
  clipboard: { copyText: async () => undefined },
};

function ReadOne({ name }: { name: keyof PortBundle }): ReactElement {
  // Touch the port to prove the hook resolves; render its typeof.
  const port = usePort(name);
  return <span data-testid="port">{typeof port}</span>;
}

function ReadAll(): ReactElement {
  const ports = usePorts();
  return (
    <span data-testid="bundle">{Object.keys(ports).sort().join(",")}</span>
  );
}

describe("PortProvider", () => {
  it("usePort returns the named port when wrapped in a provider", () => {
    const { getByTestId } = render(
      <PortProvider ports={stubPorts}>
        <ReadOne name="clipboard" />
      </PortProvider>,
    );
    expect(getByTestId("port").textContent).toBe("object");
  });

  it("usePorts returns the entire bundle", () => {
    const { getByTestId } = render(
      <PortProvider ports={stubPorts}>
        <ReadAll />
      </PortProvider>,
    );
    expect(getByTestId("bundle").textContent).toBe(
      "badge,clipboard,filePicker,notification",
    );
  });

  it("usePort throws with a clear error when no provider is mounted", () => {
    // suppress React's error logging for the expected throw
    const spy = (): void => undefined;
    const original = console.error;
    console.error = spy;
    try {
      expect(() => render(<ReadOne name="badge" />)).toThrow(
        /PortProvider missing/,
      );
    } finally {
      console.error = original;
    }
  });
});
