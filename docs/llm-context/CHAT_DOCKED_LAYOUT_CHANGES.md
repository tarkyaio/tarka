# Chat Docked Mode Layout - Implementation Summary

## Problem Statement

When the sidebar was collapsed in case view with the chat panel docked, the extra horizontal space was consumed by the report expanding wider, while the chat panel remained at a fixed 420px width. This created an unbalanced layout with excessive gray space.

## Solution

Implemented a responsive layout where:
1. **Report maintains consistent reading width** (max 920px) regardless of sidebar state
2. **Chat panel expands dynamically** to utilize available space when sidebar collapses
3. **Proper spacing maintained** between report and chat (minimum 24px gap)

## Files Modified

### 1. `ui/src/screens/CaseScreen.module.css`

**Change 1: Report max-width in docked mode**
```css
.reportWrapDocked {
  grid-column: 1 / 2;
  max-width: 920px;  /* Was: none */
  margin: 0;
}
```

**Change 2: Grid structure**
```css
.caseGridDocked {
  grid-template-columns: minmax(0, 1fr);  /* Was: minmax(0, 1fr) 420px */
  gap: 16px;
  align-items: start;
}
```

**Rationale**: Removed phantom 420px column that created empty gray space, since chat is positioned fixed outside the grid.

### 2. `ui/src/ui/AssistantChatWidget.module.css`

**Change: Responsive chat width**
```css
.widgetDocked {
  --docked-offset: 1064px;
  --docked-min-width: 420px;
  --docked-max-width: 650px;

  /* ... other properties ... */
  width: clamp(var(--docked-min-width), calc(100vw - var(--docked-offset)), var(--docked-max-width));
  /* Was: width: 420px; */
}
```

**Calculation Details**:
- **Offset (1064px)** = 72px (sidebar) + 24px (padding) + 920px (report) + 24px (gap) + 24px (right margin)
- **Min (420px)**: Ensures usability on smaller screens
- **Max (650px)**: Prevents excessive width on large displays

## Files Created

### 1. `ui/tests/chat-docked-layout.spec.ts`

Comprehensive e2e tests covering:
- Report max-width constraint (920px)
- Chat width bounds (420px - 650px)
- No overlap between report and chat
- Responsive behavior at multiple viewport sizes
- Layout stability during scrolling
- Sidebar collapse state
- Specific breakpoint testing (1440px, 1600px, 1920px, 2560px)

**Test Count**: 13 tests across 2 test suites

### 2. `ui/docs/CHAT_DOCKED_LAYOUT.md`

Documentation covering:
- Layout constraints and rationale
- Offset calculation breakdown
- Responsive behavior examples
- Grid structure explanation
- Testing instructions
- Troubleshooting guide
- Visual examples for different viewport sizes

## Optimizations Added

1. **CSS Custom Properties**
   - Extracted magic numbers into named variables
   - Makes future maintenance easier
   - Self-documenting code

2. **Comprehensive Comments**
   - Explained calculation formula in detail
   - Added warnings about interdependencies
   - Documented rationale for constraints

3. **Test Coverage**
   - Prevents regressions
   - Tests all critical layout properties
   - Covers multiple viewport sizes

## Responsive Behavior

| Viewport | Chat Width | Status      |
|----------|-----------|-------------|
| < 1484px | 420px     | Minimum     |
| 1600px   | ~536px    | Expanding   |
| 1920px   | 650px     | Maximum     |
| > 2114px | 650px     | Capped      |

## Verification Steps

1. **Visual Testing**
   ```bash
   cd ui && npm run dev
   # Navigate to a case and dock the chat
   # Verify report stays at 920px max
   # Verify chat expands on wider screens
   ```

2. **Run E2E Tests**
   ```bash
   cd ui && npm run test:e2e -- chat-docked-layout
   ```

3. **Run All Tests**
   ```bash
   cd ui && npm test
   cd ui && npm run test:e2e
   ```

## Success Criteria

- ✅ Report maintains max-width of 920px in docked mode
- ✅ Chat panel expands dynamically based on available space
- ✅ Chat never narrower than 420px (usability)
- ✅ Chat never wider than 650px (prevents excessive width)
- ✅ No overlap between report and chat
- ✅ Proper 24px gap maintained
- ✅ Layout stable during scrolling
- ✅ Comprehensive test coverage

## Maintenance Notes

### When Changing Report Max-Width

If you need to change the report max-width (currently 920px):

1. Update `CaseScreen.module.css` → `.reportWrapDocked` → `max-width`
2. Recalculate offset: `72 + 24 + NEW_WIDTH + 24 + 24`
3. Update `AssistantChatWidget.module.css` → `--docked-offset`
4. Update test expectations in `chat-docked-layout.spec.ts`
5. Update documentation in `ui/docs/CHAT_DOCKED_LAYOUT.md`

### When Changing Sidebar Width

If sidebar width changes (currently 72px collapsed):

1. Update offset calculation: `NEW_SIDEBAR + 24 + 920 + 24 + 24`
2. Update `AssistantChatWidget.module.css` → `--docked-offset`
3. Update documentation

### When Changing Chat Bounds

If you need to adjust chat min/max width:

1. Update `AssistantChatWidget.module.css` → `--docked-min-width` / `--docked-max-width`
2. Update test expectations in `chat-docked-layout.spec.ts`
3. Update documentation

## Related Issues

- **Fixed**: Chat panel width in docked mode
- **Date**: 2026-02-17
- **PR**: TBD

## Screenshots

### Before
- Large gray gap between report and chat
- Chat remained at fixed 420px
- Report expanded to fill available space

### After
- Balanced layout with proper spacing
- Chat expands responsively (420px - 650px)
- Report constrained to optimal reading width (920px)
- Consistent experience across viewport sizes
