import { test, expect } from "@playwright/test";

test("investigation appears in UI after alert processed", async ({ page }) => {
  // This test assumes:
  // 1. Backend is running with real services (not mock mode)
  // 2. An alert has been sent and processed by the worker
  // 3. The investigation has been saved and indexed

  // Navigate to the application
  await page.goto("http://localhost:5173");

  // Wait for login dialog to appear
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible({ timeout: 10000 });

  // Login with test credentials
  await page.fill('input[autocomplete="username"]', "testadmin");
  await page.fill('input[type="password"]', "testpass123");
  await page.click('button:has-text("Sign in")');

  // Wait for login to complete and inbox to load
  // The app redirects to /inbox after login
  await expect(page).toHaveURL(/\/inbox/, { timeout: 10000 });

  // Wait for the inbox to load and display cases
  // The investigation should appear in the inbox table
  // We check for either the pod name or alertname from our test alert
  await expect(page.locator("text=/PodCPUThrottling|test-pod-12345/")).toBeVisible({
    timeout: 15000,
  });

  console.log("✓ Investigation is visible in the UI inbox");
});
