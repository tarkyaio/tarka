import { test, expect } from "@playwright/test";

// Reset mock chat store before each test to prevent state pollution
test.beforeEach(async ({ request }) => {
  await request.post("/__test__/reset-mock-chat").catch(() => {
    // Ignore errors if endpoint doesn't exist (e.g., in production)
  });
});

test("chat bubble is available on inbox view", async ({ page }) => {
  await page.goto("/inbox");
  await expect(page.getByRole("button", { name: "Open Tarka chat" })).toBeVisible();
});

test("global chat initializes with Global thread selected", async ({ page }) => {
  await page.goto("/inbox");

  // Open the chat
  await page.getByRole("button", { name: "Open Tarka chat" }).click();
  const chatWidget = page.getByLabel("Tarka Assistant chat");
  await expect(chatWidget).toBeVisible();

  // Wait for thread to finish loading
  await expect(chatWidget).toHaveAttribute("data-loading", "false", { timeout: 3000 });

  // Verify the thread selector shows "Global" selected (not "Select thread...")
  const threadSelect = page.locator("select").filter({ hasText: "Global" });
  await expect(threadSelect).toBeVisible();

  // Verify the selected option is "Global"
  const selectedValue = await threadSelect.inputValue();
  expect(selectedValue).not.toBe(""); // Should not be empty (which would show "Select thread...")

  // Verify the visible text shows "Global"
  const selectedOption = threadSelect.locator("option:checked");
  await expect(selectedOption).toHaveText("Global");

  // Verify the context label shows "All cases"
  await expect(page.getByText("Context: All cases")).toBeVisible();

  // Verify the empty state message is correct for global chat
  await expect(page.getByText("Ask a question about cases in the inbox.")).toBeVisible();

  // Verify the input placeholder
  await expect(page.getByPlaceholder("Ask follow-up questions…")).toBeVisible();
});

test("chat preserves draft + history across bubble/floating/docked", async ({ page }) => {
  await page.goto("/cases/case_3920_11111111-1111-1111-1111-111111111111");

  // Open bubble → floating.
  await page.getByRole("button", { name: "Open Tarka chat" }).click();
  const chatWidget = page.getByLabel("Tarka Assistant chat");
  await expect(chatWidget).toBeVisible();
  // Wait for initial thread load to complete
  await expect(chatWidget).toHaveAttribute("data-loading", "false", { timeout: 3000 });

  // Draft input, then dock/undock, draft should persist.
  const composer = page.getByPlaceholder("Ask follow-up questions…");
  await composer.fill("draft message");

  await page.getByTitle("Expand (sidebar)").click();
  // Wait for mode transition to complete
  await page.waitForTimeout(150);
  await expect(chatWidget).toBeVisible();
  await expect(composer).toHaveValue("draft message");

  await page.getByTitle("Pop out (floating)").click();
  // Wait for mode transition to complete
  await page.waitForTimeout(150);
  await expect(chatWidget).toBeVisible();
  await expect(composer).toHaveValue("draft message");

  // Send a message.
  await composer.fill("hello");
  await page.getByTitle("Send").click();
  // Wait for both user message and bot response to appear
  // Use .last() to target the most recent message (in case there are multiple from history)
  await expect(page.getByText("You").last()).toBeVisible();
  await expect(page.getByText("Mock case chat: received").last()).toBeVisible();
  // Wait for message to be fully persisted to backend
  await page.waitForTimeout(500);

  // Minimize to bubble and reopen; history should still be present.
  await page.getByTitle("Minimize").click();
  await page.getByRole("button", { name: "Open Tarka chat" }).click();
  await expect(chatWidget).toBeVisible();
  await expect(page.getByText("Mock case chat: received").last()).toBeVisible();

  // Navigate away and back (SPA navigation) — history should still be present.
  await page.getByRole("link", { name: "Inbox" }).click();
  await expect(page.getByText("Case Inbox")).toBeVisible();
  await page.goBack();
  // Wait for navigation to complete and page to load
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Triage Report").first()).toBeVisible({ timeout: 5000 });

  // Wait for chat thread to reload after navigation
  await page.waitForTimeout(500);

  await page.getByRole("button", { name: "Open Tarka chat" }).click();
  await expect(chatWidget).toBeVisible();
  // Wait for thread to finish loading
  await expect(chatWidget).toHaveAttribute("data-loading", "false", { timeout: 3000 });
  await expect(page.getByText("Mock case chat: received").last()).toBeVisible();
});
