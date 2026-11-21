import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "app/index.html"),
        admin: resolve(import.meta.dirname, "admin/index.html"),
        share: resolve(import.meta.dirname, "share/index.html"),
      },
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
});
