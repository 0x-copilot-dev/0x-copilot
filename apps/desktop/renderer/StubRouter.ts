import type {
  ArtifactRoute,
  NavigateOptions,
  Router,
} from "@enterprise-search/chat-surface";

// Phase 1-A placeholder. Agent 1-D's HashRouter takes over at integration
// time. The on-disk Router port is generic over the route type and ships
// ArtifactRoute as the concrete shape — see PRD §3.3.
//
// This stub satisfies the port literally: current() returns the last
// navigated route (default = empty chat); navigate() updates and notifies;
// subscribe()/unsubscribe() works. No URL history, no hash parsing, no
// deep-link integration — those live in HashRouter.
export class StubRouter implements Router<ArtifactRoute> {
  #route: ArtifactRoute;
  readonly #handlers = new Set<(route: ArtifactRoute) => void>();

  constructor(initial?: ArtifactRoute) {
    this.#route = initial ?? { kind: "chat", conversationId: "" };
  }

  current(): ArtifactRoute {
    return this.#route;
  }

  navigate(route: ArtifactRoute, _opts?: NavigateOptions): void {
    this.#route = route;
    for (const handler of this.#handlers) {
      handler(route);
    }
  }

  subscribe(handler: (route: ArtifactRoute) => void): () => void {
    this.#handlers.add(handler);
    return () => {
      this.#handlers.delete(handler);
    };
  }
}
