/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import { devtools } from "@tanstack/devtools-vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const { INFRAHUB_SYNC_API_URL, INFRAHUB_SYNC_API_TOKEN } = process.env;

const config = defineConfig({
  resolve: { tsconfigPaths: true },
  plugins: [
    devtools(),
    tailwindcss(),
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    viteReact(),
  ],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
  },
  server: {
    proxy: {
      "/api": {
        target: INFRAHUB_SYNC_API_URL || "http://127.0.0.1:8001",
        changeOrigin: true,
        // The browser's Origin names this server, not the Sync API, so the Sync API would refuse a
        // write that carries no token as cross-origin. This server is the only client of the hop.
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => request.removeHeader("origin"));
        },
        headers: INFRAHUB_SYNC_API_TOKEN
          ? { Authorization: `Bearer ${INFRAHUB_SYNC_API_TOKEN}` }
          : undefined,
      },
    },
  },
});

export default config;
