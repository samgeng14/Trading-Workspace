# DV02 Fund: Trading Platform Plan

October 12, 2026 · Status: Phase 0 IBKR plumbing done; Phase 1 dashboard done and on the paper account; Phase 3 C++ engine started

This is the plan for turning `Trading-Workspace` from a daily ETF rebalancer
into a full paper-trading platform: a newspaper-style frontend, a C++
execution and risk engine, a real data platform built on WRDS, a central
risk book, and a set of strategies that together target a Sharpe ratio
of 1.0 or better on the $1M IBKR paper account. The fund is long/short with
no leverage.

Build order: **stabilize what exists (Phase 0), then frontend first
(Phase 1)**, with the data platform and the C++ engine following behind
a fixed API contract. That way nothing built early gets thrown away.

---

## 1. Summary of decisions

| Topic | Recommendation | Why |
|---|---|---|
| Fund mandate | **Long/short, strictly no leverage.** Gross (long + \|short\|) ≤ 100% of NAV, with futures at full notional and options at delta-adjusted notional once that can be measured (until then any option blocks). Leveraged and inverse ETFs count at their economic exposure (TQQQ at 3×). Cash ≥ 0 after orders, so no margin loan. Enforced in C++ on every order path (the dashboard's submit and the 15:35 ET daily run), counting every holding in the account, not just the model's. *Decided.* | Your rule. A check that sees only the model's targets misses everything else in the account. |
| Languages | **C++ and Python only**; TypeScript only for the dashboard UI (`apps/web`). No Rust in our code: the Rust `hawk-backtester` has been ported to C++ as `dv02_engine.backtest`. *Decided.* | Fewer toolchains to build, test and review. Bindings already share the C++ math with Python, so a third language adds nothing. |
| Universe | **Anything IBKR trades**, subject to the mandate and the account's permissions: stocks, ETFs, futures, options, bonds, FX, crypto. *Decided.* | Each sleeve trades where its edge is. Permissions (Section 7) and the no-leverage check decide what can actually trade. |
| Frontend language | **TypeScript + React + Vite**, hand-built design system, no UI kit theme. *Decided.* | Best tooling for dense, live, data-heavy screens. The "vibe-coded" look comes from default kits (Tailwind/shadcn gradients, glows, rounded cards), not from React. |
| Look | **DV02, Journal theme by default**: a WSJ-style financial page. Warm-white newsprint, black ink, black rules instead of boxed cards, a masthead with the nameplate between two ears and the dateline under a thick-and-thin rule, condensed capitals for section heads, a news serif for headlines and headline figures, condensed tabular figures in the tables, one ink blue for links and the portfolio line, the paper's green and red for gains and losses; a Command Center cockpit; a tab bar on phones. A dark "night edition" stays one click away. *Decided (October 12); redesigned in the same style on October 16, after the owner rejected a dark neon terminal look.* | What you asked for. It also suits a finance tool: dense, calm, readable. |
| Middle layer | **FastAPI service with a versioned contract** (OpenAPI + WebSocket schemas); TypeScript types generated from it | The frontend can ship now on the existing Python system. Later the C++ engine is swapped in underneath without touching the UI. |
| Engine | **C++20 daemon (`tradeitd`)** for broker connections, order management, the central risk book, hedging and intraday strategies | This is where C++ pays off: one long-lived broker connection, deterministic event loop, replayable state, low latency later. |
| Research | **Stays in Python** (pandas + DuckDB). C++ risk and simulation code is exposed to Python via bindings. Polars is built in Rust, so whether third-party Rust-built tools (Polars, the ruff linter) may stay is an open decision (Section 15). | Sharpe comes from data and signals, not language. WRDS tooling is Python/SQL. Shared bindings keep research and production math identical. |
| Storage | **Parquet + DuckDB** for research and market data; **Postgres** for orders, fills, positions and NAV | Zero-ops analytics on a laptop. A real transactional store for the trading record. |
| Where it runs | **Phase 0–1: laptop. Phase 2+: Oracle VM, always on** (gateway, engine, Postgres, API). Laptop becomes the cockpit. | Scheduled MOC orders and the auto-hedger need a process that is awake at 3:40pm ET. A laptop is not reliable for that. |
| Cloudflare | Tunnel + Access (private dashboard from your phone), R2 (private backups), Workers cron (heartbeat alerts) | Cheap, secure, nothing exposed publicly. Not used as a database or for compute. |
| Hedging | **Linear risk with futures and ETFs** (MES/ES, sector ETFs, ZN); **options only for tail convexity**, with a fixed premium budget. Hedges count toward the 100% gross limit like any position. | Options bleed premium when used to hedge plain beta. Futures are the cheapest beta hedge. |
| Path to Sharpe ≥ 1 | **4–6 economically motivated, low-correlation sleeves**, each targeting about 0.5, combined inside one 100% gross budget | No single model reliably gets to 1.0 net of costs. Diversification across sleeves can, without leverage (Section 9). |

---

## 2. Where things stand

**Where we are (October 12):**

- **The dashboard is connected to the paper account** (DUP…) through
  `services/api`.
- **NAV since inception is loaded from Flex:** 131 days through October 9.
- **The price-master gap is refilled.** `tradeit refresh` now finds holes in
  the price history and backfills them before the dry run, so targets are
  rebuilt on complete history.
- **The book is levered today.** It is 102.5% long with a margin loan of about
  $26k, and about 15 of its holdings are outside the 22-ETF model; the engine
  used to leave those alone. The new C++ no-leverage check counts every
  holding and blocks any basket that leaves the book levered. Decided
  (Section 15): those holdings are sold at the next rebalance, and TQQQ (a 3×
  ETF, counted at 3×) leaves the model, so the rebalance brings the book back
  under 100% gross with no margin loan.
- **Live streaming over a WebSocket.** About every 2 seconds the API sends the
  dashboard status, quotes, the summary, positions, intraday P&L and orders,
  whichever changed. On paper the quotes are 15 minutes delayed unless
  real-time data is shared to the paper login (Section 7).
- **The equity curve has a dollar view:** it shows NAV in dollars as well as
  growth of $100.
- **The dashboard looks like a financial page:** the Journal (WSJ-style)
  theme is the default, with the dark theme one click away (Section 4).
  (Redesigned in the same style on October 16; Section 4.)
- **The C++ engine has started** (`engine/`): CMake, C++20, nanobind bindings
  built by scikit-build-core, an in-repo test harness, and CI with sanitizers.
  It has the no-leverage check and the backtester port (Section 6).

**October 13:** Phase 0's safety work landed (PR #12). Every order path runs
the kill switch and the no-leverage check inside the one function that places
orders. The daily run skips NYSE holidays; reconciles the orders it sent
against IBKR first (an expected book of positions plus orders at their fills;
a break of $100 or more arms the kill switch); decides drift from today's
targets against the positions now; blocks on a model ETF whose prices are
stale; and takes its limits from `risk_limits.yaml`. `bin/tradeit kill` cancels
working orders and `resume` needs the doctor and a reconcile pass; the old
16:32 scheduler is gone. The daily NAV since inception comes from a one-time
PortfolioAnalyst import (the account's history starts 2026-03-06) plus Flex
from 2026-04-15, and the API records every close. Infrastructure: everything
still runs on the Mac (IB Gateway in Docker, the API and dashboard on demand,
nothing scheduled until `bin/tradeit schedule install`); the Oracle VM move
(Phase 2) has not started. The doctor passes against the paper gateway; quotes
are delayed until market data is shared to the paper login.

An audit of the current repo (October 2026) found a working core with a lot
of scaffolding around it that is partly or entirely disconnected. The full
list is in Appendix A. The items that matter most:

**Fixed in this PR (IBKR plumbing):**

- **Ports and client IDs.** There were four different default ports (4002, 4004,
  7497, 4003) and about 15 hardcoded client IDs. `broker.yaml` pointed paper at
  4004, the docker container's *internal* port. The executions reader, the
  reconciler and `submit_one` could never reach a laptop gateway.
  - Now there is one resolver, `src/broker/ibkr/settings.py`. Precedence is env > `.env` > `broker.yaml` > defaults.
  - Each role has a fixed client ID, so orders are always placed (and can later be cancelled) from the same ID.
- **Paper mode could land on a live session.** Port autodetect in paper mode fell
  back to the *live* ports. It now probes only same-profile ports. Order,
  basket and reconcile connections also refuse to run if the account type doesn't match
  (paper accounts start with `D`).
- **End-of-day reconcile reported "clean" when IBKR was unreachable.** The
  reconciler returned an empty diff list on connection failure. It now raises,
  and the EOD result is `ok=False`.
- **The dashboard's "Run audit" button could cancel the 4:32pm trading run.** It wrote an
  `ok: true` run log, and the scheduled job skips if today already ran. Dry
  runs no longer count.
- **The daily data append only worked under `/Users/tradingworkspace/`.** It now
  uses project-relative paths.
- **New: `python -m src.broker.ibkr.doctor`.** An end-to-end connection check
  covering socket, API handshake, account type, balances, positions, open
  orders, clock skew, contract qualification, daily bars, live vs. delayed
  quotes, and whatIf previews for a buy and a short. Each failure prints the fix.
  **Run it on your laptop with the gateway up.** This container can't reach IBKR,
  so the code was tested against a simulated gateway only.

**Still open, and fixed in Phase 0:**

- **Orders filled at the wrong time (fixed).** They were market orders sent at 4:32pm,
  after the close, so they filled at the *next day's open*. Now the daily run starts at
  15:35 ET (`tradeit schedule install` writes that in the Mac's time: 14:35 in Central
  time) and builds targets from the last completed close: a bar for a session still
  trading is dropped when bars are appended and again when targets are built. Its
  orders go market-on-close, filled at that day's close, when sent by 15:45 ET, five
  minutes before the earliest cutoff the engine uses (NYSE and NYSE Arca 15:50 ET,
  Nasdaq and Cboe BZX 15:55); an early close moves the deadline to 12:45. Sent later,
  or when the market is closed, they go market-on-open for the next session, never as
  plain market orders (`src/broker/ibkr/auction.py`). So a signal from the close of day
  *t−1* fills at the close of day *t* (Section 7). Not yet checked on the paper account:
  its MOC fills, and that IBKR holds an OPG order sent during the session for the next open.
- **Prices are unadjusted (fixed for signals, 2026-10-16).** The master stays IBKR
  TRADES bars, split-adjusted, for order prices. Momentum and volatility run on
  IBKR's dividend-adjusted closes (`src/data/cleaning/total_return.py`), so bond
  ETFs' (TLT, IEI, SHY, BIL) income counts. Not yet run against IBKR.
- **The drift-rebalance gate reads the previous run's reconciliation file.**
  So "composition changed" never fires, and drift re-fires after every cooldown.
- **The EOD reconcile is never scheduled.** Its notion of the "book" is a sum of
  order deltas, not positions.
- **Most config is ignored.** That includes `risk_limits.yaml`, `execution.yaml`
  `dry_run` / `allow_submit`, and the `app.yaml` schedule.
- **The old MOSEK license is still in git history** until it expires October 21.
  `mosek.lic` is out of the tree and `*.lic` is git-ignored. The research optimizers
  use MOSEK only with a license from `MOSEKLM_LICENSE_FILE` or `~/mosek/mosek.lic`,
  and otherwise CLARABEL; target generation for the live book uses no solver.
- **The price master in git still ends on 2026-05-06.** `tradeit refresh`
  refills it locally (above).
- **No trade history beyond today.** The IBKR API only returns the current day's
  fills. Flex now supplies NAV since inception (Section 7); the trade record
  from Flex is still to do.

---

## 3. Target architecture

```
                   ┌──────────────────────────────────────────────┐
  Browser / phone  │  apps/web  — TypeScript + React (DV02 Fund)  │
                   └───────────────▲──────────────────────────────┘
                     REST + WebSocket (contract v1, generated types)
                   ┌───────────────┴──────────────────────────────┐
  Middle           │  services/api — FastAPI                       │
                   │  read models · commands · auth · WS fan-out   │
                   └──────▲─────────────────────▲─────────────────┘
               NATS (protobuf)            SQL / DuckDB
                   ┌──────┴──────────┐   ┌──────┴───────────────────────┐
  Backend          │ engine/ (C++20) │   │ Postgres: orders, fills,     │
                   │ tradeitd        │──▶│ positions, NAV, runs, events │
                   │ ─ IBKR adapter  │   └──────────────────────────────┘
                   │ ─ Kalshi adapter│   ┌──────────────────────────────┐
                   │ ─ OMS / algos   │   │ Parquet lake + DuckDB:       │
                   │ ─ CRB / hedger  │◀──│ prices, fundamentals, WRDS,  │
                   │ ─ intraday host │   │ features, targets            │
                   └──────▲──────────┘   └──────▲───────────────────────┘
                     targets (protobuf)         │
                   ┌──────┴─────────────────────┴─────────────────────┐
  Research / batch │ research/ + data/ — Python (pandas, DuckDB)      │
                   │ WRDS / FactSet / EDGAR ingestion · factor models │
                   │ daily & monthly strategy sleeves · backtests     │
                   └──────────────────────────────────────────────────┘
```

**Contracts are the backbone.** All of them live in `schemas/` and are the
single source of truth:

1. **API contract** (frontend ↔ middle): OpenAPI plus WebSocket message schemas.
2. **Engine bus** (middle ↔ engine): protobuf over NATS.
3. **Sleeve contract** (strategy → engine): every strategy, in any language,
   emits the same message: *target positions plus a declared mandate*.

```
Sleeve contract (one message per sleeve per decision)
  sleeve_id, as_of, horizon (intraday | daily | weekly | monthly | event)
  targets: [{instrument, target_qty | target_weight, valid_until, urgency}]
  mandate: allowed exposures, e.g. {beta: [-0.05, 0.05], sector: neutral}
  risk_budget: target vol, max gross
  data_watermarks: the dataset versions the decision used
```

The mandate is what lets the central risk book hedge only *unintended*
risk. A trend sleeve is allowed to be long the market. An equity
long/short sleeve is not.

**Different time horizons** fit into this one shape:

| Horizon | Who decides | Example |
|---|---|---|
| Monthly / weekly | Python batch job | Equity L/S, macro tilts |
| Daily | Python batch job | Trend, carry, vol targeting |
| Event | Engine or Python | Kalshi / ForecastEx, earnings events |
| Intraday | C++ inside the engine | News momentum, auto-hedger |

The engine treats all of them the same way: targets in, net orders out.

**Proposed monorepo layout.** Migrate gradually; `trading-engine/` keeps
running until each piece is replaced.

```
apps/web/          frontend
services/api/      FastAPI middle layer (grows out of src/dashboard backend)
engine/            C++ engine: core, brokers/{ibkr,kalshi,sim}, oms, crb, risk, sim, bindings
research/          Python research library (factors, backtests, sleeves)
data/              ingestion jobs, dataset catalog, security master
schemas/           OpenAPI, WS messages, protobuf
cli/               `tradeit` command
infra/             docker compose (gateway, postgres, nats), systemd units, tunnel config
docs/
```

---

## 4. Frontend (Phase 1, first thing built)

### Stack

- **TypeScript + React + Vite**, as a single-page app. No SSR: it's a private
  app talking to your own API.
- **TanStack Query** for server state; **TanStack Table** with virtualization
  for blotters and positions.
- **Charts:**
  - **visx** (React + D3 primitives) for editorial charts: equity curve,
    drawdown, attribution.
  - **uPlot** for fast streaming panels.
  - Both are styled by hand. No default chart themes.
- **Radix UI primitives** (unstyled, accessible) for menus, dialogs, tabs and
  tooltips. **CSS Modules + CSS custom properties** for styling. No Tailwind,
  no component-kit theme.
- **API client generated** from the OpenAPI spec (`openapi-typescript`), so a
  backend change that breaks the UI fails type-checking.
- **MSW (Mock Service Worker)** fixtures, so every page can be built and
  tested before its backend endpoint exists.
- **Vitest** for units; **Playwright** for end-to-end tests.

Alternatives considered:

- **Svelte / Solid** would be fine; the ecosystem for grids and charts is smaller.
- **A native C++ UI (Qt/ImGui)** would match the engine language, but
  editorial typography is much harder, you lose phone access, and iteration is slower.

### Design system

Built in `apps/web/src/design/`. Every colour, font and size is a token in
`tokens.css`, for both themes, and components use only those. Redesigned on
October 16 in the same broadsheet style; a dark neon "terminal" look was tried
first and the owner rejected it.

- **Type**
  - *Newsreader* (a news serif, variable, with optical sizes) for the
    nameplate, page headlines, decks and headline figures (NAV, the data cells).
  - *IBM Plex Sans Condensed* for section heads, labels, table heads and every
    figure in a table: its digits are all one width, so columns line up and a
    ticking number never shifts, the way a paper sets market data.
  - *Public Sans* for running text.
- **Colour (Journal, the default)**
  - Warm-white newsprint (`#faf9f5`), black ink (`#111`), grey hairlines,
    black rules over every section instead of boxed cards, zebra rows in the
    market tables.
  - Black for the active section, selected options and primary buttons.
  - Ink blue (`#0f6aa8`) for the portfolio line and links, orange (`#c4621a`)
    as the second series, warm grey for benchmarks; asset classes take the
    dataviz reference palette's slots in a fixed order.
  - Green (`#0a7b34`) and red (`#c4161c`) for gains and losses, always with a sign.
  - Status text (good / warn / bad) has its own tokens, separate from P&L colours.
- **Colour (the night edition, one click away)**
  - Near-black page (`#0e0d0c`), cream ink (`#ece4d6`), sand accent
    (`#d6b07a`), ochre and blue series, sage and coral for gains and losses.
    Same layout, other tokens; the toggle remembers the choice.
  - All chart colours were run through the dataviz palette validator
    (lightness band, chroma, colour-blind and normal-vision separation,
    contrast) in both themes. All text clears WCAG 4.5:1.
- **Structure**
  - Masthead: a status line (mode, market session with a countdown and a bar
    for how far through the session it is, the pre-trade gate, the stream,
    system lights, exchange clock, Halt trading, theme); the DV02 nameplate
    between two ears (the edition and account; the gateway, today's orders and
    the next job); the dateline under a thick-and-thin rule.
  - Section bar under the masthead, in capitals, kept in view as the page
    scrolls; a markets strip under it (headline markets, the book's biggest
    gainers and losers, other markets). No animation.
  - Pages open with a section label, a serif headline and a one-line deck.
    Panels have condensed-capital heads under a black rule; columns are
    divided by hairline column rules; no glow, gradients or rounded cards.
  - Phones: the nameplate on top, the status line under it, the sections as a
    tab bar at the bottom; wide tables keep the symbol column in place.
- **Charts (WSJ graphics conventions)**
  - y labels in their own gutter, clear of the lines.
  - Lines are labelled directly at their ends.
  - Horizontal hairlines only; flat washes.
  - A crosshair tooltip lists every series and works from the keyboard
    (arrows, Home, End).
  - Every chart has a Table view.
- **Not allowed:** gradients, glows, glassmorphism, drop shadows, rounded
  cards, emoji, animated counters, neon-on-black.
- **Still to build:** a `/design` style-guide route.

### Pages ("sections")

1. **Front Page.**
   - NAV, day P&L, MTD / YTD / since inception vs. benchmark.
   - Sharpe since inception *with a confidence interval*.
   - Gross, net and beta; today's run status; alerts.
   - A small since-inception chart.
2. **Performance.**
   - Equity curve vs. a **benchmark switcher**: S&P 500, Nasdaq-100, Russell 2000,
     MSCI ACWI, 60/40, Aggregate bonds, T-bills, or any ticker. All total return.
   - Drawdowns; rolling Sharpe, vol and beta; monthly returns grid.
   - Alpha, beta, tracking error and information ratio vs. the selected benchmark.
3. **Positions & Exposures.** Live, streaming positions; sector and factor
   exposures; breakdown by sleeve.
4. **Risk Desk (central risk book).** Net exposures vs. limits, the hedge book,
   scenario shocks, and the auto-hedger log.
5. **Strategies.** Per sleeve: live vs. backtest tracking, latest signals,
   attribution, enable/disable.
6. **Trading.**
   - Rebalance preview (basket review); submit and cancel, with confirmation.
   - Orders and fills blotter.
   - Slippage and transaction-cost analysis vs. arrival and close.
7. **Data.** Freshness of every dataset (watermark vs. expected), gaps, last
   ingest, row counts.
8. **Operations.** Doctor results, schedule, run logs, kill switch, engine and
   gateway health.
9. **Prediction Markets** (deferred). Kalshi and ForecastEx positions and event exposure.

Built so far, on the demo backend and on the paper account:
- Command Center: the verdict (all clear / review / action required),
  headline P&L and risk, a readiness checklist, and blockers and warnings,
  each linking to the page that owns it
- Positions
- Performance
- Risk
- Trading
- Operations

Still to come: Strategies and Data, which wait on the sleeve framework and the data platform.

### "Make all the buttons work": the control contract

Every interactive control is registered with these fields:

- `id`
- the endpoint it calls
- what it changes
- whether it needs confirmation
- a Playwright test that clicks it and checks the result against the
  simulated broker

CI fails if a control has no test. Appendix B lists every control in the
current dashboard; that list is the acceptance checklist for the rewrite.
Several of today's controls are dead or misleading, e.g. the Quant Research
tab has no backend and the Optimizer dropdown has no effect.

### Real time

- One WebSocket per browser tab, `/api/v1/stream`. Each message names its
  topic (`status`, `strip`, `summary`, `positions`, `intraday`, `orders`), and
  its data is exactly that endpoint's REST body, so a panel has one shape
  whether it polls or streams.
- The server computes every topic once per tick (about 2 seconds) and sends
  only the ones that changed. A slow client never makes it buffer: a newer
  value replaces the one waiting. Tables update cells in place.
- Still to add: `runs` and `alerts` topics.

### Phase 1 status

**Done:**
- API contract v1: `schemas/api/v1.yaml`, 16 endpoints plus the `/stream`
  WebSocket, with TypeScript types generated from it.
- `apps/web`: shell, design system, and six sections.
- A deterministic demo backend: MSW plus a seeded simulation of the $1M book
  since inception, pinned to the real 22-ETF universe and a futures hedge.
- The control contract (`e2e/controls.ts`), with Playwright tests for every
  control and a meta-test that fails on unregistered or untested controls.
- Web CI (`.github/workflows/web.yml`).

- `services/api`: the same contract over real data (see its README).
  - **Live from IBKR:** one long-lived connection (client 307) for account,
    positions, P&L, quotes and orders.
  - **History:** NAV from IBKR Flex plus the API's own close records; bars
    cached from IBKR.
  - **Engine reuse:** the rebalance preview uses the engine's basket math and
    risk gate, plus the C++ no-leverage check over every holding.
  - **Submit:** starts the engine's daily runner, so there is one order path.
  - **Kill switch, runs and doctor:** read from the engine's own files and modules.
- `bin/tradeit setup | doctor | up | demo`: the first cut of `tradeit up`.
- API CI (`.github/workflows/api.yml`): contract tests with a fake broker.
- Running on the paper account, with NAV since inception from Flex.
- WebSocket feed: status, quotes, summary, positions, intraday P&L and orders
  (Real time, above).
- Equity curve with a dollar view.

**Next:**
1. Strategies and Data sections.
2. Delete the old static dashboard once Appendix B is covered.

---

## 5. Middle layer (`services/api`)

- **Contract-first and versioned** (`/api/v1`). It grows out of the current
  dashboard backend. The routers are rewritten against the contract; the old
  static frontend is deleted once the new pages replace it.
- **Read models:**
  - SQL over Postgres (orders, fills, positions, NAV).
  - DuckDB over the Parquet lake (prices, factor exposures, backtests).
- **Commands:**
  - rebalance preview / submit
  - cancel
  - kill / resume
  - hedge now
  - run catch-up
  - run doctor

  Until the engine exists, commands call the existing Python. After that,
  they go to the engine as NATS request/reply. The engine enforces risk
  regardless of who asks.
- **Auth:** localhost-only by default. Remote access goes through Cloudflare
  Access, which puts an email login in front of the tunnel. The IBKR gateway
  port is never exposed.

---

## 6. C++ engine (`tradeitd`)

### Scope

| Component | Responsibility |
|---|---|
| `core` | Single-threaded deterministic event loop, timers, and a clock abstraction (real or simulated). The same code runs live, in replay and in backtests. Append-only event journal; state is rebuilt from journal plus broker reconciliation on restart. |
| `brokers/ibkr` | Official TWS API C++ client. **One long-lived connection** on the fixed `orders` client ID. Handles reconnect and the gateway's daily restart, applies a pacing limiter for historical requests, and maps IBKR error codes. |
| `brokers/kalshi` | REST + WebSocket client with signed API-key requests. Runs in the same OMS, as a separate venue. |
| `brokers/sim` | Simulated broker with a fill model, used for development, CI, frontend tests and replay. |
| `marketdata` | Normalized quotes and bars. Subscription manager that respects IBKR's default 100 concurrent market-data lines. |
| `oms` | Order state machine, idempotent client order IDs (`orderRef`), persistence, start-of-day reconciliation. |
| `algos` | MOC/LOC for close-rebalanced sleeves, TWAP slicing, mid-peg for liquid ETFs, futures roll. |
| `crb` | Netting, exposures, limits, hedger (Section 8). |
| `risk` | The no-leverage pre-trade check on every order. Loads the daily factor model from research; real-time beta, sector, factor and (later) options-greek exposure. |
| `bindings` | nanobind module (`dv02_engine`) exposing `risk`, the backtester and later `crb` to Python, so backtests use the production cost and risk math. |

### Toolchain

- C++20, CMake presets + Ninja, vcpkg manifest.
- Libraries: asio, fmt/spdlog, protobuf, nats.c, simdjson, GoogleTest, nanobind.
  The TWS API also needs Intel's decimal floating-point library. Tests use a
  small in-repo harness until CI can fetch GoogleTest.
- clang-tidy, clang-format, and AddressSanitizer / UndefinedBehaviorSanitizer builds in CI.
- Builds on macOS (laptop) and Linux (VM). **Check whether the Oracle VM is ARM
  (Ampere) or x86.** The engine compiles on either, but the IB Gateway docker
  image's ARM support needs verifying.

### Status (October 12)

`engine/` builds a C++20 library and the Python module `dv02_engine` (CMake,
nanobind bindings built by scikit-build-core; `pip install ./engine`). CI
(`.github/workflows/engine.yml`) runs the tests with GCC under
AddressSanitizer and UndefinedBehaviorSanitizer, with Clang and on macOS, and
tests the Python module. `engine/README.md` has the details.

What exists:

- **The no-leverage risk gate** (`dv02_engine.risk`). It takes the whole
  account (every holding, not just the model's) plus the orders about to go.
  It blocks if, after the orders, gross is over 100% of NAV (futures at full
  notional), cash is below 0 or one name is over 25% of NAV, and it blocks
  anything it can't price. Options block until delta-adjusted notional is
  measured. Orders that only sell down may go while the book is over a limit.
  The dashboard's submit and the daily runner both call it.
- **The backtester** (`dv02_engine.backtest`), ported from the Rust
  `hawk-backtester` with the same call and frames.

Build order from here:

1. **No-leverage risk gate.** Done.
2. **Backtester.** Ported from Rust. Next it gets the cost model shared with
   live (commissions, spread, impact, borrow, futures roll) and walk-forward runs.
3. **`tradeitd` v0:** the read-only IBKR mirror on the official TWS C++ API
   (migration step 1 below).
4. **Shadow OMS** (step 2), then the engine submits (step 3).
5. **CRB and auto-hedger** (step 4, Section 8).
6. **Intraday host** for the intraday sleeves (step 5).

### Migration: replace piece by piece, never big-bang

1. **Engine v0, read-only mirror.** Holds the one IBKR connection and mirrors
   account, positions, orders and fills into Postgres and the bus. This powers
   the live dashboard and replaces the scattered Python connections. Low risk,
   immediately useful.
2. **Shadow OMS.** Python still submits. The engine receives the same targets
   and logs the orders *it would have* sent; compare them daily for two weeks.
3. **Engine submits.** The Python submit path is deleted.
4. **CRB and auto-hedger go live.**
5. **Intraday sleeves** run inside the engine (Phase 5).

Research backtests call `dv02_engine.backtest` through the bindings, so the
Rust `hawk-backtester` is no longer needed.

---

## 7. IBKR specifics

- **One session per username.** The paper login can run in only one
  Gateway/TWS at a time. Once the gateway lives on the VM, don't log in to
  paper TWS on the laptop; use the dashboard.
- **Gateway in docker with IBC** (`ibgw/docker-compose.yml`, host port 4002).
  It auto-restarts daily, and the engine reconnects.
- **Trade history and NAV: Flex Web Service.**
  - The API only returns *today's* executions. For "all the trading history",
    create an Activity Flex Query in the paper account's Client Portal with
    these sections: Trades, Cash Transactions, Open Positions, Net Asset Value
    (daily), and Change in NAV.
  - Pull it with the Flex token:
    1. `SendRequest` returns a reference code.
    2. `GetStatement` returns the XML.
    3. Each request covers at most 365 days.
  - Backfill once from inception (2025-10-15 per config). After that, pull T-1
    every morning.
  - This is the **source of truth** for the since-inception chart. The API
    covers intraday.
- **Market data.**
  - Paper accounts only get real-time data shared from the live login. Enable
    sharing in Client Portal (live user) → Settings → Paper Trading Account.
  - Subscriptions needed:
    - US securities streaming/snapshot for ETFs and stocks.
    - CME/CBOT/NYMEX/COMEX non-professional bundles for futures.
    - OPRA for options.
  - The doctor's `quotes` check shows whether you are getting live or delayed data.
  - **With a ~$10/month budget,** subscribe the live login to the **US
    Securities Snapshot and Futures Value Bundle** ($10/month).
    - It covers real-time top-of-book for US stocks and ETFs and the CME,
      CBOT, COMEX and NYMEX futures exchanges.
    - The fee is waived in months with at least $30 of real commissions; paper
      trades don't count.
    - The live account must stay funded (IBKR terminates data below about
      $500 equity).
    - Then turn on sharing to the paper user.
  - **Caveat for the VM:** if the live and paper sessions run at the same time
    on *different* machines, the paper session gets no live data. Keep the live
    login closed while the VM gateway runs, or add a second live user.
- **Limits.**
  - About 100 concurrent market-data lines by default: stream holdings only,
    and use bars or snapshots for large universes.
  - Historical-data pacing limits: batch requests and cache in the lake.
  - At most 32 API clients per session.
- **Orders.**
  - NYSE and Nasdaq closing-auction (MOC/LOC) cutoff is **3:50pm ET**. Close-rebalanced
    sleeves submit at about 3:40pm.
  - Recommended convention: signal computed on the close of day *t−1*, traded
    MOC on day *t*. The backtest does exactly the same thing, so live and
    backtest line up.
- **Futures and options permissions.**
  - Request them on the live account: Client Portal → Settings → Trading
    Permissions. Paper permissions generally follow the live account's.
  - IBKR approves based on the trading experience (years, trades per year),
    knowledge, income, net worth and objectives in your profile. A request
    "not working" usually means the profile doesn't meet their bar for that product.
  - Update Trading Experience and Financial Information honestly, then
    re-request. The denial reason is shown on screen, and support chat can tell
    you what's missing.
  - Until futures are approved, the CRB hedges beta with SPY (short) instead of MES.
- **Shorting.** Requires a margin account with short permission; the doctor's
  `short preview` checks this. The margin account is there only for shorting:
  the mandate keeps cash ≥ 0, so the fund never borrows cash. IBKR publishes
  shortable quantities and borrow fees. Use them in backtests, because **paper
  doesn't charge realistic borrow costs**.
- **Paper caveats.** Simulated fills are optimistic: no queue position and
  little size impact. Track implementation shortfall vs. the model anyway,
  so the numbers mean something when real money arrives.

---

## 8. Central risk book (CRB) and auto-hedger

This works the way multi-manager platforms run one. Strategies never send orders directly.

1. **Collect.** Each sleeve submits targets plus a mandate (sleeve contract).
2. **Net.** Targets are summed per instrument. The broker only sees *net*
   trades. If sleeve A buys AAPL while sleeve B sells it, that is an internal
   cross with zero cost. Each sleeve keeps a virtual position and P&L for attribution.
3. **Measure.** Exposures of the netted book:
   - dollar beta to the S&P 500
   - sector
   - style factors (size, value, momentum, quality, low-vol)
   - rates duration (DV01)
   - commodities and FX
   - options vega and gamma
   - event exposure from prediction markets
4. **Limit.**
   - Gross (never over 100% of NAV, hedges included), net, single name,
     factor, and expected shortfall.
   - **Drawdown throttles per sleeve and for the book**, pod-style:
     - −5% from high-water mark → risk halved
     - −10% → flat and reviewed
5. **Hedge.** Exposure *outside a sleeve's declared mandate* is hedged with
   the cheapest instrument. A small optimization chooses the minimum-cost set of
   hedges that brings exposures back inside their bands. A hedge adds gross
   like any position, so when the book is at its gross limit the hedge has to
   come with a cut elsewhere.
6. **Execute.** Net orders go to the algos.

**Hedge instruments by risk:**

| Risk | Hedge | Notes |
|---|---|---|
| Equity beta | SPY (fine granularity) → MES/ES futures (cheaper at size) | One MES is about $5 × index, roughly $30k notional, so use SPY for the residual. |
| Sector | Sector SPDR ETFs | |
| Rates | ZN / micro Treasury futures, or TLT/IEF | |
| Tail / crash | SPX or SPY put spreads, about 3 months, 10–15% out of the money | **Fixed premium budget** (e.g. 50–100 bp of NAV per year). Only worth it if it allows more risk elsewhere. Waits until the risk gate measures options at delta-adjusted notional; until then it blocks any option. |
| Momentum crash | *Not puts.* Scale momentum exposure down when market vol is high after drawdowns. | Momentum crashes happen in sharp rebounds (Daniel & Moskowitz 2016); index puts don't hedge them. |
| Event (CPI, FOMC) | Kalshi / ForecastEx contracts | A natural use of the prediction-market desk. |

On "hedge using options?": use options for convexity, not for linear beta.
A market-neutral long/short book's main tail is a *factor* crash, which index
options don't cover well.

**Auto-hedger.**

- Runs every 5 minutes during regular hours and re-measures exposures.
- Acts only when an exposure leaves its band.
- Hysteresis: re-hedge to the middle of the band, not its edge, to avoid churn.
- Minimum trade size.
- Every hedge is logged with its reason and shown on the Risk Desk.

---

## 9. Strategy roadmap to Sharpe ≥ 1

### The arithmetic

With *N* sleeves of similar Sharpe *s*, equal risk, and average pairwise
correlation *ρ*:

    SR_portfolio = s · √( N / (1 + (N − 1)·ρ) )

| Sleeves N | Sleeve Sharpe s | Correlation ρ | Portfolio Sharpe |
|---|---|---|---|
| 4 | 0.5 | 0.0 | 1.00 |
| 4 | 0.5 | 0.1 | 0.88 |
| 5 | 0.6 | 0.1 | 1.13 |
| 6 | 0.5 | 0.2 | 0.85 |

So the job is **several honest sleeves of about 0.5, kept uncorrelated**, not
one heroic model. Expect published factors to decay: McLean & Pontiff (2016)
find returns about 26% lower out of sample and about 58% lower after publication.

**Paper results can't confirm a Sharpe of 1.** The standard error of an
annualized Sharpe ratio is about √((1 + SR²/2) / T), with *T* in years.
- One year at SR = 1 has a standard error of about 1.2.
- Three years have about 0.7.

Paper trading validates **implementation**: tracking error vs. the model,
costs, and plumbing. Evidence about **edge** comes from point-in-time
backtests plus economic reasoning.

### Sleeves, in order

Every sleeve starts as a written hypothesis with an economic mechanism.

| # | Sleeve | Mechanism | Data | Instruments | Cadence |
|---|---|---|---|---|---|
| S0 | **Baseline** (live now, fixed in Phase 0) | Plumbing test, not alpha | IBKR bars | 22 ETFs | Monthly / drift |
| S1 | **Cross-asset trend + carry** | Under-reaction and hedging pressure; carry as compensation for risk (Moskowitz–Ooi–Pedersen 2012; Koijen et al. 2018; Hurst–Ooi–Pedersen 2017) | IBKR bars, futures curves | ETFs now → micro futures (equity indices, rates, FX, metals, energy, grains) | Daily, with a no-trade buffer |
| S2 | **US equity long/short, multi-factor** | Value, profitability (Novy-Marx 2013), 12-1 momentum, low-risk (Frazzini–Pedersen 2014), analyst revisions and post-earnings drift (Bernard–Thomas 1989), short interest. Beta- and sector-neutral via the CRB | CRSP, Compustat (point-in-time), IBES, FactSet, short interest | Top ~1,000 liquid, shortable US stocks | Monthly, **split into 4 weekly tranches** to reduce rebalance-timing luck |
| S3 | **Volatility** | (a) Vol-managed overlay on the whole book (Moreira–Muir 2017); (b) variance risk premium with defined risk: VIX futures roll in contango, short put spreads, strict size | Cboe (WRDS), VIX futures history; options data is a gap (Section 11) | VIX futures, SPX/SPY options | Daily / weekly |
| S4 | **Macro** | Growth/inflation regime tilts from **point-in-time** macro data (ALFRED vintages); cross-asset value and carry | FRED/ALFRED, Fed Board reports, Macro Finance Society | ETFs → futures | Weekly / monthly |
| S5 | **Event-driven** | Post-earnings drift, revision momentum; merger arbitrage as insurance against deal failure (Mitchell–Pulvino 2001) | IBES, EDGAR 8-K / S-4 / DEFM14A | Stocks | Event |
| S6 | **Prediction markets** | Same event priced differently on Kalshi and ForecastEx (net of fees); favourite–longshot bias (Snowberg–Wolfers 2010); hedging the book's macro-event risk | Kalshi API, IBKR ForecastEx | Event contracts | Event |
| S7 | **Intraday news momentum** (Phase 5) | Slow diffusion of firm-specific news | EDGAR real-time filings, IBKR news, News Engine output, minute bars | Stocks / ETFs | Minutes–hours |

**Your research ideas:**

- **"Long a bond, short the stock."** Mechanically possible: IBKR trades US
  corporate bonds and you can short the stock. That is capital-structure
  arbitrage. Evidence shows modest returns with large tails (Yu 2006). At
  retail size, bond spreads and illiquidity eat most of it, and you can't
  short bonds. Two better first steps:
  1. Use bond-market information **as an equity signal**. Bond momentum spills
     over into stocks (Gebhardt–Hvidkjaer–Swaminathan 2005); TRACE and WRDS
     Bond Returns cover this.
  2. Trade the liquid version: credit ETFs (HYG/LQD) vs. equity or sector
     ETFs, all shortable.
- **Other WRDS-powered ideas:**
  - Supplier/customer momentum (Cohen–Frazzini 2008), if FactSet supply-chain
    data is in your subscription.
  - Opportunistic insider buying (Cohen–Malloy–Pomorski 2012).
  - Audit red flags as short signals: restatements, auditor changes, control
    weaknesses (Audit Analytics).
  - Innovation efficiency from US Patents.

### Research gate (every sleeve, before paper)

1. A hypothesis and its mechanism, written down before any backtest.
2. Point-in-time data only: every fundamental row has an `available_at`.
3. Costs included: commissions, spread, impact (calibrated from your own
   fills), borrow, futures roll.
4. Walk-forward out-of-sample tests, plus a holdout that stays untouched until the end.
5. **Deflated Sharpe ratio** (Bailey & López de Prado 2014), accounting for how many variants were tried.
6. Turnover, capacity, and correlation to the sleeves already live.
7. Shadow mode, then at least 3 months of paper with tracking error inside a
   band. Kill criteria are fixed in advance.

### Allocation and leverage

There is no leverage: gross stays at or under 100% of NAV and cash at or
above 0 (Section 1).

- **One gross budget.** All sleeves share the same 100%. Risk is allocated
  across sleeves inside it (inverse vol, with correlation shrinkage), and a
  sleeve's share of gross is its budget.
- **An equity long/short sleeve at 50/50 uses all of it:** 50% long and 50%
  short is 100% gross. Next to other sleeves it runs smaller.
- **Vol targeting can only scale down.** The book targets 8–10% annualized.
  When its vol is over target, gross is cut. When it is under, the book stays
  at its gross limit and runs below target.
- **No leverage staircase.** The S0→S5 staircase in the April snapshot doc is
  removed; gross never steps above 100%.
- **Sharpe comes from diversification, not leverage.** Leverage scales return
  and drawdown together and leaves Sharpe where it was, so the mandate costs
  return, not Sharpe.

What this costs:

- **Lower absolute returns and volatility.** A diversified or market-neutral
  book at ≤ 100% gross runs at low vol. A 10% vol target may be out of reach
  for low-vol books, so the target is a ceiling, not a promise.
- **Futures count at full notional,** even though they need little cash. A
  futures sleeve can hold only as much notional as its share of the 100%
  allows, which limits trend and carry (S1) to fewer markets or smaller
  positions than a levered futures fund would run. Low-vol contracts such as
  short-rate and bond futures add little risk per dollar of gross.

---

## 10. Semi-systematic trading (later, not now)

Trades you make by hand fit the platform as one more sleeve: a
**discretionary sleeve**, with the same contract and the same checks as the
systematic ones.

- **Trade proposals with a written thesis.** Each trade starts as a proposal:
  the instrument, the direction, why, and what would prove it wrong.
- **Size from the risk model.** The proposal comes with a suggested size from
  the risk model and the sleeve's budget.
- **The same no-leverage pre-trade check** as every other order. A
  discretionary trade gets no exception.
- **Its own P&L attribution and kill criteria.** The sleeve keeps a virtual
  position and P&L like any other (Section 8), with kill criteria fixed in
  advance.
- **Overrides are logged.** Skipping or resizing a systematic sleeve's trade
  is recorded with its reason, so attribution shows what overrides cost or
  earned.

**Prerequisites:** the engine's OMS and CRB (Section 6), per-sleeve
attribution, and the event journal, so every proposal, decision and override
can be replayed.

**In the dashboard:** a trade ticket that previews the risk impact before
submitting: gross, net, cash, the largest name and beta, before and after, and
whether the no-leverage check passes.

**Not before Phase 4.** The systematic sleeves and the engine come first.

---

## 11. Data platform

### Storage

| Tier | What | Where |
|---|---|---|
| Research lake | Parquet (zstd), partitioned by dataset/date; DuckDB catalog; DuckDB SQL and pandas for transforms | Laptop SSD, synced to the VM, backed up to a **private** R2 bucket |
| Operations DB | Postgres: instruments, orders, fills, positions_eod, nav_daily, cash_flows, targets, runs, events, datasets | VM (local docker on the laptop for development) |
| Journal | Engine's append-only event log | VM disk, backed up nightly |

Rough size: CRSP daily plus Compustat plus IBES plus FactSet modules come to
tens of GB as Parquet. That fits comfortably on the laptop and on a 200 GB VM.

### Foundations

- **Security master.**
  - Links PERMNO (CRSP) ↔ GVKEY (Compustat, via CCM) ↔ IBES ticker ↔ FactSet
    entity ↔ CUSIP/ISIN ↔ IBKR `conId`, with date ranges.
  - Built from WRDS Linking Queries.
  - It is the most important table for equity long/short; get it right first.
- **Point-in-time discipline.**
  - The research API only answers "as of" queries.
  - Fundamentals become visible at their report date (Compustat `rdq`, IBES
    announcement dates), never at the period end.
- **Survivorship.** Historical universes come from CRSP, including delisting
  returns. The live universe comes from current listings.
- **Prices.**
  - Total-return series from CRSP `ret` for research.
  - IBKR `ADJUSTED_LAST` daily bars for live signals.
  - Raw `TRADES` bars kept for execution analysis.
  - Benchmarks are always total return.

### "Pull whatever data is missing"

- Every dataset is registered in a `datasets` table with:
  - its source
  - its expected update schedule (on an exchange calendar)
  - a watermark query
  - an idempotent backfill function
- `tradeit catchup` (also run by `tradeit up`):
  1. Compares each watermark with what *should* exist by now.
  2. Fills the gaps.
  3. Records what it did.
- The **Data** page shows the result.
- No Airflow: a small Python job runner is enough at this size.

### WRDS: what each subscription is for

| Use | Datasets |
|---|---|
| Universe, returns, benchmarks | CRSP (daily/monthly, indexes, delistings), Historical SPDJI |
| Fundamentals, value/quality | Compustat – Capital IQ (with short interest), Financial Ratios Suite, FactSet fundamentals |
| Earnings, revisions | LSEG IBES |
| Ownership, insiders, crowding | Thomson/Refinitiv (13F, insider filings), FactSet ownership, Blockholders, MFLINKS |
| Credit → equity signals | TRACE, WRDS Bond Returns, LSEG Mergent (bond descriptors) |
| Risk model, attribution | Fama-French factors, Factors by WRDS, Pastor-Stambaugh liquidity, Beta Suite |
| Red flags, governance | Audit Analytics, BoardEx, Federal Judicial Center (litigation) |
| Innovation | US Patents by WRDS |
| Transaction costs | Intraday Indicators by WRDS (spreads, price impact), SEC Order Execution |
| Macro | Federal Reserve Board Reports, Macro Finance Society, plus FRED/ALFRED (public) |
| Vol regime | Cboe (VIX family and strategy indexes) |
| Research tooling | Event Study by WRDS, Eventus, Linking Queries |
| Probably skip | European Short Data, MSRB, DMEF, IRI, NielsenIQ sample, OTC Markets, Bank Regulatory (unless trading banks), Subsidiary Data |

**Gaps (OptionMetrics and TAQ: not for now, revisit later):**

- **OptionMetrics**, needed for options research. Until then, S3 is limited to
  the vol-managed overlay and VIX-futures term-structure work.
- **TAQ**, needed for intraday research. Until then, use IBKR minute bars,
  within pacing limits.
- **RavenPack** or another news dataset, needed for S7.
- **Which FactSet modules** are included; supply chain matters for one of the ideas above.

**Free sources to add:**

- SEC EDGAR: full-text search, XBRL financial statements, real-time 8-K feed.
- FRED / ALFRED.
- CFTC Commitments of Traders.
- Ken French library.
- Cboe VIX futures settlement history.
- IBKR's free API news providers.

**Paid sources:**

- Budget is about $10/month, which goes to the IBKR Value Bundle above.
- Databento or a similar provider (minute bars, futures history) and a news
  feed for S7 wait until a sleeve proves it needs them.

### Practical WRDS notes

- **Licence.** WRDS data is licensed for academic research. Paper-trading
  research is fine, but:
  - keep raw data private (private bucket, Cloudflare Access on anything that shows it)
  - never redistribute it
  - confirm with the school before anything commercial
- **Decided (October 2026): manual only.** The subscription is limited and
  every session needs a Duo prompt, so nothing automates WRDS: no scheduled
  syncs, no unattended Postgres connections. Pull what research needs by hand
  while at the laptop; the daily run uses IBKR prices and free sources only.
  That's fine, because CRSP, Compustat and IBES update on daily to quarterly cycles anyway.
- **Data lag.** CRSP lags by months. It is for research and history, not live
  signals. Live signals use IBKR prices plus the fundamentals available as of that day.

### Backtesting without the data we don't have

You asked: if we don't have the data, how are we going to backtest? The answer
is sleeve by sleeve. Most sleeves have the history they need. The rest get
proxies, a forward archive and a longer shadow period.

**What each source gives:**

| Source | What it gives backtests |
|---|---|
| WRDS: CRSP | Daily stock and ETF returns, survivorship-free, with delisting returns |
| WRDS: Compustat | Fundamentals, point-in-time from the report date |
| WRDS: IBES | Analyst estimates, revisions and announcement dates |
| WRDS: Fama-French factors | Factor returns for the risk model and attribution |
| WRDS: Cboe | VIX family indexes |
| WRDS: TRACE | Corporate bond trades |
| IBKR historical bars | Stocks and ETFs: daily and intraday bars. Futures: only recent contracts or the continuous series. Pacing limits on all of it |
| FRED / ALFRED | Macro series, with ALFRED vintages of what was known on each date |
| Ken French library | Long histories of factor and portfolio returns |
| CFTC COT | Weekly futures positioning |
| Cboe | VIX futures settlement history |
| EDGAR | Filings with their filing times, and XBRL financials |

**Sleeve by sleeve:**

| Sleeve | History from | Gap | What we do |
|---|---|---|---|
| ETF and stock sleeves (S0, S2, S5) | CRSP, with Compustat and IBES | None that matters: full history, survivorship-free | Backtest on CRSP. Live signals use IBKR prices, since CRSP lags by months |
| Futures sleeves (S1, later S4) | ETF proxies for the long history; IBKR continuous futures for recent years | Expired contracts and full curves are limited, so carry history is short | Trend tested on ETF proxies, checked against continuous futures where they overlap. Carry tested only on the years with curves |
| Options and vol (S3) | Cboe VIX indexes, VIX futures settlements | No OptionMetrics, so no option prices | Only VIX-index and VIX-futures work: the vol-managed overlay and the VIX futures roll. Option strategies wait for options data |
| Intraday (S7) | IBKR minute bars | No TAQ, and pacing limits how much can be pulled | Minute bars pulled within pacing limits and cached in the lake |

**Start recording now.** What we can't buy we can still collect from today.
Daily futures curves (every listed contract), quotes and minute bars go into
the Parquet lake every day. This forward archive becomes the history that
carry and intraday sleeves are missing.

**Where history is thin, test robustness** rather than trust one backtest path:

- Block bootstrap: resample blocks of returns to see the spread of outcomes.
- Parameter perturbation: a sleeve that works at only one lookback was fitted,
  not found.
- Regime splits: results by vol regime, rate regime and period.

**One point-in-time lake.** Everything lands in Parquet with its
`available_at`. DuckDB answers "as of" queries, and its output feeds the C++
backtester (`dv02_engine.backtest`), so a backtest sees only what was known on
each day.

**The rule:** a sleeve with no usable history runs shadow-only forward tests
(signals and would-be orders logged, nothing sent) for a period set in advance,
and goes to paper only if it passes. The shadow record stands in for the
missing backtest; the rest of the research gate (Section 9) still applies.

---

## 12. Operating model: `tradeit up`

### Commands

```
tradeit up          start or attach; run doctor; catch up data; start API; open dashboard (--open)
tradeit status      one-screen health: gateway, engine, data freshness, last run, kill switch
tradeit doctor      IBKR end-to-end check (built in this PR)
tradeit catchup     fill missing data per dataset watermarks
tradeit rebalance   preview (default) or --submit, per sleeve or all
tradeit hedge       show CRB exposures and proposed hedges; --submit to execute
tradeit kill "why"  arm kill switch: cancel open orders, block new ones
tradeit resume      disarm, after a doctor + reconcile pass
tradeit logs        tail engine/API/run logs
tradeit backtest S  run a sleeve's backtest through the shared simulation core
tradeit down        stop local services (never cancels resting orders without --cancel)
```

- **Phase 0–1:** a Python CLI (Typer) over the current system.
- **Phase 3:** the same commands manage `tradeitd`.

### Where things run

- **Phase 1 (laptop).** `tradeit up` starts the gateway container, the API and
  the dashboard. The 3:40pm submission only happens if the laptop is awake.
  Missed runs are caught up the next time you open it, by trading at the next
  close rather than late.
- **Phase 2+ (Oracle VM, always on).**
  - The VM runs the gateway, engine, Postgres, NATS and API.
  - The laptop's `tradeit up` connects over Tailscale or a Cloudflare Tunnel,
    syncs the research lake, and opens the dashboard.
  - The Kalshi desk already lives on the VM and becomes an engine venue.

### Daily schedule (ET, daily-horizon sleeves)

| Time | Job |
|---|---|
| 06:00 | Catch-up: Flex T−1 statement (official fills/NAV), fundamentals, macro |
| 09:00 | Pre-open: doctor, reconcile T−1 positions vs. Flex, corporate actions, kill-switch state |
| 09:30–16:00 | Stream positions and P&L; auto-hedger every 5 min |
| 15:30 | Close-rebalanced sleeves finalize orders (signals from the t−1 close) |
| 15:40 | Submit MOC/LOC (exchange cutoff 15:50) |
| 16:15 | Daily bars into the lake |
| 16:30 | EOD reconcile (API positions vs. book); NAV snapshot |
| 17:00 | **Daily brief**: a one-page "edition" with P&L, attribution, risk, anomalies, tomorrow's events; pushed to your phone |

**Rebalance calendar:**

- S2 equity long/short trades its weekly tranche mid-week. It avoids month-end,
  the S&P quarterly rebalance (third Friday of Mar/Jun/Sep/Dec) and the Russell
  reconstitution in late June.
- S1 trend checks daily with a no-trade buffer.
- The hedger is continuous within bands.

**Monitoring:**

- A Cloudflare Worker cron checks the API health endpoint every 5 minutes
  during market hours and pushes an alert if it fails.
- The kill switch, reconcile mismatches and failed runs also push alerts.

**Security:**

- Secrets live only in `.env` / a password manager on the VM, never in git.
- **Move `mosek.lic` out of the repo** and load it via `MOSEKLM_LICENSE_FILE`.
  It also expires October 21.
- The gateway port is bound to localhost only.
- The dashboard is reachable remotely only through Cloudflare Access.

---

## 13. Prediction markets: Kalshi and IBKR ForecastEx

**Deferred.** Kalshi is out of scope for now. The notes below are kept for when it comes back.

- **Kalshi.**
  - The existing desk on the Oracle VM becomes an engine venue (`brokers/kalshi`).
  - Positions flow into the CRB as binary exposures, where maximum loss = premium paid.
  - Separate risk budget.
  - Shows up in a consolidated NAV next to IBKR.
  - Use Kalshi's demo environment for development.
- **ForecastEx via IBKR.**
  - In the TWS API, contracts are `secType="OPT"` on exchange `FORECASTX`;
    "Yes" is a call and "No" is a put.
  - **Limit orders only** (Day, GTC, IOC). Positions **can't be sold**: you exit
    by buying the opposite side.
  - **Whether paper accounts can trade them is unverified.** Check before building on it.
  - Reference: [IBKR TWS API: ForecastEx contract example](https://www.interactivebrokers.com/docs/tws-api/doc/event-trading/contract-definition-discovery/forecast-ex-contract-example).
- **Uses, in order:**
  1. Information: market-implied probabilities for CPI, FOMC and the like, fed
     into the macro sleeve.
  2. Event hedges for the main book.
  3. Cross-venue mispricing between Kalshi and ForecastEx on the same event, net of fees.

---

## 14. Phases and exit criteria

Sizes are rough, assuming one person working part-time. Phases 1–3 overlap.

| Phase | Scope | Exit criteria | Size |
|---|---|---|---|
| **0. Stabilize** | IBKR plumbing (this PR); MOC timing; adjusted prices; fix drift gate; schedule and fix EOD reconcile; wire `risk_limits.yaml` and kill switch into every submit path; holiday calendar; declare deps (pandas); MOSEK out of git with a free solver fallback (Clarabel/ECOS); backfill price data; **Flex history + NAV since inception**; delete dead code (GCS pipeline, simulations, 0-byte modules) *Done (Oct 13): MOC timing, the drift gate, the EOD reconcile as the run's first step, `risk_limits.yaml` and the kill switch on every submit path, the holiday calendar, MOSEK out, the backfill, Flex and PortfolioAnalyst history, trading-engine tests in CI. Left: adjusted prices, declared deps.* | 10 consecutive trading days with doctor green, the run executing, EOD reconcile matching the broker to the share, and NAV matching the IBKR statement | 2–3 weeks |
| **1. Frontend + API v1** (done) | Contract, design system, all 8 sections, control contract with Playwright, simulated broker for CI; delete old static dashboard. *Done: contract, design system, 6 sections, control contract, web CI, real-data API (`services/api`) and `bin/tradeit`, running on the paper account with NAV since inception from Flex, live streaming over a WebSocket, equity curve with a dollar view. Left: Strategies and Data, which wait on Phases 2 and 4, and deleting the old static dashboard.* | Every control in Appendix B is replaced or deliberately dropped; e2e green in CI | 4–6 weeks |
| **2. Data platform** | Lake + DuckDB, Postgres, security master, point-in-time layer, WRDS jobs, dataset watermarks + `catchup`, Oracle VM setup, tunnel + Access, R2 backups | S2's full history rebuildable from scratch with one command; Data page all green | 4–8 weeks (parallel) |
| **3. C++ engine** (started) | No-leverage risk gate → backtester → read-only mirror → shadow OMS → engine submits → CRB + auto-hedger → intraday host; nanobind bindings. *Done: risk gate, backtester port from Rust. Next: the backtester's cost model and walk-forward runs.* | Two weeks of shadow orders matching Python; one month engine-only with zero unexplained breaks | 2–4 months |
| **4. Sleeves** | S1, S2, S3a, S4, S5, S6 through the research gate, one at a time | Each passes the gate and 3 months of paper; book Sharpe tracked with confidence intervals | One sleeve per 4–8 weeks |
| **5. Intraday + news** | Minute data, news pipeline (EDGAR, IBKR news, News Engine), S7 inside the engine | Paper tracking error inside its band | After Phase 3 |
| **6. Futures and options (within the no-leverage mandate)** | Futures sleeves as broad as the 100% gross budget allows; options sleeves once options data exists and the risk gate measures delta-adjusted notional | Each sleeve passes the research gate and 3 months of paper, with the book inside the mandate throughout | After two sleeves are live |

CI, by phase:
- Phase 0: Python tests and lint for `trading-engine`. *(Added.)*
- Phase 1: frontend typecheck, lint and e2e (`web.yml`); API contract tests
  and lint (`api.yml`). *(Added.)*
- Phase 3: C++ build and tests with sanitizers (`engine.yml`). *(Added.)*
  Replay tests that run a recorded day through the engine and compare the
  result come with the engine's event loop.

---

## 15. Decisions

**Answered (October 10 and 12):**
- **Leverage:** none. Long/short with gross ≤ 100% of NAV (futures at
  notional) and cash ≥ 0, enforced in C++ on every order path (Section 1).
- **Languages:** C++ and Python only, TypeScript only for the dashboard UI.
  No Rust in our code.
- **Universe:** anything IBKR trades, within the mandate and the account's
  permissions.
- **Frontend:** React + TypeScript; built first.
- **Look and name:** the fund is "DV02 Fund"; the nameplate reads "DV02".
  The Journal (WSJ-style newspaper) theme is the default, with a dark theme
  one click away; Command Center layout. (Dark was the default until
  October 12. On October 16 the owner rejected a dark neon terminal look and
  had the Journal redesigned in the same style.)
- **Holdings outside the model (October 12):** sold at the next rebalance.
  The book becomes the model alone, under 100% gross with no margin loan.
- **TQQQ (October 12):** out of the model. QQQ was already in the
  universe, so the model holds unlevered Nasdaq-100 exposure through QQQ and
  places TQQQ's weight wherever its signals say.
- **Kalshi:** deferred.
- **OptionMetrics and TAQ:** not now.
- **Paid data:** about $10/month, which goes to the IBKR US Securities
  Snapshot and Futures Value Bundle.

**Still open:**
1. **Rust-built third-party tools.** Polars (in research code) and the ruff
   linter (in API CI) are written in Rust; our own code has none. Keep them,
   or replace them (pandas for Polars, a Python linter for ruff)?
2. **Oracle VM shape.** On the VM, `uname -m` (aarch64 = ARM Ampere), `nproc`,
   `free -h` and `df -h /` give everything needed. Is it OK to run the IBKR
   gateway and Postgres there?
3. **IBKR permissions.** Re-request futures and options after updating the
   profile (Section 7). Confirm the account is a margin account (needed for shorts).
4. **WRDS:** ~~which FactSet modules are active. Do unattended connections pass Duo?~~
   Decided: manual only (Section 11); nothing in production depends on it.
5. **Collaborators:** Rithwik (original engine), the News Engine project with
   Prof. Singh. One monorepo, or separate repos with shared `schemas/`?
6. **Benchmark default** for the Performance page: S&P 500 total return (current default)?

---

## Appendix A: known issues in the current system

**Fixed in this PR:** port and client-ID sprawl; paper autodetect falling back
to live ports; reconcile reporting clean on connection failure; "Run audit"
cancelling the scheduled run; hardcoded `/Users/...` data paths; contracts that
failed to qualify returning `None` instead of raising; `.env` never being loaded;
the exposure gate ignoring holdings outside the model (the C++ no-leverage
check now counts every holding, on both the dashboard and the daily-runner
order paths).

**Execution and risk (Phase 0):**
- Fixed: orders were market orders sent after the close, so they filled at the next
  open. The daily run now starts at 15:35 ET on targets from the last completed close
  and sends market-on-close orders until 15:45 ET (12:45 on an early close), and
  market-on-open orders for the next session after that (`src/broker/ibkr/auction.py`).
  The MOC setting in `broker.yaml` / `execution.yaml` is still not read: the clock decides.
- ~~The kill switch is checked only in the daily-runner path. Dashboard and CLI
  submits bypass it.~~ Fixed: `submit_paper_orders()`, the one function that
  places orders, runs the kill switch and the no-leverage check itself before it
  connects; the unguarded Streamlit submit page is deleted.
- ~~Three sets of limits disagree: `exposure_limits` defaults, `risk_limits.yaml`
  (unread), and `order_policy`.~~ The gate's limits now come from
  `risk_limits.yaml` (every key set, none looser than the mandate);
  `order_policy` is still its own thing. ~~Stale data only produces a warning.~~
  Fixed: a model ETF more than a session behind the last completed close blocks the run.
- ~~The drift gate reads the previous run's reconciliation file, not today's targets.~~
  Fixed: it compares today's targets with the account's positions now.
- ~~The EOD reconcile is not scheduled. Its "book" is a sum of order deltas
  rather than positions, so it would false-alarm.~~ Fixed: the reconcile is the
  daily run's first step, on an expected book of positions plus the orders sent
  since, at their fills; a break of $100 or more arms the kill switch.
- ~~No holiday calendar in the runner (it skips weekends only).~~ Fixed: a real run
  skips NYSE holidays and weekends (`src/common/clock.py`), and says so in its run log.
- Production trades all 22 ETFs in the price master, not the 13-name universe
  in `paper.yaml`. The live optimizer is chosen by env var (`momentum_tilted`
  in the LaunchAgent, `ensemble_default` otherwise); `paper.yaml`'s
  AMMA/MVO/MOSEK setup only drives a broken pipeline.
- ~~Schedule times disagree across `app.yaml` (14:50 CT), the LaunchAgent (16:32
  local) and `schedule.py` (16:30 ET).~~ Fixed: one schedule, `bin/tradeit schedule`
  at 15:35 ET; the old `production/scheduler.py` and `runtime/schedule.py` are deleted.

**Data (Phase 0 / 2):**
- No dividend or split adjustment; bond-ETF momentum and total returns are wrong.
- The price master in git ends on 2026-05-06 (`tradeit refresh` refills it locally).
- Futures, indices and rates masters are referenced but absent.
- `src/storage/` is gitignored, so the storage package was never committed and its tests fail.
- ~~`db/migrations` table names don't match `db.yaml`.~~ Fixed: `db.yaml` lists the
  migrations' tables (a test keeps them in step), `006_nav.sql` adds daily NAV, and
  credentials come from the VM's `.env` (`DV02_DB_USER`, `DV02_DB_PASSWORD`).

**Code health (Phase 0):**
- `pandas` and `apscheduler` are used but not declared in `pyproject.toml`.
- `mosek.lic` (personal academic licence) is out of the tree; old commits keep it
  until it expires 2026-10-21. Every cvxpy solve takes its solver from
  `trading_engine/solvers.py`: MOSEK only with a usable license, else CLARABEL.
- Dead or broken paths:
  - `production/pipeline/*` and `production/simulations/*` import missing `common.*` modules and write to GCS
  - `cli/backfill.py` raises a TypeError
  - `src/main.py` is empty
  - roughly 20 zero-byte modules
- Two strategy hierarchies: `strategies/etf` (pandas, live) and
  `trading_engine/models` (polars, research). The name AMMA means different
  algorithms in each.
- The daily runner imports the dashboard package to submit orders.
- `tests/production/test_ibkr_ports.py` connects to the gateway at import time.
- 19 tests fail and 1 errors because of the missing storage package, missing
  GitHub workflow files, and missing `common.async_gcs_writer`.

## Appendix B: current dashboard controls (acceptance list for Phase 1)

| Control | Today | In the rewrite |
|---|---|---|
| Paper/Live toggle | Works, but server-global, and Live mostly shows `paper_*` files | Per-session profile; Live disabled until live trading exists |
| Refresh ETF data | Worked only on the original Mac (paths fixed in this PR) | Operations → Run catch-up |
| Clear cache | Works | Dropped (server invalidates on writes) |
| Optimizer select | **No visible effect** | Dropped; sleeve config lives in Strategies |
| Run audit (force rebalance) | Mutates production files; cancelled the scheduled run (fixed in this PR) | Trading → Rebalance preview (read-only), then Submit |
| Auto-refresh | Resets page controls on every tick | Replaced by WebSocket live updates |
| Portfolio: dates, S&P, drawdown, log, hypothetical history | Work, but the hypothetical-history toggle is broken and "log base 2" is base 10 | Performance page with benchmark switcher |
| Model: engine backtests | Portfolio-level files are never produced; per-ETF works once generated | Strategies → backtest vs. live |
| Model: ETF prices | Works; unchecking all symbols shows the defaults | Kept |
| Model: Quant Research tab | **Dead (no backend)** | Dropped until a research run store exists |
| Asset: refresh from IBKR | Works when the gateway is up; doesn't clear cache | Live via engine mirror |
| Asset: attribution controls | Duplicate re-renders; no data producer | Strategies → attribution |
| Asset: orders calendar | Duplicate re-renders; paper-only regex | Trading → blotter with calendar |
| Slippage: dates, mode, sort | Works, but no report producer exists | Trading → TCA from fills |
| System Audit: re-run checks | Works; macOS-specific | Operations → doctor + health |
