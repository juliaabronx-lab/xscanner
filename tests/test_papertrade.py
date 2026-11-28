"""Fill math for the simulation book. No network, no wallets."""
import unittest

import papertrade as pt

MINT = "9cmXfuC26anZtH9AUWVzpUxU1UJ7PPogYxftYTepump"


def funded(sol=10):
    a, fill, _ = pt.apply(pt.new_account(now=1), {"action": "deposit", "sol": sol, "rid": "d1"}, now=1)
    assert fill["side"] == "deposit"
    return a


class FillMath(unittest.TestCase):
    def test_buy_spends_exact_sol_and_charges_fees(self):
        a, fill, _ = pt.apply(funded(), {
            "action": "buy", "mint": MINT, "sol": 1, "px_usd": 0.001, "sol_usd": 100,
            "symbol": "TST", "rid": "b1",
        }, now=2)
        self.assertAlmostEqual(a["cash_sol"], 9, places=6)
        self.assertGreater(fill["fee_sol"], 0)
        self.assertGreater(fill["qty"], 0)
        self.assertIn(MINT, a["positions"])

    def test_sell_realizes_against_cost(self):
        a, _, _ = pt.apply(funded(), {
            "action": "buy", "mint": MINT, "sol": 1, "px_usd": 0.001, "sol_usd": 100,
            "symbol": "TST", "rid": "b1",
        }, now=2)
        a, fill, _ = pt.apply(a, {
            "action": "sell", "mint": MINT, "pct": 100, "px_usd": 0.002, "sol_usd": 100, "rid": "s1",
        }, now=3)
        self.assertGreater(fill["realized_sol"], 0)
        self.assertNotIn(MINT, a["positions"])

    def test_buy_limit_reserves_cash_until_price_crosses(self):
        a, fill, _ = pt.apply(funded(), {
            "action": "limit", "side": "buy", "mint": MINT, "sol": 1, "limit_px": 0.001,
            "symbol": "TST", "rid": "l1",
        }, now=2)
        self.assertEqual(fill["side"], "limit")
        self.assertAlmostEqual(a["reserved_sol"], 1, places=6)
        a, _, fills = pt.apply(a, {
            "action": "mark", "quotes": {MINT: 0.0009}, "sol_usd": 100, "rid": "m1",
        }, now=3)
        self.assertTrue(any(f.get("kind") == "limit" for f in fills))
        self.assertAlmostEqual(a["reserved_sol"], 0, places=6)
        self.assertIn(MINT, a["positions"])


if __name__ == "__main__":
    unittest.main()
