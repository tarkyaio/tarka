# SRE Console UI

## Local dev (no backend required)

1) Install deps:

```bash
cd ui
npm install
```

2) Enable mock mode (create `ui/.env.local`):

```bash
VITE_MOCK_API=1
VITE_CHAT_ENABLED=1
```

3) Run:

```bash
npm run dev
```

This will serve the UI with **realistic mock** responses for:
- `GET /api/v1/cases`
- `GET /api/v1/cases/:caseId`
- `GET /api/v1/investigation-runs/:runId`

## Tests

### Unit tests (Vitest + React Testing Library)

```bash
cd ui
npm test
```

### E2E (Playwright)

One-time browser install:

```bash
cd ui
npx --yes playwright install chromium
```

Run:

```bash
cd ui
npm run test:e2e
```

## Real cluster dev (port-forward)

- UI:

```bash
kubectl -n tarka port-forward svc/tarka-ui 3000:80
```

Then open `http://localhost:3000`.

Notes:
- The UI container proxies `/api/*` to the in-cluster agent service (same-origin, no CORS).
- The Console API requires Google SSO (OIDC). Configure the Google OAuth client and `AUTH_ALLOWED_DOMAINS`.
