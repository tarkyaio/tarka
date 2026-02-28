# Chat Docked Mode Layout

## Overview

When the chat panel is docked to the sidebar in case view, the layout uses a responsive design that balances report readability with chat usability.

## Layout Constraints

### Report Width
- **Max-width**: `920px`
- **Rationale**: Optimal reading width for triage reports (prevents text from becoming too wide)
- **Location**: `CaseScreen.module.css` → `.reportWrapDocked`

### Chat Width
- **Formula**: `clamp(420px, calc(100vw - 1064px), 650px)`
- **Min-width**: `420px` (ensures chat remains usable on smaller screens)
- **Max-width**: `650px` (prevents chat from becoming too wide on large displays)
- **Location**: `AssistantChatWidget.module.css` → `.widgetDocked`

### Offset Calculation (1064px)

The offset in the chat width formula accounts for all horizontal space except the chat:

```
1064px = 72px (sidebar) + 24px (padding) + 920px (report) + 24px (gap) + 24px (right margin)
```

| Component        | Width   | Description                                    |
|------------------|---------|------------------------------------------------|
| Sidebar          | 72px    | Collapsed sidebar (auto-collapses in docked)   |
| Content padding  | 24px    | Left padding in Shell content area             |
| Report           | 920px   | Report max-width                               |
| Gap              | 24px    | Minimum spacing between report and chat        |
| Right margin     | 24px    | Chat's `right: 24px` positioning               |
| **Total Offset** | **1064px** |                                            |

## Responsive Behavior

### Viewport Width Examples

| Viewport | Chat Width | Calculation              | Notes                    |
|----------|-----------|--------------------------|--------------------------|
| 1440px   | 420px     | max(420, 1440-1064)     | At minimum               |
| 1600px   | 536px     | clamp(420, 536, 650)    | Expanded                 |
| 1920px   | 650px     | min(856, 650)           | At maximum               |
| 2560px   | 650px     | min(1496, 650)          | Capped at maximum        |

### Breakpoint Behavior

- **< 1484px**: Chat stays at minimum width (420px)
- **1484px - 2114px**: Chat expands proportionally with viewport
- **> 2114px**: Chat stays at maximum width (650px)

## Grid Structure

In docked mode, `CaseScreen` uses a **single-column grid**:

```css
.caseGridDocked {
  grid-template-columns: minmax(0, 1fr);
}
```

**Important**: The chat is **not** in the grid. It uses `position: fixed` and is rendered separately. The grid only contains the report.

## Key Constraints

### DO NOT Change These Without Updating Others:

1. **Report max-width (920px)**
   - If changed, update chat offset calculation
   - Update tests in `chat-docked-layout.spec.ts`
   - Update this documentation

2. **Chat offset (1064px)**
   - If sidebar, padding, or gap changes, recalculate offset
   - Formula: `sidebar + padding_left + report_width + gap + right_margin`

3. **Chat min/max (420px / 650px)**
   - Min ensures usability on smaller screens
   - Max prevents excessive width on large displays

## Testing

Layout is tested in `ui/tests/chat-docked-layout.spec.ts`:

### Prerequisites
- **Node.js 20.19.4+** (required for Playwright)
- Use nvm: `nvm use 20.19.4`

### Running Tests

**From project root:**
```bash
# Run only UI e2e tests (includes layout tests)
make test-ui-e2e

# Run ALL tests like in CI (pre-commit + pytest + playwright)
make test-ci
```

**From ui/ directory:**
```bash
# Using nvm (recommended)
nvm use 20.19.4 && npm run test:e2e

# Run only layout tests
nvm use 20.19.4 && npm run test:e2e -- chat-docked-layout

# Run with UI (headed mode)
nvm use 20.19.4 && npm run test:e2e -- chat-docked-layout --headed
```

### Test Coverage

- ✅ Report max-width (920px)
- ✅ Chat width bounds (420px - 650px)
- ✅ No overlap between report and chat
- ✅ Responsive behavior at different viewports
- ✅ Chat position fixed during scroll
- ✅ Sidebar collapse state

## Visual Examples

### Standard Desktop (1920px)
```
┌──────┬────────────────────────────────────────────────────────────┬──────────────┐
│ Side │ Report (920px max)                                         │ Chat (650px) │
│ 72px │                                                            │              │
└──────┴────────────────────────────────────────────────────────────┴──────────────┘
        ├─ 24px padding                                              ├─ 24px gap
```

### Laptop (1600px)
```
┌──────┬────────────────────────────────────────────────────┬────────────┐
│ Side │ Report (920px max)                                 │ Chat (536px)│
│ 72px │                                                    │            │
└──────┴────────────────────────────────────────────────────┴────────────┘
```

### Small Screen (1440px)
```
┌──────┬──────────────────────────────────────────────┬──────────┐
│ Side │ Report (920px max)                           │Chat(420px)│
│ 72px │                                              │          │
└──────┴──────────────────────────────────────────────┴──────────┘
```

## Troubleshooting

### Chat Overlapping Report
- Check that offset calculation is correct (1064px)
- Verify report max-width is 920px
- Ensure grid is single-column in docked mode

### Chat Too Narrow
- Check min-width is 420px
- Verify viewport is wide enough (> 1484px for expansion)
- Check offset isn't too large

### Chat Too Wide
- Check max-width is 650px
- Verify clamp() is working correctly

### Large Gray Gap
- Verify grid is single-column (`minmax(0, 1fr)`, not `minmax(0, 1fr) 420px`)
- Check report wrapper doesn't have phantom columns

## Implementation History

**Issue**: Chat panel width in docked mode
**Fixed**: 2026-02-17
**Changes**:
1. Constrained report to 920px max-width in docked mode
2. Changed grid from two-column to single-column
3. Made chat width responsive using clamp() formula
4. Added CSS custom properties for maintainability
5. Added comprehensive e2e tests

**Related Files**:
- `ui/src/screens/CaseScreen.module.css`
- `ui/src/ui/AssistantChatWidget.module.css`
- `ui/tests/chat-docked-layout.spec.ts`
