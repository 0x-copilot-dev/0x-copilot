import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  define: {
    __BUILD_SHA__: JSON.stringify(process.env.GIT_SHA ?? "dev"),
    __DEPLOY_ENV__: JSON.stringify(
      process.env.DEPLOY_ENVIRONMENT ?? "development",
    ),
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: process.env.BACKEND_FACADE_URL ?? "http://127.0.0.1:8200",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (_proxyReq, req) => {
            console.log("[vite-proxy] →", req.method, req.url);
          });
          proxy.on("proxyRes", (proxyRes, req) => {
            console.log(
              "[vite-proxy] ←",
              proxyRes.statusCode,
              req.method,
              req.url,
            );
          });
          proxy.on("error", (err, req) => {
            console.log("[vite-proxy] !", err.message, req.method, req.url);
          });
        },
      },
    },
  },
});
