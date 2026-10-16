# DV02 Fund dashboard

The trading dashboard: a PM-style cockpit for the paper book, set as a
broadsheet's financial pages. A masthead (status line, the DV02 nameplate between
two ears, the dateline under a thick-and-thin rule), a section bar that stays in
view, a markets strip, then the page: serif headlines and headline figures,
condensed capitals for section heads, tabular figures in the tables, black rules
instead of boxes. Dark (the night edition) is behind the theme button, remembered
as `dv02.theme`. On a phone the nameplate stays on top and the sections become a
tab bar at the bottom.

```bash
cd apps/web
npm install
npm run dev        # http://localhost:5173 with the demo backend (simulated data)
npm run dev:api    # same UI, /api proxied to the FastAPI service on :8000
```

Demo mode simulates the $1M paper book, deterministically, from Oct 15, 2025.
`?demo_now=2026-10-09T18:12:00Z` pins the market clock (useful on weekends).
The header's mode badge reads **Demo** while it runs on simulated data.

## Pages

| Page | Shortcut | What it answers |
|---|---|---|
| Command Center | `g c` | The verdict and readiness checks; NAV and P&L today / MTD / YTD / since inception vs S&P; intraday P&L; exposure; limits; biggest movers; schedule |
| Positions | `g p` | The whole book: filters, allocation by asset class, sortable columns, drift vs target, totals |
| Performance | `g f` | Growth vs any benchmark, drawdown, Sharpe with its confidence interval, monthly grid |
| Risk | `g r` | Central risk book: beta, vol, VaR/ES, exposure, limits, sleeve mandates, stress scenarios |
| Trading | `g t` | Rebalance preview with pre-trade checks, typed-confirmation submit, blotter with cancel and slippage |
| Operations | `g s` | System health, kill switch, IBKR doctor, schedule, run history |

## How it fits together

- `../../schemas/api/v1.yaml` is the API contract. `npm run gen:api` regenerates
  `src/api/schema.d.ts`; CI fails if they drift.
- `src/mocks/` is the demo backend: an MSW service worker implementing the
  contract from a seeded simulation (`sim.ts`). The FastAPI service implements
  the same contract over real data.
- `src/design/tokens.css` holds every colour, font and size, for both themes.
  Chart colours (the series and the asset-class palette) were checked with the
  dataviz palette validator (colour-blind and normal-vision separation,
  contrast) on each theme's page. WSJ's own faces (Escrow, Retina) are
  proprietary; the tokens use Newsreader (with optical sizes) for headlines and
  headline figures, IBM Plex Sans Condensed for section heads and table figures
  (its digits are all one width, so columns line up), and Public Sans for text.

## Every control works: the control contract

Every interactive element has a `data-control` id listed in `e2e/controls.ts`.
`npx playwright test` enforces two rules:

1. any control rendered anywhere must be in the registry, and
2. every registry entry must be exercised by a spec (via `ctl()` or `covers()`).

So a new button without a test fails CI.

```bash
npm run typecheck && npm run lint && npm test   # types, lint, unit tests
npx playwright test                              # e2e against the demo backend
```
