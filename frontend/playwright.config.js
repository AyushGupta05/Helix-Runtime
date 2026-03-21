import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: "http://127.0.0.1:4174"
  },
  webServer: {
    command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4174",
    port: 4174,
    reuseExistingServer: false,
    timeout: 120000
  }
});
