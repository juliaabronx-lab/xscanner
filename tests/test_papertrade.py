"""Fill, fee and PnL math for the paper book. No network, no wallet."""

import os
import tempfile
import unittest
from datetime import datetime, timezone

os.environ.setdefault("PULSE", "0")
os.environ.setdefault("LIVE_SCAN", "0")
os.environ.setdefault("SELF_PING", "0")
os.environ.setdefault("PAPER_ACT_PER_10S", "0")
os.environ.setdefault("PAPER_BOOK_DIR", tempfile.mkdtemp(prefix="paperbook-"))

import papertrade as pt
import paperbook as pb

MINT = "So11111111111111111111111111111111111111112"
MINT2 = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NOW = 1_767_225_600  # 2026-01-01 00:00:00 UTC = 08:00 MYT


def book(**settings):
    a = pt.new_account(NOW)
    if settings:
        a, _, _ = pt.apply(a, {"action": "settings", "settings": settings}, now=NOW)
    a, _, _ = pt.apply(a, {"action": "deposit", "sol": 10}, now=NOW)
    return a


class MathTests(unittest.TestCase):
    def test_zero_fee_round_trip_doubles(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, buy, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100,
                                 "symbol": "TEST"}, now=NOW)
        self.assertAlmostEqual(buy["qty"], 100.0, places=8)
        self.assertAlmostEqual(buy["fill_px_usd"], 1.0, places=8)
        self.assertAlmostEqual(buy["avg_px_usd"], 1.0, places=8)
        self.assertAlmostEqual(a["cash_sol"], 9.0, places=8)
        self.assertAlmostEqual(a["positions"][MINT]["cost_sol"], 1.0, places=8)

        a, sell, _ = pt.apply(a, {"action": "sell", "mint": MINT, "pct": 100, "px_usd": 2, "sol_usd": 100}, now=NOW + 60)
        self.assertAlmostEqual(sell["sol"], 2.0, places=8)
        self.assertAlmostEqual(sell["realized_sol"], 1.0, places=8)
        self.assertAlmostEqual(sell["pnl_pct"], 100.0, places=6)
        self.assertAlmostEqual(a["cash_sol"], 11.0, places=8)
        self.assertAlmostEqual(a["realized_sol"], 1.0, places=8)
        self.assertNotIn(MINT, a["positions"])
        self.assertEqual(a["wins"], 1)
        self.assertEqual(a["losses"], 0)

    def test_slippage_and_fees_on_buy(self):
        # 1% slippage, 0.001 SOL priority, 1% platform, spend exactly 1 SOL.
        a = book(slippage_bps=100, priority_fee_sol=0.001, platform_fee_bps=100)
        px, sol_usd, spend = 0.001, 100.0, 1.0
        pri = 0.001
        after = spend - pri
        plat = after * 0.01
        net = after - plat
        fill_px = px * 1.01
        tokens = net * sol_usd / fill_px
        a, buy, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": spend, "px_usd": px, "sol_usd": sol_usd,
                                 "symbol": "FEE"}, now=NOW)
        self.assertAlmostEqual(buy["fee_sol"], pri + plat, places=10)
        self.assertAlmostEqual(buy["fill_px_usd"], fill_px, places=12)
        self.assertAlmostEqual(buy["qty"], tokens, places=6)
        self.assertGreater(buy["fill_px_usd"], px)
        self.assertAlmostEqual(a["positions"][MINT]["avg_px_usd"], spend * sol_usd / tokens, places=8)
        self.assertAlmostEqual(a["cash_sol"], 9.0, places=8)
        # Selling back at the same spot price is a loss: slippage and fees both ways.
        a, sell, _ = pt.apply(a, {"action": "sell", "mint": MINT, "pct": 100, "px_usd": px, "sol_usd": sol_usd}, now=NOW + 5)
        self.assertLess(sell["fill_px_usd"], px)
        self.assertLess(sell["realized_sol"], 0)
        self.assertEqual(a["losses"], 1)
        self.assertNotIn(MINT, a["positions"])
        self.assertAlmostEqual(a["cash_sol"], 9.0 + sell["sol"], places=8)

    def test_average_entry_two_buys(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "AVG"}, now=NOW)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 2, "sol_usd": 100}, now=NOW + 1)
        pos = a["positions"][MINT]
        self.assertAlmostEqual(pos["qty"], 150.0, places=8)          # 100 + 50 tokens
        self.assertAlmostEqual(pos["cost_sol"], 2.0, places=8)
        self.assertAlmostEqual(pos["avg_px_usd"], (100 * 1 + 50 * 2) / 150, places=8)

    def test_partial_sell_keeps_cost_share(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "HALF"}, now=NOW)
        a, sell, _ = pt.apply(a, {"action": "sell", "mint": MINT, "pct": 50, "px_usd": 1, "sol_usd": 100}, now=NOW + 1)
        self.assertAlmostEqual(sell["realized_sol"], 0.0, places=8)
        self.assertAlmostEqual(sell["pnl_pct"], 0.0, places=6)
        pos = a["positions"][MINT]
        self.assertAlmostEqual(pos["qty"], 50.0, places=8)
        self.assertAlmostEqual(pos["cost_sol"], 0.5, places=8)
        self.assertAlmostEqual(a["cash_sol"], 9.5, places=8)
        self.assertEqual(a["scratches"], 1)
        self.assertIsNone(pt.summary(a, {}, 100)["win_rate"])

    def test_insufficient_cash_and_tiny_spend(self):
        a = book()
        original = a["cash_sol"]
        with self.assertRaises(pt.Reject):
            pt.apply(a, {"action": "buy", "mint": MINT, "sol": 999, "px_usd": 1, "sol_usd": 100}, now=NOW)
        self.assertEqual(a["cash_sol"], original)
        with self.assertRaises(pt.Reject) as cm:
            pt.apply(a, {"action": "buy", "mint": MINT, "sol": 0.0001, "px_usd": 1, "sol_usd": 100}, now=NOW)
        self.assertIn("fee", cm.exception.msg.lower())
        self.assertEqual(a["cash_sol"], original)

    def test_limit_buy_reserves_and_fills_on_touch(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, placed, _ = pt.apply(a, {"action": "limit", "side": "buy", "mint": MINT, "sol": 1,
                                    "limit_px": 1, "symbol": "LIM"}, now=NOW)
        self.assertAlmostEqual(a["cash_sol"], 9.0, places=8)
        self.assertAlmostEqual(a["reserved_sol"], 1.0, places=8)
        oid = placed["order"]["id"]
        a2, _, fills = pt.apply(a, {"action": "mark", "quotes": {MINT: 1.2}, "sol_usd": 100}, now=NOW + 1)
        self.assertEqual(fills, [])
        self.assertEqual(len(a2["orders"]), 1)
        a3, _, fills = pt.apply(a, {"action": "mark", "quotes": {MINT: 0.5}, "sol_usd": 100}, now=NOW + 2)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["kind"], "limit")
        self.assertAlmostEqual(fills[0]["qty"], 200.0, places=6)  # 1 SOL at a better live price
        self.assertEqual(a3["orders"], [])
        self.assertAlmostEqual(a3["reserved_sol"], 0.0, places=8)
        self.assertAlmostEqual(a3["cash_sol"], 9.0, places=8)
        self.assertNotIn(oid, {o["id"] for o in a3["orders"]})

    def test_limit_sell_blocks_a_second_full_sell(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "HOLD"}, now=NOW)
        a, _, _ = pt.apply(a, {"action": "limit", "side": "sell", "mint": MINT, "pct": 100, "limit_px": 3}, now=NOW + 1)
        self.assertAlmostEqual(a["positions"][MINT]["reserved_qty"], 100.0, places=6)
        with self.assertRaises(pt.Reject):
            pt.apply(a, {"action": "sell", "mint": MINT, "pct": 100, "px_usd": 1, "sol_usd": 100}, now=NOW + 2)
        a, _, fills = pt.apply(a, {"action": "mark", "quotes": {MINT: 3}, "sol_usd": 100}, now=NOW + 3)
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0]["realized_sol"], 2.0, places=6)  # 3 SOL out, 1 SOL cost
        self.assertNotIn(MINT, a["positions"])
        self.assertEqual(a["orders"], [])

    def test_cancel_buy_limit_returns_cash(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, placed, _ = pt.apply(a, {"action": "limit", "side": "buy", "mint": MINT, "sol": 2, "limit_px": 1}, now=NOW)
        a, _, _ = pt.apply(a, {"action": "cancel", "id": placed["order"]["id"]}, now=NOW + 1)
        self.assertEqual(a["orders"], [])
        self.assertAlmostEqual(a["cash_sol"], 10.0, places=8)
        self.assertAlmostEqual(a["reserved_sol"], 0.0, places=8)

    def test_reset_clears_book_and_keeps_settings(self):
        a = book(slippage_bps=250, priority_fee_sol=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "Z"}, now=NOW)
        a, _, _ = pt.apply(a, {"action": "reset"}, now=NOW + 9)
        self.assertAlmostEqual(a["cash_sol"], 0.0, places=8)
        self.assertEqual(a["positions"], {})
        self.assertEqual(a["trades"], [])
        self.assertEqual(a["realized_sol"], 0.0)
        self.assertEqual(a["settings"]["slippage_bps"], 250)
        self.assertEqual(a["resets"], 1)

    def test_win_rate_and_daily_bucket_myt(self):
        # 20:30 UTC is 04:30 the next calendar day in MYT (UTC+8).
        ts = int(datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc).timestamp())
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "W"}, now=ts)
        a, _, _ = pt.apply(a, {"action": "sell", "mint": MINT, "pct": 100, "px_usd": 2, "sol_usd": 100}, now=ts + 10)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT2, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "L"}, now=ts + 20)
        a, _, _ = pt.apply(a, {"action": "sell", "mint": MINT2, "pct": 100, "px_usd": 0.5, "sol_usd": 100}, now=ts + 30)
        s = pt.summary(a, {}, 100)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["win_rate"], 0.5, places=8)
        self.assertAlmostEqual(s["realized_sol"], 0.5, places=8)  # +1 and -0.5
        self.assertIn("2026-01-02", a["daily"])
        self.assertAlmostEqual(a["daily"]["2026-01-02"], 0.5, places=8)
        self.assertEqual(s["best"]["symbol"], "W")
        self.assertEqual(s["worst"]["symbol"], "L")

    def test_mark_to_market_unrealized(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "UP"}, now=NOW)
        s = pt.summary(a, {MINT: 1.5}, 100)
        self.assertAlmostEqual(s["positions"][0]["upnl_sol"], 0.5, places=8)
        self.assertAlmostEqual(s["positions"][0]["upnl_pct"], 50.0, places=6)
        self.assertAlmostEqual(s["equity_sol"], 10.5, places=8)
        self.assertAlmostEqual(s["unrealized_sol"], 0.5, places=8)
        missing = pt.summary(a, {}, 100)
        self.assertIsNone(missing["unrealized_sol"])
        self.assertIsNone(missing["equity_sol"])
        self.assertEqual(missing["unpriced"], [MINT])

    def test_replay_and_dry_run_do_not_double_apply(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        a, _, _ = pt.apply(a, {"action": "deposit", "sol": 1, "rid": "dep-1"}, now=NOW)
        self.assertAlmostEqual(a["cash_sol"], 11.0, places=8)
        with self.assertRaises(pt.Replay):
            pt.apply(a, {"action": "deposit", "sol": 1, "rid": "dep-1"}, now=NOW + 1)
        self.assertAlmostEqual(a["cash_sol"], 11.0, places=8)
        snapshot = pt.normalize(a)
        preview, fill, _ = pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100,
                                        "symbol": "DRY", "rid": "b1"}, now=NOW, dry=True)
        self.assertAlmostEqual(a["cash_sol"], 11.0, places=8)
        self.assertNotIn("b1", a["seen"])
        self.assertAlmostEqual(preview["cash_sol"], 10.0, places=8)
        self.assertEqual(fill["side"], "buy")
        self.assertEqual(snapshot["cash_sol"], a["cash_sol"])

    def test_apply_does_not_mutate_caller(self):
        a = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        cash = a["cash_sol"]
        pt.apply(a, {"action": "buy", "mint": MINT, "sol": 1, "px_usd": 1, "sol_usd": 100, "symbol": "X"}, now=NOW)
        self.assertEqual(a["cash_sol"], cash)
        self.assertEqual(a["positions"], {})

    def test_normalize_puts_reserved_back_on_the_orders(self):
        raw = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
        raw, _, _ = pt.apply(raw, {"action": "limit", "side": "buy", "mint": MINT, "sol": 4, "limit_px": 1}, now=NOW)
        tampered = pt.normalize({**raw, "reserved_sol": 0, "cash_sol": raw["cash_sol"] + 4})
        self.assertAlmostEqual(tampered["reserved_sol"], 4.0, places=8)
        self.assertAlmostEqual(tampered["cash_sol"], raw["cash_sol"], places=8)


