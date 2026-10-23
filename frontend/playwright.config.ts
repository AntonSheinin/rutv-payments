import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: "http://localhost:8100",
    viewport: { width: 1440, height: 1000 },
  },
  reporter: "list",
  workers: 1,
});
