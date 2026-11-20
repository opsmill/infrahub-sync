/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import { devtools } from "@tanstack/devtools-vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const { INFRAHUB_SYNC_API_URL } = process.env;

/**
 * The Sync service's address, as the CLI reads it: one trailing `api` path segment is the Sync
 * API's prefix, which the forwarded path already carries. A value that is not a URL is kept as it
 * is, so `vite build`, which never forwards, does not fail on it.
 */
function serviceAddress(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return value;
  }
  url.pathname = url.pathname.replace(/\/+$/, "").replace(/\/api$/, "");
  return url.toString().replace(/\/+$/, "");
}

const config = defineConfig({
  resolve: { tsconfigPaths: true },
  plugins: [
    devtools(),
    tailwindcss(),
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    viteReact(),
  ],
  server: {
    proxy: {
      "/api": {
        target: serviceAddress(INFRAHUB_SYNC_API_URL || "http://127.0.0.1:8001"),
        changeOrigin: true,
      },
    },
  },
  // Unit tests of the pure functions in src/. The pattern keeps Vitest out of the
  // vendor/infrahub submodule, which carries its own tests.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});

export default config;
