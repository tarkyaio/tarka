# UI styling guide (scalable + non-brittle)

This repo uses a **layered styling system** designed to scale as we add more pages and shared components.

## Layers

### 1) Global tokens + reset (`ui/src/styles/global.css`)
Global is for **design tokens** and truly global defaults only:
- CSS variables: colors, radii, typography families
- Minimal reset: `box-sizing`, `body` defaults, scrollbar styling
- Tiny global utilities (if needed): e.g. `.mono`, `.muted`

**Do not** add screen/component styles here.

### 2) Global base primitives (`ui/src/styles/base.css`)
Base defines **stable primitives** that make new pages look good immediately:
- Typography for semantic tags (`h1/h2/h3/p/small/code/pre`)
- Accessible focus rings (`:focus-visible`)
- A few prefixed primitives to avoid collisions:
  - `.uiCard`, `.uiCardHeader`, `.uiCardBody`
  - `.uiBtn*`, `.uiIconBtn`, `.uiInput`

Base primitives should be **small, composable, and unlikely to change**.

### 3) CSS Modules (default for everything else)

All screen/component styling should live in `*.module.css` colocated with the TSX:
- `ui/src/screens/Shell.module.css`
- `ui/src/screens/InboxScreen.module.css`
- `ui/src/screens/CaseScreen.module.css`
- `ui/src/ui/*.module.css`

CSS Modules prevent cross-screen collisions automatically.

## Conventions

### File placement
- **Tokens/reset**: `ui/src/styles/global.css`
- **Base primitives**: `ui/src/styles/base.css`
- **Component/screen styles**: `*.module.css` next to the TSX file

### Class naming
- In CSS Modules, use **semantic names** (`toolbar`, `title`, `meta`, `row`) — collisions are handled by modules.
- Avoid exporting generic global classes; prefer base primitives prefixed with `ui*` when global classes are required.

### Inline styles policy
Inline styles in TSX are allowed **only** for:
- Truly dynamic values (e.g., popover coordinates, a computed width)
- Rare one-off cases where a class would be more complex than the value

Everything else should be a class (module or base primitive).

## Building new pages quickly

### Start with semantic structure
Use `h1/h2/h3/p` and the page will inherit consistent typography from `base.css`.

### Use primitives for common surfaces
Use the `Card` component (`ui/src/ui/Card.tsx`) for consistent layout:
- Header/title styling
- Body padding
- Consistent border/background

Use `IconButton`, `MetaPill`, and `ClassificationPill` from `ui/src/ui/` for common UI patterns.

## Checklist for PRs
- No new screen/component styles added to `global.css`
- New visual styling is in a `*.module.css`
- Inline styles are only used for dynamic values
- New primitives are added to `base.css` only if they are broadly reusable and stable
