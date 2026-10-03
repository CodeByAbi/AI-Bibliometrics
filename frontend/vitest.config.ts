/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["components/**/*.test.tsx", "components/**/*.test.ts", "lib/**/*.test.ts"],
    css: false,
    // axe-core walks the whole DOM for every rule; the 50-evidence fixture
    // legitimately takes seconds, and coverage instrumentation doubles it.
    testTimeout: 30_000,
    hookTimeout: 30_000,
    coverage: {
      provider: "v8",
      include: ["components/**/*.{ts,tsx}", "lib/**/*.ts"],
      exclude: ["components/**/*.test.{ts,tsx}", "components/**/__tests__/**"],
      thresholds: { lines: 80, functions: 65, branches: 75, statements: 80 },
    },
  },
});