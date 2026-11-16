import tailwindcss from "@tailwindcss/vite";
import { devtools } from "@tanstack/devtools-vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const { INFRAHUB_SYNC_API_URL } = process.env;

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
        target: INFRAHUB_SYNC_API_URL || "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});

export default config;
