"""Paper-trading fill and PnL math for xScanner.

Simulation only. This module never talks to a chain, a wallet, or a market-data
API. Callers pass the price. Nothing here can move real SOL.

Money is play SOL. Token prices are USD per token. ``sol_usd`` is USD per 1 SOL.

A market buy spends exactly the SOL the user typed. Out of that amount, in order:

1. a flat priority fee (default 0.001 SOL)
2. a platform fee on what remains (default 1% = 100 bps)
3. the rest buys tokens at the live price worsened by slippage
   (default 1.5% = 150 bps), so the fill price is ``px * (1 + slippage)``

A market sell sells a share of the tokens that are not reserved by a limit
sell. The fill price is ``px * (1 - slippage)``. The same priority fee and
platform fee come out of the proceeds. Realized PnL is net SOL received minus
the share of the position's all-in cost (fees included) those tokens carried.

Limit orders are triggers, not an order-book match:

* a buy limit reserves the spend up front and fills as a market buy once the
  live price is at or below the limit
* a sell limit reserves the tokens and fills as a market sell once the live
  price is at or above the limit

The execution delay is not applied here. The browser waits, then submits the
price it is showing after the wait. ``waited_ms`` is only recorded on the trade.
"""

from __future__ import annotations

import copy
import math
import time
import uuid
from datetime import datetime, timedelta, timezone

MYT = timezone(timedelta(hours=8))
MINT_RE_SRC = r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"

MAX_DEPOSIT = 10_000.0
MAX_CASH = 1_000_000.0
MAX_SPEND = 10_000.0
MAX_POSITIONS = 40
MAX_ORDERS = 30
MAX_TRADES = 400
MAX_SEEN = 80

DEFAULT_SETTINGS = {
    "slippage_bps": 150,
    "priority_fee_sol": 0.001,
    "bribe_sol": 0.0,
    "platform_fee_bps": 100,
    "exec_delay_ms": 700,
}
FAUCET_SOL = 1.0
_BOOK_KEYS = ("cash_sol", "reserved_sol", "realized_sol", "deposited_sol", "wins", "losses", "scratches",
              "positions", "orders", "trades", "daily", "faucet_day")


