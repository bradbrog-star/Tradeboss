# Tradeboss

A self-hosted, autonomous momentum-runner bot for Robinhood equities.

**Strategy:** low-priced ("penny") stocks making a large, fast intraday move
on above-average volume — social-chatter/momentum driven, not fundamentals.
These moves are frequently pump-driven and short-lived; the bot is built
around that reality (software-managed exits, force-flatten before close,
handling of trading halts) rather than pretending it's investing.

**This trades real money with no human approval step once live.** Read this
whole file before running it.

## How it works

- `src/scanner.py` — polls Robinhood's top up-movers, filters to symbols
  matching your price range, minimum intraday gain, and relative volume
  (today's volume vs. average) from `config.yaml`.
- `src/risk_manager.py` — the guardrails: max position size, max total
  capital deployed, max concurrent positions, max trades/day, and a daily
  loss **kill switch** (halts new entries once today's realized + open P&L
  hits your configured loss limit — existing positions still get managed
  and exited normally). State persists in `state/day_state.json` and
  resets automatically each new trading day.
- `src/executor.py` — places orders. Entries are limit orders just above
  the ask; exits are market orders. Exits are **software-monitored**, not
  resting stop orders — a resting stop does nothing on a halted stock, so
  the bot checks price every cycle and fires the sell itself.
- `src/main.py` — the loop: while the market is open, refresh the kill
  switch, manage/exit open positions, and (only inside your configured
  entry window) look for new candidates to enter.

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
4. Review `stop_loss_pct` / `take_profit_pct` / `force_exit_time_et` — the
   exit rules. Remember the stop-loss is monitored, not guaranteed: if a
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
