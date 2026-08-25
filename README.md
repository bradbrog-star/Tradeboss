# Tradeboss

A self-hosted, autonomous momentum-runner bot for Robinhood equities,
built around the "Mr. Michael" strategy: low-priced (roughly $0.30-$3,
sometimes up to $5), tiny-market-cap, low-float US stocks suddenly firing
up on abnormal volume — a real catalyst, a weak one, social-media hype,
short covering, or no clear reason at all. Same-day only, no overnight
holds.

The core idea the bot is built around: a stock isn't automatically too
late because it's already up 40%, 100%, or 200% — what matters is whether
fresh demand is still arriving. So entries require not just a big day
gain but recent (last few cycles) price/volume continuation, and exits
are based on giving back gains from the peak reached in the position (or
buying pace visibly drying up), not a fixed target — so a real leader can
keep running, and a stock that reignites after a pullback can be
re-entered later the same day.

**This trades real money with no human approval step once live.** Read this
whole file before running it.

A human doing this manually would check more than one place — Robinhood,
Webull, social chatter — before acting. This isn't institutional-grade
infrastructure (no colocated execution, no proprietary data feeds, no
research desk behind it), but it's the honest, buildable version of that:
more than one source for the candidate universe, a real chatter signal,
and a weighted composite score instead of a single threshold deciding
what to trade.

## How it works