class BookApiTests(unittest.TestCase):
    def test_displayed_price_used_without_upstream(self):
        import trader as tr
        called = {"n": 0}
        real = tr.ds_tokens

        def boom(*_a, **_k):
            called["n"] += 1
            raise AssertionError("paper book must not call DexScreener")

        tr.ds_tokens = boom
        tr._quote_cache.clear()
        try:
            out = pb.handle_act(None, {
                "action": "buy", "mint": MINT, "sol": 1, "px_usd": 0.42, "sol_usd": 150,
                "symbol": "LOC", "account": book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0),
            }, now=NOW)
        finally:
            tr.ds_tokens = real
        self.assertEqual(called["n"], 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["fill"]["price_source"], "displayed")
        self.assertAlmostEqual(out["fill"]["px_usd"], 0.42, places=8)
        self.assertAlmostEqual(out["account"]["cash_sol"], 9.0, places=8)

    def test_fresh_cache_beats_a_client_hint(self):
        import trader as tr
        tr._quote_cache[MINT] = (time_now(), {"px": 2.0})
        try:
            acct = book(slippage_bps=0, priority_fee_sol=0, platform_fee_bps=0)
            out = pb.handle_act(None, {
                "action": "buy", "mint": MINT, "sol": 1, "px_usd": 99, "sol_usd": 100,
                "symbol": "CACHE", "account": acct,
            }, now=NOW)
        finally:
            tr._quote_cache.pop(MINT, None)
        self.assertEqual(out["fill"]["price_source"], "quote-cache")
        self.assertAlmostEqual(out["fill"]["px_usd"], 2.0, places=8)
        self.assertAlmostEqual(out["fill"]["qty"], 50.0, places=6)

    def test_server_book_ignores_client_cash(self):
        import app as appmod
        email = "paper-test@example.com"
        path = pb._path(email)
        if os.path.exists(path):
            os.remove(path)
        user = {"email": email, "name": "Paper"}
        real = appmod.auth.current_user
        appmod.auth.current_user = lambda _request: user
        try:
            from fastapi.testclient import TestClient
            client = TestClient(appmod.app)
            r = client.post("/api/paper/book/act", json={"action": "deposit", "sol": 3})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertAlmostEqual(r.json()["account"]["cash_sol"], 3.0, places=8)
            # A tampered body must not replace the server balance.
            r = client.post("/api/paper/book/act", json={
                "action": "deposit", "sol": 1,
                "account": {"v": 1, "cash_sol": 5000, "positions": {}, "orders": [], "trades": []},
            })
            self.assertEqual(r.status_code, 200, r.text)
            self.assertAlmostEqual(r.json()["account"]["cash_sol"], 4.0, places=8)
            got = client.get("/api/paper/book")
            self.assertTrue(got.json()["signed_in"])
            self.assertAlmostEqual(got.json()["account"]["cash_sol"], 4.0, places=8)
            # The shared RM ledger route is still the old one.
            legacy = client.get("/api/paper")
            self.assertEqual(legacy.status_code, 200)
            self.assertIn("n_trades", legacy.json())
        finally:
            appmod.auth.current_user = real
            if os.path.exists(path):
                os.remove(path)


def time_now():
    import time
    return time.time()


if __name__ == "__main__":
    unittest.main()
