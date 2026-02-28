import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { createMockMiddleware } from "./vite-mock-middleware";

// Vite (and some deps) rely on Web Crypto at config time.
// In some Node setups, `globalThis.crypto` is missing or points at the Node `crypto` module
// (which doesn't implement `getRandomValues`). Ensure it's the WebCrypto implementation.
// This keeps `npm run dev` working without forcing a specific Node minor version.
import { webcrypto } from "node:crypto";

const g: any = globalThis as any;
if (!g.crypto || typeof g.crypto.getRandomValues !== "function") {
  g.crypto = webcrypto;
}

// Check if mock mode is enabled
const isMockMode = process.env.VITE_MOCK_API === "1";

// Plugin to add mock API middleware
function mockApiPlugin(): Plugin {
  return {
    name: "mock-api-middleware",
    configureServer(server) {
      // Add middleware early in the stack
      server.middlewares.use(createMockMiddleware());
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    ...(isMockMode ? [mockApiPlugin()] : []),
  ],
  server: {
    port: 5173,
    strictPort: true,
    host: 'localhost',
    // Disable proxy in mock mode so middleware can intercept requests
    proxy: isMockMode ? undefined : {
      // Proxy API requests to backend (use 127.0.0.1 to avoid IPv6 issues)
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        secure: false,
      },
      '/alerts': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
