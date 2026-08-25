import math

import robin_stocks.robinhood as r


def enter_position(symbol: str, ask_price: float, dollar_amount: float, dry_run: bool, log):
    """Buy up to `dollar_amount` worth of `symbol` via a limit order priced
    just above the current ask (small buffer to improve fill odds on a
    fast-moving name). Returns {'symbol', 'qty', 'price'} on success, else
    None. NOTE: `price` is the limit price, used as an approximate entry
    for risk tracking - it is not a confirmed fill price."""
    qty = math.floor(dollar_amount / ask_price)
    if qty < 1:
        log.info(
            "%s: ask $%.2f too high for allocated $%.2f, skipping",
            symbol,
            ask_price,
            dollar_amount,
        )
        return None

    limit_price = round(ask_price * 1.005, 2)

    if dry_run:
        log.info(
            "[DRY RUN] Would BUY %d %s @ limit $%.2f (~$%.2f)",
            qty,
            symbol,
            limit_price,
            qty * limit_price,
        )
        return {"symbol": symbol, "qty": qty, "price": limit_price}

    order = r.orders.order_buy_limit(symbol, qty, limit_price, timeInForce="gfd")
    log.info("BUY submitted for %s: %s", symbol, order)
    if order and order.get("id") and not order.get("reject_reason"):
        return {"symbol": symbol, "qty": qty, "price": limit_price}
    log.warning("BUY order for %s may have failed: %s", symbol, order)
    return None


def exit_position(symbol: str, qty: float, dry_run: bool, log, reason: str = "") -> bool:
    """Exit a position immediately at market. Exits are software-monitored
    (see main.py) rather than resting stop orders, because resting stops
    don't help on a halted, illiquid runner - so when we decide to exit we
    want the order in the book now, not sitting as a trigger that may
    never fire cleanly."""
    if dry_run:
        log.info("[DRY RUN] Would SELL %s x%s (%s)", symbol, qty, reason)
        return True

    order = r.orders.order_sell_market(symbol, qty, timeInForce="gfd")
    log.info("SELL submitted for %s (%s): %s", symbol, reason, order)
    return bool(order and order.get("id"))
