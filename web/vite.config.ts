import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  envPrefix: "VITE_PUBLIC_",
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    allowedHosts: ["localhost", "127.0.0.1"],
    fs: {
      strict: true,
      allow: [fileURLToPath(new URL(".", import.meta.url))],
      deny: ["**/.env", "**/.env.*", "**/.git/**"],
    },
    headers: { "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer" },
    proxy: { "/api": { target: "http://127.0.0.1:3001", changeOrigin: false } },
  },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.ts", css: false },
});