- `src/scanner.py` — builds the candidate universe from Robinhood's top
  up-movers (plus Webull's, if `enable_webull_source` is on), filters to
  symbols matching your price range, minimum intraday gain, and relative
  volume (today's volume vs. average) from `config.yaml`.
- `src/social_signal.py` — a real social-chatter signal from StockTwits'
  public API (message volume, trending status) — the actual place this
  kind of chatter lives, not a generic web search. It's a scoring input,
  not a hard filter: the strategy explicitly allows for moves with no
  visible catalyst at all.
- `src/webull_source.py` — optional secondary universe via the unofficial
  `webull` package. Off by default; it's a reverse-engineered API that
  drifts between versions, so verify it against current docs before
  relying on it (see the file for details).
- `src/float_data.py` / `src/float_metrics.py` — real float data. Neither
  Robinhood nor Webull reliably expose float, so this hits a real
  financial-data API instead (Financial Modeling Prep's dedicated
  `shares_float` endpoint by default, sourced from SEC filings — free
  tier 250 req/day; Alpha Vantage's `OVERVIEW.SharesFloat` as an
  alternative or cross-check). Requires `FMP_API_KEY` and/or
  `ALPHA_VANTAGE_API_KEY` in `.env`. Because float rarely changes, it's
  **cached per symbol** (`float_cache_ttl_hours`) instead of re-fetched
  every scan cycle — this is what makes even the free tiers workable.
  Every float record carries an honest `stale`/`stale_reasons` verdict
  (missing figure, no as-of date, figure older than
  `float_staleness_days_threshold`, or the two providers disagreeing if
  `float_cross_check` is on) — a stale or unverifiable float is never
  silently treated as exact, and by default doesn't disqualify a
  candidate, it just doesn't get the turnover score boost (set
  `require_valid_float: true` to make it a hard gate instead).
  On top of the raw figure, `float_metrics.py` computes the actual
  effective-float signals: `day_turnover` / `rotations_since_open`
  (cumulative volume ÷ float), `recent_turnover_rate` (volume ÷ float
  over just the last few cycles — the *current* pace, not the whole
  day), and `float_adjusted_relative_volume`. `dollar_volume_over_float_value`
  is also computed but documented as an approximation (it collapses to
  the same ratio as `day_turnover` without a true VWAP-weighted dollar
  volume feed, which isn't wired in here).
- `src/level2.py` — Robinhood Gold's Nasdaq Level II order book
  (`get_pricebook_by_symbol`, needs an active Gold subscription, $5/mo,
  reuses the same login). **Nasdaq-listed symbols only** — NYSE and
  OTC/pink-sheet tickers just won't have this, which is expected, and
  it's a polled snapshot each cycle, not a streaming tape (see "What this
  isn't" below for what a real tape provider adds). Used two ways: a hard
  gate (`max_spread_pct`) that skips a candidate if its top-of-book
  spread is too wide for the limit-order entry to make sense, and a
  scoring input (`weight_l2_imbalance`) from bid/ask size imbalance.
- `src/technical_indicators.py` — RSI, computed **locally** from
  Robinhood's own intraday candles (`get_stock_historicals`, 5-minute
  bars including extended hours) — no separate vendor. A standard
  14-period Wilder's-smoothed RSI. This is a momentum-**confirmation**
  input (higher RSI nudges the score up via `weight_rsi`), not an
  "overbought, avoid" filter — consistent with the strategy's own framing
  that a stock isn't automatically too late just because it already ran.
- **Market cap**, computed free from price × `shares_outstanding` (same
  float-provider lookup, no extra call). Mostly already implied by the
  price/float bounds, so it's exposed for logging and an optional hard
  ceiling (`max_market_cap_usd`) rather than folded into the score, which
  would just double-count size against float.
- Every qualifying candidate gets a weighted **composite score**
  (`weight_gain` / `weight_relvol` / `weight_continuation` / `weight_buzz` /
  `weight_float_turnover` / `weight_l2_imbalance` / `weight_rsi` in
  `config.yaml`) combining day gain, relative volume, recent
  continuation, chatter, float turnover, book imbalance, and RSI —
  candidates are ranked and entered by this score, not raw gain alone.
- `src/momentum_tracker.py` — keeps a short rolling history of price/volume
  per symbol across scan cycles. A name only qualifies if it shows real
  movement in the last few cycles (`momentum_lookback_cycles`), not just a
  big cumulative gain from earlier in the day — this is the "still fresh
  demand vs. already stalled" check, and it's what lets an already-huge
  mover still qualify, or a second wave re-ignite a name the bot already
  exited once today.
- `src/database.py` — the runner/failure research database
  (`state/tradeboss.db`, plain sqlite3, open it with any SQL tool or
  `pandas.read_sql`). Three tables: `candidate_snapshots` logs **every**
  candidate the scanner ever flags, whether traded or not (an `entered`
  flag distinguishes them) - the "runner" side; `halt_sightings` logs
  every time a symbol was seen halted, including a held position that
  suddenly has no quote - the clearest "failure mode" a resting stop
  can't handle; `trades` logs every completed trade's full outcome -
  entry/exit price and time, exit reason and a normalized
  `exit_category` (stop_loss/trailing/distribution/take_profit_cap/
  force_exit_time), P&L, and MFE/MAE (how far it ran for you and against
  you while held) - plus the candidate's score/float/rotations/buzz at
  entry, so later analysis can ask what actually distinguished winners
  from losers without rejoining other tables. This is what a replay
  harness or a paper-trading sample review would query against - the
  text log in `logs/` is for watching the bot live, this is for research
  after the fact.

  The database also holds four **reference** tables imported from
  `research/MR_Michael_Historical_Runners_Failures_Controls.xlsx` (a
  hand-researched, cited event study - see the workbook's own README
  sheet): `historical_events` (real runner/failed-runner/regulatory-
  failure cases, each cited to an actual SEC/Nasdaq filing),
  `historical_matched_controls` (provisional control pairings for
  comparison), `historical_match_rules` (the matching spec - calipers,
  weights, and a leakage rule per variable), and `historical_sources`
  (the citation registry). These are read-only reference data, never
  written to by the live bot - re-import after editing the workbook with
  `python scripts/import_historical_seed.py` (idempotent, requires
  `openpyxl`). The workbook's own discipline matters here: its
  `Feature_Snapshots`/`Outcomes` sheets are schemas only (no point-in-time
  data ingested yet), and its `Match_Rules` sheet explicitly forbids
  matching on outcome variables - any future code that joins against
  these tables must respect that same no-lookahead rule.
- `src/risk_manager.py` — the guardrails: max position size, max total
  capital deployed, max concurrent positions, max trades/day, and a daily
  loss **kill switch** (halts new entries once today's realized + open P&L
  hits your configured loss limit — existing positions still get managed
  and exited normally). Also tracks, per open position, the peak price and
  peak buying-pace reached since entry, which drive the exit logic below.
  State persists in `state/day_state.json` and resets automatically each
  new trading day.
- `src/executor.py` — places orders. Entries are limit orders just above
  the ask; exits are market orders. Exits are **software-monitored**, not
  resting stop orders — a resting stop does nothing on a halted stock, so
  the bot checks price every cycle and fires the sell itself.
- `src/main.py` — the loop: while the market is open, refresh the kill
  switch, manage/exit open positions, and (only inside your configured
  entry window) look for new candidates to enter. Exit priority per
  position, each cycle:
  1. **Stop loss** — hard floor, always wins if breached.
  2. **Take-profit cap** — optional outer ceiling (set high to effectively
     disable and let the trailing exit do the work).
  3. **Trailing exit** — sell once price has given back `trailing_giveback_pct`
     off its peak since entry, while still net positive.
  4. **Distribution exit** — sell if buying pace (volume/sec) has dropped
     `distribution_volume_drop_pct` off its own peak since entry while price
     has stalled — the crowd pushing it up has stopped showing up.
  5. **Force exit time** — flatten regardless, same-day only.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # fill in your Robinhood credentials + float API key
cp config.example.yaml config.yaml   # review EVERY value, see below
```

`config.yaml` and `.env` are gitignored — they hold your credentials and
personal risk settings and should never be committed.

Get a free `FMP_API_KEY` at financialmodelingprep.com (or `ALPHA_VANTAGE_API_KEY`
at alphavantage.co) for float data — see "Effective float" above. The bot
runs without one, it just can't compute float metrics or filter on float
size.

Level II (`src/level2.py`) needs an active Robinhood Gold subscription
($5/mo) on the account you log in with — no separate API key, it uses the
same Robinhood login. Without Gold, `get_pricebook_by_symbol` just returns
nothing and the bot proceeds without the spread gate/imbalance score.

### MFA

If your Robinhood account uses an authenticator app, set
`ROBINHOOD_TOTP_SECRET` in `.env` (the base32 secret shown when you set up
the authenticator). If you instead approve logins via push notification,
leave it blank — the first run will prompt you on your phone.

## Before you flip `dry_run: false`

`config.yaml` ships with conservative placeholder numbers, not a
recommendation. At minimum, before going live:

1. Set `account_equity_usd` to your real account balance.
2. Set `max_position_size_usd`, `max_total_deployed_usd`, and
   `max_concurrent_positions` to amounts you are OK losing entirely —
   these are momentum penny stocks; a position going to near-zero is a
   real, not hypothetical, outcome.
3. Set `daily_loss_limit_pct` — this is your actual kill switch. Once hit,
   the bot stops opening new positions for the rest of the day.
4. Review `stop_loss_pct` / `trailing_giveback_pct` / `take_profit_cap_pct` /
   `distribution_volume_drop_pct` / `force_exit_time_et` — the exit rules.
   Remember the stop-loss is monitored, not guaranteed: if a
   stock gaps down through your stop or halts while you're in it, the
   actual exit price can be materially worse than `entry_price *
   (1 - stop_loss_pct)`.
5. Run at least one full session with `dry_run: true` and read the logs in
   `logs/tradeboss.log` before trusting it with real orders.

## Running

```bash
python -m src.main
```

Keep it running during market hours (a `screen`/`tmux` session, or a
systemd/launchd service, or similar — it's a long-running process, not a
one-shot script). It logs every decision to stdout and to
`logs/tradeboss.log`.

## Open research questions (not implemented - deliberately)

Two claims that keep coming up but aren't backed by data yet, so they're
NOT hard-coded into the bot. This is exactly what the runner/failure
database (`state/tradeboss.db`) exists to answer once a real sample
accumulates - see "runner/failure research database" above.

- **"Best time of day is ~5am-9:45am ET."** Unproven, and only partly
  actionable as stated: **Robinhood's own premarket window starts at
  7:00 AM ET** (with Gold) for actual order execution - not 5am, no
  matter what the data shows before then. Data from ~4am is more of a
  Webull thing (`webull_source.py` already has an optional feed for
  that). Also note: outside regular hours, Robinhood only accepts limit
  orders - `executor.py`'s exits currently use market orders
  (`order_sell_market`), which would simply get rejected in extended
  hours. Enabling real premarket trading needs that fixed first, not
  just widening `entry_window_start_et`.
- **"Hold overnight if the data/news proves there's more to the run."**
  This directly reverses the hard same-day/`force_exit_time_et` rule the
  strategy is built around. Not implemented on a hunch - if you want
  this, it should come from the database showing overnight gaps
  net-favorable often enough to justify the added risk, not be added
  speculatively.

## Known limitations (read before relying on this)

- **Entry price is approximate.** `risk_manager` records the *limit price*
  submitted, not a confirmed fill price. If you need exact fills, extend
  `executor.py` to poll `robin_stocks.orders.get_stock_order_info` after
  submitting and reconcile.
- **No fractional shares.** Position size is floored to whole shares, so
  small `max_position_size_usd` values on names near your `price_max_usd`
  may buy 0 shares and skip the trade.
- **Robinhood's top-movers list is a small curated set** (~20 names), not
  a full market scan. It won't catch every low-float runner. Wiring in a
  broader scanner (e.g. Webull's gainers list, or a paid screener API) is
  a reasonable next step and slots into `scanner.py`.
- **The unofficial `robin_stocks` API can break** if Robinhood changes
  their internal API. Watch the logs for repeated auth or order failures.
- **Nothing here assesses whether a specific move is a pump-and-dump you
  don't want exposure to** — the filters are purely price/volume. That
  judgment call is on you per trade.
- **No real tape.** `src/level2.py` gives a polled Nasdaq-only order-book
  snapshot each scan cycle, not continuous trade-by-trade time-and-sales.
  A real replay/slippage-measurement pass (phase 4 of the research
  roadmap) needs an actual streaming feed with historical tick data and
  broader (non-Nasdaq) coverage — evaluated options: Alpaca's Algo Trader
  Plus ($99/mo flat, full SIP trades/quotes + LULD halt messages) or
  Databento (pay-as-you-go, true L2 order-book depth, more integration
  work). Neither is wired in; this is a deliberate cost decision left to
  you, not an oversight.
