import react from "@vitejs/plugin-react";
import { createHmac } from "node:crypto";
import { defineConfig } from "vite";

const DEFAULT_DEV_IDENTITY = {
  org_id: "org_123",
  user_id: "user_123",
  roles: ["employee"],
  permission_scopes: ["runtime:use"],
  connector_scopes: {}
};

function base64Url(value: Buffer): string {
  return value.toString("base64url");
}

function devIdentityToken(): string | undefined {
  if (process.env.ENTERPRISE_DEV_IDENTITY_TOKEN) {
    return process.env.ENTERPRISE_DEV_IDENTITY_TOKEN;
  }
  if (!process.env.ENTERPRISE_AUTH_SECRET) {
    return undefined;
  }
  const payload = base64Url(Buffer.from(JSON.stringify(DEFAULT_DEV_IDENTITY)));
  const signature = base64Url(
    createHmac("sha256", process.env.ENTERPRISE_AUTH_SECRET).update(payload).digest()
  );
  return `${payload}.${signature}`;
}

const token = devIdentityToken();

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: process.env.BACKEND_FACADE_URL ?? "http://127.0.0.1:8200",
        changeOrigin: true,
        headers: token ? { authorization: `Bearer ${token}` } : undefined
      }
    }
  }
});
