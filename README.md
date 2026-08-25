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

## How it works

- `src/scanner.py` — polls Robinhood's top up-movers, filters to symbols
  matching your price range, minimum intraday gain, and relative volume
  (today's volume vs. average) from `config.yaml`.
- `src/momentum_tracker.py` — keeps a short rolling history of price/volume
  per symbol across scan cycles. A name only qualifies if it shows real
  movement in the last few cycles (`momentum_lookback_cycles`), not just a
  big cumulative gain from earlier in the day — this is the "still fresh
  demand vs. already stalled" check, and it's what lets an already-huge
  mover still qualify, or a second wave re-ignite a name the bot already
  exited once today.
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

cp .env.example .env        # fill in your Robinhood credentials
cp config.example.yaml config.yaml   # review EVERY value, see below
```

`config.yaml` and `.env` are gitignored — they hold your credentials and
personal risk settings and should never be committed.

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
