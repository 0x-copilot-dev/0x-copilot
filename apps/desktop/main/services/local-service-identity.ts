import { randomBytes as nodeRandomBytes, timingSafeEqual } from "node:crypto";

import type { SupervisedServiceName } from "./runtime-paths";

export type LocalBrokerAudience =
  | "desktop-browser-broker"
  | "desktop-capability-broker";

/** A non-secret, per-boot identity for a supervised child. */
export interface LocalServiceIdentity {
  readonly service: SupervisedServiceName;
  readonly audience: `desktop-local:${SupervisedServiceName}`;
}

/**
 * One secret credential for one exact `(service, local broker)` pair.  It is
 * intentionally not a general service credential: using it at another local
 * authority must fail even if the caller lies about its audience.
 */
export interface LocalServiceChannelCredential {
  readonly service: SupervisedServiceName;
  readonly brokerAudience: LocalBrokerAudience;
  readonly credential: string;
}

export const LOCAL_BROKER_AUDIENCE = {
  browser: "desktop-browser-broker",
  capability: "desktop-capability-broker",
} as const satisfies Record<string, LocalBrokerAudience>;

export const LOCAL_SERVICE_IDENTITY_PROTOCOL = "1";
export const LOCAL_SERVICE_IDENTITY_HEADER = "x-desktop-local-service";
export const LOCAL_SERVICE_AUDIENCE_HEADER = "x-desktop-local-audience";
export const LOCAL_SERVICE_IDENTITY_ENV = {
  service: "DESKTOP_LOCAL_SERVICE_IDENTITY",
  audience: "DESKTOP_LOCAL_SERVICE_AUDIENCE",
  protocol: "DESKTOP_LOCAL_SERVICE_IDENTITY_PROTOCOL",
} as const;

const SERVICES = ["backend", "ai-backend", "backend-facade"] as const;
const BROKERS = [
  LOCAL_BROKER_AUDIENCE.browser,
  LOCAL_BROKER_AUDIENCE.capability,
] as const;
const CREDENTIAL_BYTES = 32;

/** Main-owned, in-memory identities. They rotate on every supervised boot. */
export class LocalServiceIdentityRegistry {
  readonly #identities: ReadonlyMap<
    SupervisedServiceName,
    LocalServiceIdentity
  >;
  readonly #channels: ReadonlyMap<string, LocalServiceChannelCredential>;

  constructor(input?: {
    readonly randomBytes?: (size: number) => Buffer;
    readonly identities?: readonly LocalServiceIdentity[];
    readonly channelCredentials?: readonly LocalServiceChannelCredential[];
  }) {
    const suppliedIdentities = input?.identities;
    const suppliedChannels = input?.channelCredentials;
    if (suppliedIdentities !== undefined || suppliedChannels !== undefined) {
      if (suppliedIdentities === undefined || suppliedChannels === undefined) {
        throw new Error(
          "desktop service identities require channel credentials",
        );
      }
      this.#identities = validateIdentities(suppliedIdentities);
      this.#channels = validateChannels(suppliedChannels);
      return;
    }

    const random = input?.randomBytes ?? nodeRandomBytes;
    this.#identities = new Map(
      SERVICES.map((service) => [
        service,
        Object.freeze({
          service,
          audience: `desktop-local:${service}` as const,
        }),
      ]),
    );
    const issued = new Set<string>();
    this.#channels = new Map(
      SERVICES.flatMap((service) =>
        BROKERS.map((brokerAudience) => {
          const credential = mintDistinctCredential(random, issued);
          const channel = Object.freeze({
            service,
            brokerAudience,
            credential,
          });
          return [channelKey(service, brokerAudience), channel] as const;
        }),
      ),
    );
  }

  forService(service: SupervisedServiceName): LocalServiceIdentity {
    const identity = this.#identities.get(service);
    if (identity === undefined)
      throw new Error(`unknown desktop service ${service}`);
    return identity;
  }

  forBroker(
    service: SupervisedServiceName,
    brokerAudience: LocalBrokerAudience,
  ): LocalServiceChannelCredential {
    const channel = this.#channels.get(channelKey(service, brokerAudience));
    if (channel === undefined)
      throw new Error("desktop service broker channel is not configured");
    return channel;
  }

  /** Constant-time verification used only by main-owned local authorities. */
  verifies(
    service: SupervisedServiceName,
    brokerAudience: LocalBrokerAudience,
    credential: string,
  ): boolean {
    const expected = Buffer.from(
      this.forBroker(service, brokerAudience).credential,
      "utf8",
    );
    const actual = Buffer.from(credential, "utf8");
    return (
      actual.length === expected.length && timingSafeEqual(actual, expected)
    );
  }
}

function mintDistinctCredential(
  random: (size: number) => Buffer,
  issued: Set<string>,
): string {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const candidate = random(CREDENTIAL_BYTES).toString("base64url");
    if (!issued.has(candidate)) {
      issued.add(candidate);
      return candidate;
    }
  }
  throw new Error("could not mint a distinct desktop broker credential");
}

function validateIdentities(
  identities: readonly LocalServiceIdentity[],
): ReadonlyMap<SupervisedServiceName, LocalServiceIdentity> {
  const result = new Map<SupervisedServiceName, LocalServiceIdentity>();
  for (const identity of identities) {
    if (!SERVICES.includes(identity.service))
      throw new Error("invalid desktop service identity");
    if (identity.audience !== `desktop-local:${identity.service}`) {
      throw new Error("invalid desktop service identity");
    }
    if (result.has(identity.service))
      throw new Error("duplicate desktop service identity");
    result.set(identity.service, Object.freeze({ ...identity }));
  }
  if (result.size !== SERVICES.length)
    throw new Error("missing desktop service identity");
  return result;
}

function validateChannels(
  channels: readonly LocalServiceChannelCredential[],
): ReadonlyMap<string, LocalServiceChannelCredential> {
  const result = new Map<string, LocalServiceChannelCredential>();
  const issued = new Set<string>();
  for (const channel of channels) {
    if (
      !SERVICES.includes(channel.service) ||
      !BROKERS.includes(channel.brokerAudience)
    ) {
      throw new Error("invalid desktop service broker channel");
    }
    const key = channelKey(channel.service, channel.brokerAudience);
    if (
      channel.credential === "" ||
      result.has(key) ||
      issued.has(channel.credential)
    ) {
      throw new Error("duplicate desktop service broker channel");
    }
    issued.add(channel.credential);
    result.set(key, Object.freeze({ ...channel }));
  }
  if (result.size !== SERVICES.length * BROKERS.length) {
    throw new Error("missing desktop service broker channel");
  }
  return result;
}

function channelKey(
  service: SupervisedServiceName,
  brokerAudience: LocalBrokerAudience,
): string {
  return `${service}:${brokerAudience}`;
}