class Reject(Exception):
    """The action is well-formed but the simulator will not do it."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class Replay(Exception):
    """This client id was already applied. ``account`` is unchanged."""

    def __init__(self, account):
        super().__init__("replay")
        self.account = account


def _finite(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _q(v):
    """Shorten a float so JSON stays stable without changing the value much."""
    x = _finite(v)
    if x is None:
        return 0.0
    return float(f"{x:.12g}")


def _mint_ok(m) -> bool:
    import re
    return isinstance(m, str) and re.fullmatch(MINT_RE_SRC, m) is not None


def _text(v, n):
    if not isinstance(v, str):
        return ""
    return v.strip()[:n]


def new_account(now=None) -> dict:
    ts = int(now if now is not None else time.time())
    return {
        "v": 1,
        "cash_sol": 0.0,
        "reserved_sol": 0.0,
        "realized_sol": 0.0,
        "deposited_sol": 0.0,
        "wins": 0,
        "losses": 0,
        "scratches": 0,
        "positions": {},
        "orders": [],
        "trades": [],
        "daily": {},
        "settings": dict(DEFAULT_SETTINGS),
        "mode": "practice",
        "faucet_day": "",
        "alt": None,
        "seen": [],
        "resets": 0,
        "created_at": ts,
        "updated_at": ts,
    }


def clamp_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    slip = _finite(raw.get("slippage_bps"))
    pri = _finite(raw.get("priority_fee_sol"))
    bribe = _finite(raw.get("bribe_sol"))
    plat = _finite(raw.get("platform_fee_bps"))
    delay = _finite(raw.get("exec_delay_ms"))
    active = raw.get("active_p") if raw.get("active_p") in ("P1", "P2", "P3") else "P1"
    def pack(src, dslip, dss, dpri, dtip):
        src = src if isinstance(src, dict) else {}
        def I(k, d, hi=5000):
            n = _finite(src.get(k))
            return int(_clamp(n if n is not None else d, 0, hi))
        def F(k, d):
            n = _finite(src.get(k))
            return _q(_clamp(n if n is not None else d, 0, 0.5))
        return {"slippage_bps": I("slippage_bps", dslip), "sell_slippage_bps": I("sell_slippage_bps", dss),
                "priority_fee_sol": F("priority_fee_sol", dpri), "bribe_sol": F("bribe_sol", dtip)}
    srcb = raw.get("bundles") if isinstance(raw.get("bundles"), dict) else {}
    bundles = {
        "P1": pack(srcb.get("P1"), 100, 150, 0.001, 0.0),
        "P2": pack(srcb.get("P2"), 300, 400, 0.003, 0.001),
        "P3": pack(srcb.get("P3"), 1000, 1500, 0.01, 0.01),
    }
    def nums(key, default, lo, hi, as_int=False):
        rawv = raw.get(key)
        if not isinstance(rawv, list) or not rawv:
            return list(default)
        out = []
        for v in rawv[:6]:
            n = _finite(v)
            if n is None or n <= 0:
                continue
            n = _clamp(n, lo, hi)
            out.append(int(round(n)) if as_int else _q(n))
        return out or list(default)
    return {
        "slippage_bps": int(_clamp(slip if slip is not None else DEFAULT_SETTINGS["slippage_bps"], 0, 5000)),
        "priority_fee_sol": _q(_clamp(pri if pri is not None else DEFAULT_SETTINGS["priority_fee_sol"], 0, 0.5)),
        "bribe_sol": _q(_clamp(bribe if bribe is not None else DEFAULT_SETTINGS["bribe_sol"], 0, 0.5)),
        "platform_fee_bps": int(_clamp(plat if plat is not None else DEFAULT_SETTINGS["platform_fee_bps"], 0, 1000)),
        "exec_delay_ms": int(_clamp(delay if delay is not None else DEFAULT_SETTINGS["exec_delay_ms"], 0, 5000)),
        "active_p": active,
        "bundles": bundles,
        "buy_amounts": nums("buy_amounts", [0.1, 0.25, 0.5, 1], 0.001, MAX_SPEND),
        "sell_pcts": nums("sell_pcts", [10, 25, 50, 100], 1, 100, True),
    }


def _blank_pos(mint, symbol, name, pool, now):
    return {
        "mint": mint,
        "symbol": _text(symbol, 16) or "?",
        "name": _text(name, 64),
        "pool": _text(pool, 64),
        "qty": 0.0,
        "reserved_qty": 0.0,
        "cost_sol": 0.0,
        "avg_px_usd": 0.0,
        "opened_at": int(now),
        "updated_at": int(now),
    }


def normalize(raw) -> dict:
    """Rebuild a client-supplied book so a bad payload cannot crash the engine.

    Anonymous books live in the browser, so the numbers are the user's own play
    money. Types are coerced, sizes are capped, and reserved SOL / tokens are
    recomputed from the orders so a hand-edited book cannot spend the same
    paper SOL twice in one action.
    """
    if not isinstance(raw, dict) or raw.get("v") != 1:
        base = new_account()
        if isinstance(raw, dict) and isinstance(raw.get("settings"), dict):
            base["settings"] = clamp_settings(raw["settings"])
        return base
    created = _finite(raw.get("created_at"))
    a = new_account(int(created) if created and created > 0 else None)
    a["settings"] = clamp_settings(raw.get("settings"))
    a["mode"] = "hard" if raw.get("mode") == "hard" else "practice"
    day = raw.get("faucet_day")
    a["faucet_day"] = day if isinstance(day, str) and len(day) == 10 else ""
    alt = raw.get("alt") if isinstance(raw.get("alt"), dict) else None
    if alt and not raw.get("_nested"):
        inner = dict(alt)
        inner["v"] = 1
        inner.pop("alt", None)
        inner["_nested"] = True
        inner["settings"] = raw.get("settings") or {}
        parked = normalize(inner)
        a["alt"] = {k: parked.get(k) for k in _BOOK_KEYS}
    else:
        a["alt"] = None
    a["cash_sol"] = _q(_clamp(_finite(raw.get("cash_sol")) or 0.0, 0.0, MAX_CASH))
    a["realized_sol"] = _q(_finite(raw.get("realized_sol")) or 0.0)
    a["deposited_sol"] = _q(_clamp(_finite(raw.get("deposited_sol")) or 0.0, 0.0, MAX_CASH))
    for k in ("wins", "losses", "scratches", "resets"):
        n = _finite(raw.get(k))
        a[k] = int(_clamp(n or 0, 0, 1_000_000))
    updated = _finite(raw.get("updated_at"))
    if updated and updated > 0:
        a["updated_at"] = int(updated)

    positions = {}
    src_pos = raw.get("positions") if isinstance(raw.get("positions"), dict) else {}
    for mint, p in list(src_pos.items())[:MAX_POSITIONS]:
        if not _mint_ok(mint) or not isinstance(p, dict):
            continue
        qty = _finite(p.get("qty")) or 0.0
        cost = _finite(p.get("cost_sol")) or 0.0
        avg = _finite(p.get("avg_px_usd")) or 0.0
        if qty <= 0 or cost < 0 or avg < 0:
            continue
        opened = _finite(p.get("opened_at")) or a["created_at"]
        row = _blank_pos(mint, p.get("symbol"), p.get("name"), p.get("pool"), opened)
        row["qty"] = _q(qty)
        row["cost_sol"] = _q(cost)
        row["avg_px_usd"] = _q(avg)
        row["updated_at"] = int(_finite(p.get("updated_at")) or opened)
        positions[mint] = row
    a["positions"] = positions

    orders = []
    src_orders = raw.get("orders") if isinstance(raw.get("orders"), list) else []
    for o in src_orders[:MAX_ORDERS]:
        if not isinstance(o, dict):
            continue
        mint = o.get("mint")
        side = o.get("side")
        limit = _finite(o.get("limit_px"))
        if side == "oco":
            if not _mint_ok(mint) or mint not in positions:
                continue
            tp, sl = _finite(o.get("tp_px")), _finite(o.get("sl_px"))
            qty = _finite(o.get("qty")) or 0.0
            if qty <= 0 or not ((tp and tp > 0) or (sl and sl > 0)):
                continue
            orders.append({
                "id": _text(o.get("id"), 40) or uuid.uuid4().hex[:12],
                "side": "oco", "mint": mint,
                "symbol": _text(o.get("symbol"), 16) or positions[mint]["symbol"],
                "name": _text(o.get("name"), 64), "pool": _text(o.get("pool"), 64),
                "limit_px": 0.0, "sol": 0.0, "qty": _q(qty),
                "tp_px": _q(tp) if tp and tp > 0 else None,
                "sl_px": _q(sl) if sl and sl > 0 else None,
                "created_at": int(_finite(o.get("created_at")) or a["created_at"]),
            })
            continue
        if side not in ("buy", "sell") or not _mint_ok(mint) or not limit or limit <= 0:
            continue
        if side == "sell" and mint not in positions:
            continue
        oid = _text(o.get("id"), 40) or uuid.uuid4().hex[:12]
        row = {
            "id": oid,
            "side": side,
            "mint": mint,
            "symbol": _text(o.get("symbol"), 16) or (positions.get(mint) or {}).get("symbol") or "?",
            "name": _text(o.get("name"), 64),
            "pool": _text(o.get("pool"), 64),
            "limit_px": _q(limit),
            "sol": 0.0,
            "qty": 0.0,
            "created_at": int(_finite(o.get("created_at")) or a["created_at"]),
        }
        if side == "buy":
            sol = _finite(o.get("sol")) or 0.0
            if sol <= 0:
                continue
            row["sol"] = _q(min(sol, MAX_SPEND))
        else:
            qty = _finite(o.get("qty")) or 0.0
            if qty <= 0:
                continue
            row["qty"] = _q(qty)
        orders.append(row)

    # Drop newest sell orders that reserve more tokens than the position holds.
    for mint, p in positions.items():
        sells = [o for o in orders if o["side"] in ("sell", "oco") and o["mint"] == mint]
        while sum(o["qty"] for o in sells) > p["qty"] * (1 + 1e-9) and sells:
            doomed = sells.pop()
            orders = [o for o in orders if o is not doomed and o["id"] != doomed["id"]]
        held = sum(o["qty"] for o in orders if o["side"] in ("sell", "oco") and o["mint"] == mint)
        p["reserved_qty"] = _q(min(p["qty"], held))

    implied = sum(o["sol"] for o in orders if o["side"] == "buy")
    client_res = _clamp(_finite(raw.get("reserved_sol")) or 0.0, 0.0, MAX_CASH)
    extra = implied - client_res
    if extra > 1e-9:
        a["cash_sol"] = _q(max(0.0, a["cash_sol"] - extra))
    a["reserved_sol"] = _q(implied)
    a["orders"] = orders

    trades = []
    src_tr = raw.get("trades") if isinstance(raw.get("trades"), list) else []
    for t in src_tr[-MAX_TRADES:]:
        if not isinstance(t, dict) or t.get("side") not in ("buy", "sell"):
            continue
        if not _mint_ok(t.get("mint")):
            continue
        trades.append({
            "id": _text(t.get("id"), 40) or uuid.uuid4().hex[:12],
            "ts": int(_finite(t.get("ts")) or a["created_at"]),
            "side": t["side"],
            "kind": t.get("kind") if t.get("kind") in ("limit", "tp", "sl") else "market",
            "mint": t["mint"],
            "symbol": _text(t.get("symbol"), 16) or "?",
            "qty": _q(_finite(t.get("qty")) or 0.0),
            "sol": _q(_finite(t.get("sol")) or 0.0),
            "fee_sol": _q(_finite(t.get("fee_sol")) or 0.0),
            "px_usd": _q(_finite(t.get("px_usd")) or 0.0),
            "fill_px_usd": _q(_finite(t.get("fill_px_usd")) or 0.0),
            "realized_sol": None if t.get("side") == "buy" else _q(_finite(t.get("realized_sol")) or 0.0),
            "pnl_pct": None if t.get("realized_sol") is None and t.get("side") == "buy" else (
                None if _finite(t.get("pnl_pct")) is None else _q(_finite(t.get("pnl_pct")))
            ),
            "price_source": _text(t.get("price_source"), 24),
            "waited_ms": int(_clamp(_finite(t.get("waited_ms")) or 0, 0, 5000)),
        })
    a["trades"] = trades

    daily = {}
    src_daily = raw.get("daily") if isinstance(raw.get("daily"), dict) else {}
    for k, v in list(src_daily.items())[:400]:
        if not isinstance(k, str) or len(k) != 10:
            continue
        n = _finite(v)
        if n is None:
            continue
        daily[k] = _q(n)
    a["daily"] = daily
    seen = []
    for s in (raw.get("seen") if isinstance(raw.get("seen"), list) else [])[-MAX_SEEN:]:
        if isinstance(s, str) and 0 < len(s) <= 64:
            seen.append(s)
    a["seen"] = seen
    return a


def _settings(a):
    return clamp_settings(a.get("settings"))


def trade_settings(account, action=None) -> dict:
    """Stored settings, plus an optional per-trade ``fees`` override that does not stick."""
    base = _settings(account)
    over = (action or {}).get("fees") if isinstance((action or {}).get("fees"), dict) else None
    if not over:
        out = dict(base)
        out["sell_slippage_bps"] = int(out["slippage_bps"])
        return out
    merged = dict(base)
    for k in ("slippage_bps", "sell_slippage_bps", "priority_fee_sol", "bribe_sol", "platform_fee_bps"):
        if k in over and over[k] is not None:
            merged[k] = over[k]
    out = clamp_settings(merged)
    sell = _finite(over.get("sell_slippage_bps"))
    out["sell_slippage_bps"] = int(_clamp(sell if sell is not None else out["slippage_bps"], 0, 5000))
    return out


def _fees(spend_or_gross, settings):
    """Split SOL into priority fee, tip, platform fee and remainder."""
    pri = min(settings.get("priority_fee_sol") or 0.0, spend_or_gross)
    after = spend_or_gross - pri
    tip = min(settings.get("bribe_sol") or 0.0, after)
    after -= tip
    plat = after * (settings.get("platform_fee_bps") or 0) / 10_000.0
    net = after - plat
    return pri, tip, plat, net


def _meta(action, pos=None):
    return {
        "symbol": _text(action.get("symbol"), 16) or (pos or {}).get("symbol") or "?",
        "name": _text(action.get("name"), 64) or (pos or {}).get("name") or "",
        "pool": _text(action.get("pool"), 64) or (pos or {}).get("pool") or "",
    }


def _push_trade(a, trade):
    a["trades"].append(trade)
    if len(a["trades"]) > MAX_TRADES:
        del a["trades"][:-MAX_TRADES]


def _day(ts):
    return datetime.fromtimestamp(int(ts), MYT).strftime("%Y-%m-%d")


def _record_realized(a, realized, ts):
    a["realized_sol"] = _q(a["realized_sol"] + realized)
    key = _day(ts)
    a["daily"][key] = _q(a["daily"].get(key, 0.0) + realized)
    if len(a["daily"]) > 400:
        for old in sorted(a["daily"])[:-400]:
            a["daily"].pop(old, None)
    if realized > 1e-9:
        a["wins"] += 1
    elif realized < -1e-9:
        a["losses"] += 1
    else:
        a["scratches"] += 1


def _close_if_empty(a, mint):
    p = a["positions"].get(mint)
    if not p:
        return
    if p["reserved_qty"] > 1e-12:
        if p["qty"] < p["reserved_qty"]:
            p["qty"] = p["reserved_qty"]
        return
    if p["qty"] <= 1e-10 or p["cost_sol"] <= 1e-12:
        a["orders"] = [o for o in a["orders"] if not (o["side"] == "sell" and o["mint"] == mint)]
        a["positions"].pop(mint, None)


def buy(a, mint, spend, px, sol_usd, now, *, kind="market", meta=None, funded=False,
        price_source="", waited_ms=0, order_id=None, settings=None):
    """Spend ``spend`` SOL (already reserved when ``funded``) and add tokens."""
    s = settings or _settings(a)
    spend = _finite(spend)
    px = _finite(px)
    sol_usd = _finite(sol_usd)
    if not _mint_ok(mint):
        raise Reject("That is not a Solana token address.")
    if spend is None or spend <= 0:
        raise Reject("Enter a paper SOL amount greater than zero.")
    if spend > MAX_SPEND:
        raise Reject("One paper trade can spend at most %s SOL." % int(MAX_SPEND))
    if px is None or px <= 0 or sol_usd is None or sol_usd <= 0:
        raise Reject("No live price for this trade yet.")
    if funded:
        if spend - a["reserved_sol"] > 1e-8:
            raise Reject("That limit is no longer reserved.")
    elif spend - a["cash_sol"] > 1e-8:
        raise Reject("Not enough paper SOL. Deposit more, or cancel a buy limit that is holding some.")
    pri, tip, plat, net = _fees(spend, s)
    if net <= 1e-12:
        raise Reject("That amount is smaller than the paper priority fee and platform fee.")
    slip = s["slippage_bps"] / 10_000.0
    fill_px = px * (1.0 + slip)
    tokens = net * sol_usd / fill_px
    if tokens <= 0 or not math.isfinite(tokens):
        raise Reject("The fill rounded down to zero tokens.")
    effective = spend * sol_usd / tokens
    pos = a["positions"].get(mint)
    info = meta or {}
    if pos is None:
        if len(a["positions"]) >= MAX_POSITIONS:
            raise Reject("The paper book is full (%d positions). Close one first." % MAX_POSITIONS)
        pos = _blank_pos(mint, info.get("symbol"), info.get("name"), info.get("pool"), now)
        a["positions"][mint] = pos
        new_avg = effective
    else:
        new_avg = (pos["qty"] * pos["avg_px_usd"] + tokens * effective) / (pos["qty"] + tokens)
        if info.get("symbol") and pos["symbol"] in ("", "?"):
            pos["symbol"] = _text(info["symbol"], 16)
        if info.get("name") and not pos["name"]:
            pos["name"] = _text(info["name"], 64)
        if info.get("pool") and not pos["pool"]:
            pos["pool"] = _text(info["pool"], 64)
    if funded:
        a["reserved_sol"] = _q(max(0.0, a["reserved_sol"] - spend))
        if order_id:
            a["orders"] = [o for o in a["orders"] if o["id"] != order_id]
    else:
        a["cash_sol"] = _q(a["cash_sol"] - spend)
    pos["qty"] = _q(pos["qty"] + tokens)
    pos["cost_sol"] = _q(pos["cost_sol"] + spend)
    pos["avg_px_usd"] = _q(new_avg)
    pos["updated_at"] = int(now)
    fill = {
        "id": uuid.uuid4().hex[:12],
        "ts": int(now),
        "side": "buy",
        "kind": kind if kind in ("limit", "tp", "sl") else "market",
        "mint": mint,
        "symbol": pos["symbol"],
        "qty": _q(tokens),
        "sol": _q(spend),
        "sol_net": _q(net),
        "fee_sol": _q(pri + tip + plat),
        "priority_fee_sol": _q(pri),
        "bribe_sol": _q(tip),
        "platform_fee_sol": _q(plat),
        "px_usd": _q(px),
        "fill_px_usd": _q(fill_px),
        "avg_px_usd": pos["avg_px_usd"],
        "realized_sol": None,
        "pnl_pct": None,
        "price_source": _text(price_source, 24),
        "waited_ms": int(_clamp(_finite(waited_ms) or 0, 0, 5000)),
    }
    _push_trade(a, fill)
    return fill


def sell(a, mint, qty_out, px, sol_usd, now, *, kind="market", from_reserved=False,
         price_source="", waited_ms=0, order_id=None, settings=None):
    """Sell ``qty_out`` tokens. Reserved tokens can only be sold by their limit."""
    s = settings or _settings(a)
    px = _finite(px)
    sol_usd = _finite(sol_usd)
    qty_out = _finite(qty_out)
    pos = a["positions"].get(mint)
    if pos is None:
        raise Reject("No open paper position in this token.")
    if qty_out is None or qty_out <= 0:
        raise Reject("Nothing to sell.")
    if px is None or px <= 0 or sol_usd is None or sol_usd <= 0:
        raise Reject("No live price for this trade yet.")
    free = pos["qty"] - pos["reserved_qty"]
    if from_reserved:
        if qty_out - pos["reserved_qty"] > 1e-8:
            raise Reject("That sell limit is larger than the tokens it reserved.")
    else:
        if free <= 1e-12:
            raise Reject("Those tokens are reserved by a sell limit. Cancel the limit, or sell a smaller size.")
        if qty_out - free > max(1e-8, free * 1e-6):
            raise Reject("Not enough free tokens. Some are held by a sell limit.")
        qty_out = min(qty_out, free)
    slip = (s.get("sell_slippage_bps") if s.get("sell_slippage_bps") is not None else s["slippage_bps"]) / 10_000.0
    fill_px = px * (1.0 - slip)
    if fill_px <= 0:
        raise Reject("Slippage is 100% or more, so a sell would receive nothing. Lower it in paper settings.")
    gross = qty_out * fill_px / sol_usd
    # A dust sell can be smaller than the priority fee. The fee then takes the
    # whole proceeds and the position still closes; cash does not go negative.
    pri, tip, plat, net = _fees(gross, s)
    if net < 0:
        net = 0.0
    cost_out = pos["cost_sol"] * (qty_out / pos["qty"]) if pos["qty"] else 0.0
    realized = net - cost_out
    pnl_pct = (realized / cost_out * 100.0) if cost_out > 0 else None
    if from_reserved:
        pos["reserved_qty"] = _q(max(0.0, pos["reserved_qty"] - qty_out))
        if order_id:
            a["orders"] = [o for o in a["orders"] if o["id"] != order_id]
    pos["qty"] = _q(max(0.0, pos["qty"] - qty_out))
    pos["cost_sol"] = _q(max(0.0, pos["cost_sol"] - cost_out))
    pos["updated_at"] = int(now)
    a["cash_sol"] = _q(a["cash_sol"] + net)
    _record_realized(a, realized, now)
    fill = {
        "id": uuid.uuid4().hex[:12],
        "ts": int(now),
        "side": "sell",
        "kind": kind if kind in ("limit", "tp", "sl") else "market",
        "mint": mint,
        "symbol": pos["symbol"],
        "qty": _q(qty_out),
        "sol": _q(net),
        "sol_gross": _q(gross),
        "fee_sol": _q(pri + tip + plat),
        "priority_fee_sol": _q(pri),
        "bribe_sol": _q(tip),
        "platform_fee_sol": _q(plat),
        "px_usd": _q(px),
        "fill_px_usd": _q(fill_px),
        "avg_px_usd": pos["avg_px_usd"],
        "cost_sol": _q(cost_out),
        "realized_sol": _q(realized),
        "pnl_pct": None if pnl_pct is None else _q(pnl_pct),
        "price_source": _text(price_source, 24),
        "waited_ms": int(_clamp(_finite(waited_ms) or 0, 0, 5000)),
    }
    _push_trade(a, fill)
    _close_if_empty(a, mint)
    return fill


def _require_price(action):
    px = _finite(action.get("px_usd"))
    sol = _finite(action.get("sol_usd"))
    if px is None or px <= 0 or sol is None or not (5.0 < sol < 5000.0):
        raise Reject("Live price is not available yet. Open the token page and try again in a moment.")
    return px, sol


def _hard(a) -> bool:
    return (a.get("mode") or "practice") == "hard"


def _snap_book(a) -> dict:
    return {k: copy.deepcopy(a.get(k)) for k in _BOOK_KEYS}


def _blank_book() -> dict:
    return {
        "cash_sol": 0.0, "reserved_sol": 0.0, "realized_sol": 0.0, "deposited_sol": 0.0,
        "wins": 0, "losses": 0, "scratches": 0, "positions": {}, "orders": [], "trades": [],
        "daily": {}, "faucet_day": "",
    }


def _load_book(a, snap) -> None:
    snap = snap or _blank_book()
    for k, v in _blank_book().items():
        a[k] = copy.deepcopy(snap.get(k, v))


def _do_deposit(a, action, now):
    if _hard(a):
        raise Reject("Hard mode starts at zero. Claim the daily paper SOL. There is no deposit.")
    sol = _finite(action.get("sol"))
    if sol is None or sol <= 0:
        raise Reject("Enter a paper SOL amount to deposit.")
    if sol > MAX_DEPOSIT:
        raise Reject("One deposit can add at most %s paper SOL." % int(MAX_DEPOSIT))
    if a["cash_sol"] + a["reserved_sol"] + sol > MAX_CASH:
        raise Reject("The paper book stops at %s SOL." % int(MAX_CASH))
    a["cash_sol"] = _q(a["cash_sol"] + sol)
    a["deposited_sol"] = _q(a["deposited_sol"] + sol)
    return {"side": "deposit", "sol": _q(sol), "ts": int(now)}


def _do_reset(a, now):
    if _hard(a):
        raise Reject("Hard mode cannot be reset. Switch back to practice, or keep trading the claimed SOL.")
    settings = _settings(a)
    resets = int(a.get("resets") or 0) + 1
    created = a.get("created_at") or int(now)
    alt = a.get("alt")
    seen = []
    fresh = new_account(created)
    fresh["settings"] = settings
    fresh["resets"] = resets
    fresh["created_at"] = created
    fresh["updated_at"] = int(now)
    fresh["mode"] = "practice"
    fresh["alt"] = alt
    fresh["seen"] = seen
    a.clear()
    a.update(fresh)
    return {"side": "reset", "ts": int(now)}


def _do_mode(a, action, now):
    want = action.get("mode")
    if want not in ("practice", "hard"):
        raise Reject("Choose practice or hard mode.")
    cur = a.get("mode") or "practice"
    if want == cur:
        return {"side": "mode", "mode": cur, "ts": int(now)}
    parked = a.get("alt")
    a["alt"] = _snap_book(a)
    _load_book(a, parked if isinstance(parked, dict) else None)
    a["mode"] = want
    return {"side": "mode", "mode": want, "ts": int(now)}


def _do_faucet(a, action, now):
    if not _hard(a):
        raise Reject("The daily claim is only available in hard mode.")
    day = _day(now)
    if a.get("faucet_day") == day:
        raise Reject("Today's paper SOL is already claimed.")
    if a["cash_sol"] + a["reserved_sol"] + FAUCET_SOL > MAX_CASH:
        raise Reject("The paper book stops at %s SOL." % int(MAX_CASH))
    a["cash_sol"] = _q(a["cash_sol"] + FAUCET_SOL)
    a["faucet_day"] = day
    return {"side": "faucet", "sol": FAUCET_SOL, "day": day, "ts": int(now)}


def _do_tpsl(a, action, now):
    """Attach one take-profit and/or stop to a position. They share one reserved quantity."""
    mint = action.get("mint")
    pos = a["positions"].get(mint) if _mint_ok(mint) else None
    if pos is None:
        raise Reject("Open a paper position before setting a target or a stop.")
    for o in list(a["orders"]):
        if o.get("side") == "oco" and o.get("mint") == mint:
            pos["reserved_qty"] = _q(max(0.0, pos["reserved_qty"] - o["qty"]))
            a["orders"] = [x for x in a["orders"] if x["id"] != o["id"]]
    tp, sl = _finite(action.get("tp_px")), _finite(action.get("sl_px"))
    tp = tp if tp and tp > 0 else None
    sl = sl if sl and sl > 0 else None
    if tp is None and sl is None:
        return {"side": "tpsl", "cleared": True, "ts": int(now)}
    pct = _finite(action.get("pct"))
    if pct is None:
        pct = 100.0
    if pct <= 0 or pct > 100:
        raise Reject("The protected size has to be between 0 and 100 percent.")
    free = pos["qty"] - pos["reserved_qty"]
    qty = min(free, free * pct / 100.0)
    if qty <= 1e-12:
        raise Reject("No free tokens left for a target or stop. Cancel a sell limit first.")
    pos["reserved_qty"] = _q(pos["reserved_qty"] + qty)
    order = {
        "id": uuid.uuid4().hex[:12], "side": "oco", "mint": mint, "symbol": pos["symbol"],
        "name": pos.get("name") or "", "pool": pos.get("pool") or "", "limit_px": 0.0, "sol": 0.0,
        "qty": _q(qty), "tp_px": None if tp is None else _q(tp), "sl_px": None if sl is None else _q(sl),
        "created_at": int(now),
    }
    a["orders"].append(order)
    return {"side": "tpsl", "order": order, "ts": int(now)}


def _do_settings(a, action, now):
    incoming = action.get("settings") if isinstance(action.get("settings"), dict) else {}
    merged = dict(_settings(a))
    for k, v in incoming.items():
        if k != "action" and v is not None:
            merged[k] = v
    a["settings"] = clamp_settings(merged)
    return {"side": "settings", "settings": dict(a["settings"]), "ts": int(now)}


def _do_buy(a, action, now):
    mint = action.get("mint")
    px, sol_usd = _require_price(action)
    pos = a["positions"].get(mint)
    return buy(
        a, mint, action.get("sol"), px, sol_usd, now,
        meta=_meta(action, pos), price_source=action.get("price_source") or "",
        waited_ms=action.get("waited_ms") or 0, settings=trade_settings(a, action),
    )


def _do_sell(a, action, now):
    mint = action.get("mint")
    pos = a["positions"].get(mint)
    if pos is None:
        raise Reject("No open paper position in this token.")
    px, sol_usd = _require_price(action)
    pct = _finite(action.get("pct"))
    qty = _finite(action.get("qty"))
    free = pos["qty"] - pos["reserved_qty"]
    if pct is not None:
        if pct <= 0 or pct > 100:
            raise Reject("Sell percent has to be between 0 and 100.")
        qty = free * (pct / 100.0)
    if qty is None:
        raise Reject("Choose how much of the position to sell.")
    return sell(
        a, mint, qty, px, sol_usd, now,
        price_source=action.get("price_source") or "", waited_ms=action.get("waited_ms") or 0,
        settings=trade_settings(a, action),
    )


def _do_limit(a, action, now):
    mint = action.get("mint")
    if not _mint_ok(mint):
        raise Reject("That is not a Solana token address.")
    side = action.get("side")
    if side not in ("buy", "sell"):
        raise Reject("A limit is either a buy or a sell.")
    limit = _finite(action.get("limit_px"))
    if limit is None or limit <= 0:
        raise Reject("Enter a limit price in USD per token.")
    if len(a["orders"]) >= MAX_ORDERS:
        raise Reject("Too many open paper orders (max %d). Cancel one first." % MAX_ORDERS)
    pos = a["positions"].get(mint)
    info = _meta(action, pos)
    oid = uuid.uuid4().hex[:12]
    order = {
        "id": oid,
        "side": side,
        "mint": mint,
        "symbol": info["symbol"] or (pos or {}).get("symbol") or "?",
        "name": info["name"],
        "pool": info["pool"],
        "limit_px": _q(limit),
        "sol": 0.0,
        "qty": 0.0,
        "created_at": int(now),
    }
    if side == "buy":
        spend = _finite(action.get("sol"))
        if spend is None or spend <= 0:
            raise Reject("Enter the paper SOL to reserve for this buy limit.")
        if spend > MAX_SPEND:
            raise Reject("One paper order can reserve at most %s SOL." % int(MAX_SPEND))
        if spend - a["cash_sol"] > 1e-8:
            raise Reject("Not enough free paper SOL to reserve that buy.")
        # Reject up front when the fees would consume the whole order.
        _pri, _tip, _plat, net = _fees(spend, _settings(a))
        if net <= 1e-12:
            raise Reject("That amount is smaller than the paper fees.")
        a["cash_sol"] = _q(a["cash_sol"] - spend)
        a["reserved_sol"] = _q(a["reserved_sol"] + spend)
        order["sol"] = _q(spend)
    else:
        if pos is None:
            raise Reject("You need a paper position before placing a sell limit.")
        free = pos["qty"] - pos["reserved_qty"]
        pct = _finite(action.get("pct"))
        qty = _finite(action.get("qty"))
        if pct is not None:
            if pct <= 0 or pct > 100:
                raise Reject("Sell percent has to be between 0 and 100.")
            qty = free * (pct / 100.0)
        if qty is None or qty <= 0:
            raise Reject("Choose how many tokens the sell limit should reserve.")
        if qty - free > max(1e-8, free * 1e-6):
            raise Reject("Not enough free tokens to reserve that sell.")
        qty = min(qty, free)
        pos["reserved_qty"] = _q(pos["reserved_qty"] + qty)
        order["qty"] = _q(qty)
        order["symbol"] = pos["symbol"]
    a["orders"].append(order)
    a["orders"].sort(key=lambda o: o["created_at"])
    return {"side": "limit", "order": order, "ts": int(now)}


def _do_cancel(a, action, now):
    oid = action.get("id")
    order = next((o for o in a["orders"] if o["id"] == oid), None)
    if order is None:
        raise Reject("That paper order is already gone.")
    a["orders"] = [o for o in a["orders"] if o["id"] != oid]
    if order["side"] == "buy":
        a["reserved_sol"] = _q(max(0.0, a["reserved_sol"] - order["sol"]))
        a["cash_sol"] = _q(min(MAX_CASH, a["cash_sol"] + order["sol"]))
    else:
        pos = a["positions"].get(order["mint"])
        if pos:
            pos["reserved_qty"] = _q(max(0.0, pos["reserved_qty"] - order["qty"]))
            _close_if_empty(a, order["mint"])
    return {"side": "cancel", "id": oid, "ts": int(now)}


def _quote_map(action):
    raw = action.get("quotes")
    out = {}
    if not isinstance(raw, dict):
        return out
    for mint, px in list(raw.items())[:MAX_POSITIONS + MAX_ORDERS]:
        if not _mint_ok(mint):
            continue
        n = _finite(px)
        if isinstance(px, dict):
            n = _finite(px.get("px"))
        if n and n > 0:
            out[mint] = n
    return out


def _do_mark(a, action, now):
    """Fill triggered limits. Prices come from the caller. No I/O."""
    quotes = _quote_map(action)
    sol_usd = _finite(action.get("sol_usd"))
    if sol_usd is None or not (5.0 < sol_usd < 5000.0):
        raise Reject("SOL/USD is not available, so limits cannot be marked.")
    fills = []
    # Snapshot ids; fills remove orders as they go.
    for order in list(a["orders"]):
        if order["id"] not in {o["id"] for o in a["orders"]}:
            continue
        px = quotes.get(order["mint"])
        if not px:
            continue
        if order["side"] == "oco":
            kind = None
            if order.get("tp_px") and px >= order["tp_px"] * (1 - 1e-12):
                kind = "tp"
            elif order.get("sl_px") and px <= order["sl_px"] * (1 + 1e-12):
                kind = "sl"
            if not kind:
                continue
        elif order["side"] == "buy" and px > order["limit_px"] * (1 + 1e-12):
            continue
        elif order["side"] == "sell" and px < order["limit_px"] * (1 - 1e-12):
            continue
        else:
            kind = "limit" if order["side"] in ("buy", "sell") else None
        try:
            if order["side"] == "buy":
                fill = buy(
                    a, order["mint"], order["sol"], px, sol_usd, now, kind="limit", funded=True,
                    meta=order, price_source=action.get("price_source") or "mark", order_id=order["id"],
                    settings=trade_settings(a, action),
                )
            elif order["side"] in ("sell", "oco"):
                fill = sell(
                    a, order["mint"], order["qty"], px, sol_usd, now, kind=kind or "limit", from_reserved=True,
                    price_source=action.get("price_source") or "mark", order_id=order["id"],
                    settings=trade_settings(a, action),
                )
            else:
                continue
        except Reject as e:
            order["last_error"] = e.msg
            continue
        fills.append(fill)
    return fills


def apply(account, action, *, now=None, dry=False):
    """Copy ``account``, apply one action, and return ``(account, fill, fills)``.

    The caller's dict is never mutated. ``dry=True`` does the math and leaves
    ``seen`` / ``updated_at`` untouched so a preview cannot consume an id.
    """
    if not isinstance(action, dict):
        raise Reject("Missing action.")
    now_i = int(now if now is not None else time.time())
    a = copy.deepcopy(account if isinstance(account, dict) and account.get("v") == 1 else normalize(account))
    # Engine-built accounts skip the lossy normalize round-trip; client payloads
    # are normalized by the HTTP layer before they get here.
    name = action.get("action")
    rid = action.get("rid")
    if isinstance(rid, str) and rid and rid in (a.get("seen") or []) and not dry:
        raise Replay(a)
    fills = []
    fill = None
    if name == "deposit":
        fill = _do_deposit(a, action, now_i)
    elif name == "reset":
        fill = _do_reset(a, now_i)
    elif name == "settings":
        fill = _do_settings(a, action, now_i)
    elif name == "buy":
        fill = _do_buy(a, action, now_i)
    elif name == "sell":
        fill = _do_sell(a, action, now_i)
    elif name == "limit":
        fill = _do_limit(a, action, now_i)
    elif name == "cancel":
        fill = _do_cancel(a, action, now_i)
    elif name == "tpsl":
        fill = _do_tpsl(a, action, now_i)
    elif name == "mode":
        fill = _do_mode(a, action, now_i)
    elif name == "faucet":
        fill = _do_faucet(a, action, now_i)
    elif name == "mark":
        fills = _do_mark(a, action, now_i)
        if not fills:
            return copy.deepcopy(account) if isinstance(account, dict) else a, None, []
    else:
        raise Reject("Unknown paper action.")
    if name == "reset":
        # reset replaced the dict contents; seen must not keep old ids, except this one
        a["seen"] = []
    if not dry:
        a["updated_at"] = now_i
        if isinstance(rid, str) and rid and name != "mark":
            a["seen"] = ([rid] + [s for s in a.get("seen", []) if s != rid])[:MAX_SEEN]
    return a, fill, fills


def is_empty(account) -> bool:
    if not isinstance(account, dict):
        return True
    return (
        (account.get("cash_sol") or 0) == 0
        and (account.get("reserved_sol") or 0) == 0
        and not account.get("positions")
        and not account.get("orders")
        and not account.get("trades")
        and (account.get("deposited_sol") or 0) == 0
    )


def summary(account, prices=None, sol_usd=None) -> dict:
    """Mark open positions. Missing prices leave totals partial instead of zero."""
    a = account if isinstance(account, dict) else new_account()
    prices = prices or {}
    sol = _finite(sol_usd)
    sol_ok = sol is not None and 5.0 < sol < 5000.0
    rows = []
    known = 0.0
    unpriced = []
    equity = (a.get("cash_sol") or 0) + (a.get("reserved_sol") or 0)
    for mint, p in (a.get("positions") or {}).items():
        px = _finite(prices.get(mint))
        if isinstance(prices.get(mint), dict):
            px = _finite(prices[mint].get("px"))
        row = {
            "mint": mint,
            "symbol": p.get("symbol") or "?",
            "name": p.get("name") or "",
            "qty": p.get("qty") or 0,
            "reserved_qty": p.get("reserved_qty") or 0,
            "free_qty": max(0.0, (p.get("qty") or 0) - (p.get("reserved_qty") or 0)),
            "cost_sol": p.get("cost_sol") or 0,
            "avg_px_usd": p.get("avg_px_usd") or 0,
            "opened_at": p.get("opened_at"),
            "mark_px": None,
            "mark_sol": None,
            "upnl_sol": None,
            "upnl_pct": None,
        }
        if px and px > 0 and sol_ok and p.get("qty"):
            mark = p["qty"] * px / sol
            upnl = mark - (p.get("cost_sol") or 0)
            cost = p.get("cost_sol") or 0
            row.update({
                "mark_px": _q(px),
                "mark_sol": _q(mark),
                "upnl_sol": _q(upnl),
                "upnl_pct": _q(upnl / cost * 100.0) if cost else None,
            })
            known += upnl
            equity += mark
        else:
            unpriced.append(mint)
        rows.append(row)
    wins = int(a.get("wins") or 0)
    losses = int(a.get("losses") or 0)
    closed = wins + losses
    sells = [t for t in (a.get("trades") or []) if t.get("side") == "sell" and _finite(t.get("realized_sol")) is not None]
    best = max(sells, key=lambda t: t["realized_sol"]) if sells else None
    worst = min(sells, key=lambda t: t["realized_sol"]) if sells else None
    partial = bool(unpriced) and bool(a.get("positions"))
    return {
        "cash_sol": a.get("cash_sol") or 0,
        "reserved_sol": a.get("reserved_sol") or 0,
        "equity_sol": None if partial else _q(equity),
        "equity_known_sol": _q(equity if not partial else (a.get("cash_sol") or 0) + (a.get("reserved_sol") or 0) + sum(
            (r["mark_sol"] or 0) for r in rows if r["mark_sol"] is not None
        )),
        "unrealized_sol": None if partial else _q(known),
        "unrealized_known_sol": _q(known),
        "unpriced": unpriced,
        "realized_sol": a.get("realized_sol") or 0,
        "deposited_sol": a.get("deposited_sol") or 0,
        "win_rate": (wins / closed) if closed else None,
        "wins": wins,
        "losses": losses,
        "scratches": int(a.get("scratches") or 0),
        "n_positions": len(a.get("positions") or {}),
        "n_orders": len(a.get("orders") or []),
        "n_trades": len(a.get("trades") or []),
        "best": best,
        "worst": worst,
        "positions": rows,
        "sol_usd": sol if sol_ok else None,
        "daily": dict(a.get("daily") or {}),
        "settings": clamp_settings(a.get("settings")),
    }
