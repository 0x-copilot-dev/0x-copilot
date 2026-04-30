import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: process.env.BACKEND_FACADE_URL ?? "http://127.0.0.1:8200",
        changeOrigin: true
      }
    }
  }
});
