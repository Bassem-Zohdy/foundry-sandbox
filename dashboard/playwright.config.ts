import { defineConfig, devices } from '@playwright/test';

// E2E_BASE_URL points the journeys at a running app: the built image (as CI does), a preview
// or staging. Without it Playwright serves the last `npm run build` with `vite preview`.
const external = process.env.E2E_BASE_URL;
const baseURL = external ?? 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './e2e',
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: { baseURL, trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: external
    ? undefined
    : {
        command: 'npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
