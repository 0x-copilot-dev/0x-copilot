import { afterEach, describe, expect, it } from "vitest";
import { configureAuthBearerProvider, dynamicCorrelationHeaders } from "./http";

describe("dynamicCorrelationHeaders", () => {
  afterEach(() => {
    configureAuthBearerProvider(() => null);
  });

  it("reflects the current bearer when enumerated by OTLP transports", () => {
    let bearer: string | null = null;
    configureAuthBearerProvider(() => bearer);
    const headers = dynamicCorrelationHeaders();

    expect(Object.fromEntries(Object.entries(headers))).not.toHaveProperty(
      "authorization",
    );

    bearer = "dev-token";

    expect(Object.fromEntries(Object.entries(headers))).toMatchObject({
      authorization: "Bearer dev-token",
    });
  });
});
