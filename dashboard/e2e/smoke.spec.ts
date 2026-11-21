import { expect, test } from '@playwright/test';

// Critical journeys are the project's: add one spec per journey in this folder. The app's root
// element carries data-testid="app-shell".
test('the app shell renders and the browser reports no Content-Security-Policy violation', async ({ page }) => {
  const violations: string[] = [];
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text());
  });
  const response = await page.goto('/');
  expect(response?.ok()).toBe(true);
  await expect(page.getByTestId('app-shell')).toBeVisible();
  expect(violations).toEqual([]);
});

test('/healthz answers 200', async ({ request }) => {
  const response = await request.get('/healthz');
  expect(response.status()).toBe(200);
});
