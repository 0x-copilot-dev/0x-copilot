import { createServer, type Server } from "node:net";

// OS-assigned free-port allocation. All listeners are held open until the
// full set is allocated so the same port can never be handed out twice in
// one call; they are closed before returning, which leaves a tiny window
// before the real service binds — acceptable on a single-user desktop
// (127.0.0.1 only) and unavoidable without SO_REUSEPORT games.
export async function allocateFreePorts(
  count: number,
  host = "127.0.0.1",
): Promise<number[]> {
  if (!Number.isInteger(count) || count <= 0) {
    throw new Error(`allocateFreePorts: count must be a positive integer`);
  }
  const servers: Server[] = [];
  try {
    const ports: number[] = [];
    for (let i = 0; i < count; i += 1) {
      const server = createServer();
      servers.push(server);
      const port = await new Promise<number>((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, host, () => {
          const address = server.address();
          if (address === null || typeof address === "string") {
            reject(new Error("allocateFreePorts: no address after listen"));
            return;
          }
          resolve(address.port);
        });
      });
      ports.push(port);
    }
    return ports;
  } finally {
    await Promise.all(
      servers.map(
        (server) =>
          new Promise<void>((resolve) => {
            server.close(() => {
              resolve();
            });
          }),
      ),
    );
  }
}
