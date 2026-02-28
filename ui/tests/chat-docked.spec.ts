import { test, expect } from "@playwright/test";

test("chat does not vanish when switching to docked mode", async ({ page }) => {
  // In mock mode, auth is disabled and mock data is served by the UI client.
  await page.goto("/cases/case_3920_11111111-1111-1111-1111-111111111111");

  // Open bubble → floating.
  await page.getByRole("button", { name: "Open Tarka chat" }).click();
  await expect(page.getByLabel("Tarka Assistant chat")).toBeVisible();

  // Dock (sidebar).
  await page.getByTitle("Expand (sidebar)").click();
  await expect(page.getByLabel("Tarka Assistant chat")).toBeVisible();
  await expect(page.getByPlaceholder("Ask follow-up questions…")).toBeVisible();

  // Ensure we can interact.
  await page.getByPlaceholder("Ask follow-up questions…").fill("hello");
  await page.getByTitle("Send").click();
  await expect(page.getByText("hello")).toBeVisible();
});
