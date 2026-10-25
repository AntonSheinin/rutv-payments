import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:8100",
    viewport: { width: 1440, height: 1000 },
  },
  reporter: "list",
  workers: 1,
});
