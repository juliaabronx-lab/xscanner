"""Trades tape: GeckoTerminal 429 falls back to RPC, and concurrent viewers share one fetch."""
import threading, time, unittest

import trades_tape as tape

POOL = "FcsUythNUWbfT7RP2yuFp1kiWjft15du9WtgLUrrqx3p"
MINT = "9cmXfuC26anZtH9AUWVzpUxU1UJ7PPogYxftYTepump"
USER = "7VfU1sC4vYkQ2mE4rT3wL8nB6pH4aJxD5cF2gS8uZ1yK"
WSOL = tape.WSOL
USDC = tape.USDC
SIG = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW"


def _tx(user_pre_tok, user_post_tok, quote_mint, quote_pre, quote_post, err=None, block_time=1730000000, fee=5000,
        native=None):
    pre_tok = [
        {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": user_pre_tok, "decimals": 6}},
        {"mint": MINT, "owner": POOL, "uiTokenAmount": {"uiAmount": 500000.0, "decimals": 6}},
    ]
    post_tok = [
        {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": user_post_tok, "decimals": 6}},
        {"mint": MINT, "owner": POOL, "uiTokenAmount": {"uiAmount": 500000.0 - (user_post_tok - user_pre_tok), "decimals": 6}},
    ]
    if quote_mint:
        pre_tok.append({"mint": quote_mint, "owner": USER, "uiTokenAmount": {"uiAmount": quote_pre, "decimals": 6}})
        post_tok.append({"mint": quote_mint, "owner": USER, "uiTokenAmount": {"uiAmount": quote_post, "decimals": 6}})
        pre_tok.append({"mint": quote_mint, "owner": POOL, "uiTokenAmount": {"uiAmount": 10.0, "decimals": 6}})
        post_tok.append({"mint": quote_mint, "owner": POOL, "uiTokenAmount": {"uiAmount": 10.0 - (quote_post - quote_pre), "decimals": 6}})
    pre_bal, post_bal = [2_000_000_000, 1_000_000_000], [1_500_000_000, 1_500_000_000]
    if native:
        pre_bal[0], post_bal[0] = native
    return {
        "blockTime": block_time,
        "transaction": {"signatures": [SIG], "message": {"accountKeys": [
            {"pubkey": USER, "signer": True, "writable": True},
            {"pubkey": POOL, "signer": False, "writable": True},
        ]}},
        "meta": {"err": err, "fee": fee, "preBalances": pre_bal, "postBalances": post_bal,
                 "preTokenBalances": pre_tok, "postTokenBalances": post_tok},
    }


def _gt_ok(trades=None):
    rows = trades if trades is not None else [
        {"t": 1730000000, "k": "buy", "usd": 10.0, "px": 0.01, "amt": 1000.0, "w": USER, "sig": "gtsig", "anom": False, "tok": MINT}
    ]
    return {"available": True, "trades": rows, "n_seen": len(rows), "traders": [], "span_s": 0,
            "fetched_at": 1730000000, "note": "Last trades GeckoTerminal reports for this pool (up to 300)."}


def _gt_429():
    return {"available": False, "reason": "Trades tape unavailable right now (rate-limited, retry in 145s).",
            "trades": [], "traders": []}


class ParseTests(unittest.TestCase):
    def test_buy_priced_from_wsol(self):
        row = tape.parse_swap(_tx(1000, 1500, WSOL, 2.0, 1.5), POOL, MINT, SIG, sol_usd=150)
        self.assertEqual(row["k"], "buy")
        self.assertEqual(row["usd"], 75.0)          # 0.5 WSOL * $150, measured from the fixture deltas
        self.assertEqual(row["amt"], 500.0)
        self.assertEqual(row["px"], 0.15)
        self.assertEqual(row["w"], USER)
        self.assertEqual(row["tok"], MINT)
        self.assertEqual(row["sig"], SIG)
        self.assertFalse(row["anom"])

    def test_sell_priced_from_usdc_without_sol_price(self):
        row = tape.parse_swap(_tx(1500, 1000, USDC, 1.0, 5.5), POOL, MINT, SIG, sol_usd=None)
        self.assertEqual(row["k"], "sell")
        self.assertEqual(row["usd"], 4.5)
        self.assertEqual(row["w"], USER)

    def test_native_sol_when_no_quote_token(self):
        # 0.5 SOL leaves the signer and the fee is 0, so the swap is exactly 0.5 SOL.
        tx = _tx(1000, 1100, None, None, None, fee=0, native=(2_000_000_000, 1_500_000_000))
        row = tape.parse_swap(tx, POOL, MINT, SIG, sol_usd=100)
        self.assertEqual(row["k"], "buy")
        self.assertEqual(row["usd"], 50.0)

    def test_no_price_no_row(self):
        tx = _tx(1000, 1100, None, None, None, fee=0, native=(2_000_000_000, 1_500_000_000))
        self.assertIsNone(tape.parse_swap(tx, POOL, MINT, SIG, sol_usd=None))

    def test_failed_transaction_skipped(self):
        tx = _tx(1000, 1500, WSOL, 2.0, 1.5, err={"InstructionError": [0, "Custom"]})
        self.assertIsNone(tape.parse_swap(tx, POOL, MINT, SIG, sol_usd=150))

    def test_pool_vault_is_not_the_trader(self):
        row = tape.parse_swap(_tx(1000, 1500, WSOL, 2.0, 1.5), POOL, MINT, SIG, sol_usd=150)
        self.assertNotEqual(row["w"], POOL)


class TapeTests(unittest.TestCase):
    def setUp(self):
        tape.reset()

    def _rpc(self, sigs=None, tx=None, calls=None):
        sigs = sigs if sigs is not None else [{"signature": SIG, "err": None, "blockTime": 1730000000}]
        tx = tx if tx is not None else _tx(1000, 1500, WSOL, 2.0, 1.5)

        def rpc(method, params):
            if calls is not None:
                calls.append(method)
            if method == "getSignaturesForAddress":
                self.assertEqual(params[0], POOL)
                return sigs
            if method == "getTransaction":
                self.assertEqual(params[0], SIG)
                return tx
            raise AssertionError(method)
        return rpc

    def test_gecko_429_falls_back_to_rpc_and_normalizes(self):
        calls = []
        out = tape.trades(POOL, MINT, gt=lambda p, t: _gt_429(), rpc=self._rpc(calls=calls), sol_usd=150)
        self.assertTrue(out["available"])
        self.assertEqual(out["source"], "solana_rpc")
        self.assertEqual(out["n_seen"], 1)
        self.assertEqual(len(out["trades"]), 1)
        tr = out["trades"][0]
        self.assertEqual(set(tr), {"t", "k", "usd", "px", "amt", "w", "sig", "anom", "tok"})
        self.assertEqual(tr["k"], "buy")
        self.assertEqual(tr["usd"], 75.0)
        self.assertEqual(out["traders"][0]["w"], USER)
        self.assertEqual(out["traders"][0]["buys"], 1)
        self.assertEqual(out["traders"][0]["buy_usd"], 75.0)
        self.assertEqual(out["traders"][0]["net_usd"], -75.0)
        self.assertIn("Solana RPC", out["note"])
        self.assertEqual(calls, ["getSignaturesForAddress", "getTransaction"])

    def test_gecko_success_does_not_call_rpc(self):
        def boom(method, params):
            raise AssertionError("rpc should not be called")
        out = tape.trades(POOL, MINT, gt=lambda p, t: _gt_ok(), rpc=boom, sol_usd=150)
        self.assertTrue(out["available"])
        self.assertEqual(out["source"], "geckoterminal")
        self.assertEqual(out["trades"][0]["sig"], "gtsig")
        self.assertNotIn("stale", out)

    def test_both_down_is_rate_limited_not_an_exception(self):
        def rpc(method, params):
            if method == "getSignaturesForAddress":
                raise RuntimeError("RPC busy")
            return None
        out = tape.trades(POOL, MINT, gt=lambda p, t: _gt_429(), rpc=rpc, sol_usd=150)
        self.assertFalse(out["available"])
        self.assertEqual(out["trades"], [])
        self.assertEqual(out["traders"], [])
        self.assertIn("rate-limited", out["reason"])

    def test_stale_gecko_kept_when_rpc_has_no_priced_swaps(self):
        stale = {**_gt_ok(), "stale": True, "stale_reason": "HTTP 429"}
        def rpc(method, params):
            if method == "getSignaturesForAddress":
                return [{"signature": SIG, "err": None}]
            return _tx(1000, 1000, None, None, None)  # no token change, no price
        out = tape.trades(POOL, MINT, gt=lambda p, t: stale, rpc=rpc, sol_usd=None)
        self.assertTrue(out["available"])
        self.assertTrue(out["stale"])
        self.assertEqual(out["source"], "geckoterminal")
        self.assertEqual(out["trades"][0]["sig"], "gtsig")

    def test_single_flight_one_upstream_for_concurrent_viewers(self):
        n = {"gt": 0}
        gate = threading.Barrier(6)

        def gt(pool, token):
            n["gt"] += 1
            time.sleep(0.15)
            return _gt_429()

        rpc_n = {"n": 0}
        lock = threading.Lock()

        def rpc(method, params):
            with lock:
                rpc_n["n"] += 1
            if method == "getSignaturesForAddress":
                return [{"signature": SIG, "err": None, "blockTime": 1730000000}]
            return _tx(1000, 1500, WSOL, 2.0, 1.5)

        outs = []

        def worker():
            gate.wait()
            outs.append(tape.trades(POOL, MINT, gt=gt, rpc=rpc, sol_usd=150))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(len(outs), 6)
        self.assertEqual(n["gt"], 1)
        self.assertEqual(rpc_n["n"], 2)  # one signatures + one transaction, not 6 of each
        self.assertTrue(all(o["available"] and o["source"] == "solana_rpc" and o["trades"][0]["usd"] == 75.0 for o in outs))

    def test_fresh_cache_skips_upstream(self):
        n = {"n": 0}

        def gt(pool, token):
            n["n"] += 1
            return _gt_ok()

        tape.trades(POOL, MINT, gt=gt, rpc=lambda *a: None, sol_usd=1)
        out = tape.trades(POOL, MINT, gt=gt, rpc=lambda *a: None, sol_usd=1)
        self.assertEqual(n["n"], 1)
        self.assertEqual(out["trades"][0]["sig"], "gtsig")
        self.assertNotIn("stale", out)

    def test_stale_while_revalidate_returns_immediately_and_refreshes_once(self):
        n = {"n": 0}
        second = threading.Event()

        def gt(pool, token):
            n["n"] += 1
            if n["n"] >= 2:
                second.set()
            return _gt_ok()

        tape.trades(POOL, MINT, gt=gt, rpc=lambda *a: (_ for _ in ()).throw(AssertionError("rpc")), sol_usd=1)
        with tape._lock:
            tape._cache[POOL]["ts"] -= 30
            tape._cache[POOL]["retry_at"] = time.time() + 3600
        out = tape.trades(POOL, MINT, gt=gt, rpc=lambda *a: None, sol_usd=1)
        self.assertTrue(out["stale"])
        self.assertTrue(out["cached"])
        self.assertEqual(n["n"], 1)
        with tape._lock:
            tape._cache[POOL]["retry_at"] = time.time() - 1
        t0 = time.perf_counter()
        out = tape.trades(POOL, MINT, gt=gt, rpc=lambda *a: None, sol_usd=1)
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertTrue(out["stale"])
        self.assertTrue(second.wait(2))
        time.sleep(0.05)
        self.assertEqual(n["n"], 2)


if __name__ == "__main__":
    unittest.main()
