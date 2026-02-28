import { test, expect } from "@playwright/test";

/**
 * Chat Docked Mode Layout Tests
 *
 * These tests verify the responsive layout behavior when the chat is docked:
 * - Report maintains max-width of 920px
 * - Chat width is responsive and stays within bounds (420px - 650px)
 * - No overlap between report and chat
 * - Proper spacing is maintained
 *
 * These tests prevent regressions from:
 * - https://github.com/your-repo/issues/XXX (chat panel width in docked mode)
 */

test.describe("Chat docked mode layout", () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to a case and open chat in docked mode
    await page.goto("/cases/case_3920_11111111-1111-1111-1111-111111111111");
    await page.getByRole("button", { name: "Open Tarka chat" }).click();
    await page.getByTitle("Expand (sidebar)").click();

    // Wait for layout to settle
    await page.waitForTimeout(300);
  });

  test("report maintains max-width of 920px in docked mode", async ({ page }) => {
    const reportCard = page.getByTestId("triage-report-card");
    await expect(reportCard).toBeVisible();

    const boundingBox = await reportCard.boundingBox();
    expect(boundingBox).toBeTruthy();
    expect(boundingBox!.width).toBeLessThanOrEqual(920);
  });

  test("chat width is responsive and within bounds", async ({ page }) => {
    const viewport = page.viewportSize();
    expect(viewport).toBeTruthy();

    const chatWidget = page.getByLabel("Tarka Assistant chat");
    await expect(chatWidget).toBeVisible();

    const boundingBox = await chatWidget.boundingBox();
    expect(boundingBox).toBeTruthy();

    const chatWidth = boundingBox!.width;

    // Chat should be at least 420px (minimum)
    expect(chatWidth).toBeGreaterThanOrEqual(420);

    // Chat should be at most 650px (maximum)
    expect(chatWidth).toBeLessThanOrEqual(650);

    // For standard desktop viewport (1280px+), chat should expand beyond minimum
    if (viewport!.width >= 1600) {
      expect(chatWidth).toBeGreaterThan(420);
    }
  });

  test("no overlap between report and chat", async ({ page }) => {
    const reportCard = page.getByTestId("triage-report-card");
    const chatWidget = page.getByLabel("Tarka Assistant chat");

    await expect(reportCard).toBeVisible();
    await expect(chatWidget).toBeVisible();

    const reportBox = await reportCard.boundingBox();
    const chatBox = await chatWidget.boundingBox();

    expect(reportBox).toBeTruthy();
    expect(chatBox).toBeTruthy();

    // Report right edge should be left of chat left edge (with some tolerance)
    const reportRightEdge = reportBox!.x + reportBox!.width;
    const chatLeftEdge = chatBox!.x;
    const gap = chatLeftEdge - reportRightEdge;

    // There should be at least 16px gap (with 8px tolerance for rounding)
    expect(gap).toBeGreaterThanOrEqual(8);

    // But not too much gap (max ~60px for reasonable layouts)
    expect(gap).toBeLessThan(100);
  });

  test("chat width increases with viewport width", async ({ page, browser }) => {
    // Test at 1600px viewport
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.waitForTimeout(200);

    const chatWidget = page.getByLabel("Tarka Assistant chat");
    let boundingBox = await chatWidget.boundingBox();
    const width1600 = boundingBox!.width;

    // Test at 1920px viewport
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.waitForTimeout(200);

    boundingBox = await chatWidget.boundingBox();
    const width1920 = boundingBox!.width;

    // Chat should be wider at 1920px than at 1600px
    expect(width1920).toBeGreaterThan(width1600);

    // At 1920px, chat should be at or near max-width (650px)
    expect(width1920).toBeGreaterThanOrEqual(640); // Allow some tolerance
  });

  test("chat stays at minimum width on smaller viewports", async ({ page }) => {
    // Test at 1480px viewport (close to minimum threshold)
    await page.setViewportSize({ width: 1480, height: 900 });
    await page.waitForTimeout(200);

    const chatWidget = page.getByLabel("Tarka Assistant chat");
    const boundingBox = await chatWidget.boundingBox();

    // Chat should be at or very close to minimum (420px)
    expect(boundingBox!.width).toBeLessThanOrEqual(430);
    expect(boundingBox!.width).toBeGreaterThanOrEqual(420);
  });

  test("layout remains stable when scrolling", async ({ page }) => {
    const chatWidget = page.getByLabel("Tarka Assistant chat");

    // Get initial position
    const initialBox = await chatWidget.boundingBox();

    // Scroll down
    await page.evaluate(() => window.scrollBy(0, 500));
    await page.waitForTimeout(100);

    // Chat position should remain fixed (not scroll with content)
    const afterScrollBox = await chatWidget.boundingBox();

    expect(afterScrollBox!.x).toBe(initialBox!.x);
    expect(afterScrollBox!.y).toBe(initialBox!.y);
    expect(afterScrollBox!.width).toBe(initialBox!.width);
  });

  test("sidebar collapse state persists with chat docked", async ({ page }) => {
    // When chat is docked, sidebar should be collapsed
    const sidebar = page.locator('[class*="sidebar"]').first();
    const sidebarBox = await sidebar.boundingBox();

    // Sidebar should be narrow (collapsed ~72px, not expanded ~256px)
    expect(sidebarBox!.width).toBeLessThan(100);
  });

  test("report is properly constrained within grid", async ({ page }) => {
    const reportWrap = page.locator(".triageReport").first();
    await expect(reportWrap).toBeVisible();

    const boundingBox = await reportWrap.boundingBox();

    // Report wrapper should not exceed max-width
    expect(boundingBox!.width).toBeLessThanOrEqual(920);
  });

  test("chat right edge is 24px from viewport edge", async ({ page }) => {
    const viewport = page.viewportSize();
    const chatWidget = page.getByLabel("Tarka Assistant chat");

    const boundingBox = await chatWidget.boundingBox();
    const chatRightEdge = boundingBox!.x + boundingBox!.width;
    const viewportWidth = viewport!.width;

    // Chat should be 24px from right edge (with tolerance)
    const distanceFromEdge = viewportWidth - chatRightEdge;
    expect(distanceFromEdge).toBeGreaterThanOrEqual(20);
    expect(distanceFromEdge).toBeLessThanOrEqual(28);
  });
});

test.describe("Chat docked mode - responsive breakpoints", () => {
  const viewportTests = [
    { width: 1440, height: 900, expectedChatMin: 420, expectedChatMax: 450 },
    { width: 1600, height: 900, expectedChatMin: 500, expectedChatMax: 560 },
    { width: 1920, height: 1080, expectedChatMin: 640, expectedChatMax: 650 },
    { width: 2560, height: 1440, expectedChatMin: 650, expectedChatMax: 650 }, // Maxed out
  ];

  for (const { width, height, expectedChatMin, expectedChatMax } of viewportTests) {
    test(`chat width is correct at ${width}x${height}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await page.goto("/cases/case_3920_11111111-1111-1111-1111-111111111111");

      await page.getByRole("button", { name: "Open Tarka chat" }).click();
      await page.getByTitle("Expand (sidebar)").click();
      await page.waitForTimeout(300);

      const chatWidget = page.getByLabel("Tarka Assistant chat");
      const boundingBox = await chatWidget.boundingBox();
      const chatWidth = boundingBox!.width;

      expect(chatWidth).toBeGreaterThanOrEqual(expectedChatMin);
      expect(chatWidth).toBeLessThanOrEqual(expectedChatMax);
    });
  }
});
