export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export type QueryParamValue = string | number | boolean | undefined;

export interface TypedRequest {
  readonly method: HttpMethod;
  readonly path: string;
  readonly query?: Readonly<Record<string, QueryParamValue>>;
  readonly body?: unknown;
  readonly headers?: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal;
}

export interface Session {
  readonly bearer: string | null;
}

export interface TransportCapabilities {
  readonly substrate: "web" | "desktop-webview";
  readonly nativeSecretStorage: boolean;
  readonly fileSystemAccess: boolean;
  readonly clipboardWrite: boolean;
  readonly openExternal: boolean;
}

export interface SseSubscribeOptions {
  readonly path: string;
  readonly query?: Readonly<Record<string, QueryParamValue>>;
  readonly eventName?: string;
  readonly onMessage: (raw: string) => void;
  readonly onOpen?: () => void;
  readonly onError?: (err: Error) => void;
}

export interface SseSubscription {
  close(): void;
}

export class UnauthorizedError extends Error {
  readonly status = 401;

  constructor(detail?: string) {
    super(detail || "Request failed with 401");
    this.name = "UnauthorizedError";
  }
}
