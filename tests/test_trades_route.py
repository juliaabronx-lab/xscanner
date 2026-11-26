"""The /api/trades route is the tape module, not trader.trades directly.

Env is set before importing app so this does not start the live scan or Pulse.
"""
import os, time, unittest
os.environ["LIVE_SCAN"] = "0"
os.environ["PULSE"] = "0"
os.environ["PRICEHIST"] = "0"
os.environ["SELF_PING"] = "0"
os.environ["LIVE_DIR"] = "/tmp/xscanner-test-tape"
os.environ["PUBLIC_READONLY"] = "1"

import sol_rpc
import trader
import trades_tape

POOL = "FcsUythNUWbfT7RP2yuFp1kiWjft15du9WtgLUrrqx3p"
MINT = "9cmXfuC26anZtH9AUWVzpUxU1UJ7PPogYxftYTepump"
USER = "7VfU1sC4vYkQ2mE4rT3wL8nB6pH4aJxD5cF2gS8uZ1yK"
SIG = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW"


def _tx():
    return {
        "blockTime": 1730000000,
        "transaction": {"signatures": [SIG], "message": {"accountKeys": [
            {"pubkey": USER, "signer": True, "writable": True},
            {"pubkey": POOL, "signer": False, "writable": True},
        ]}},
        "meta": {
            "err": None, "fee": 0,
            "preBalances": [2_000_000_000, 0], "postBalances": [1_500_000_000, 0],
            "preTokenBalances": [
                {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": 1000.0}},
                {"mint": trades_tape.WSOL, "owner": USER, "uiTokenAmount": {"uiAmount": 2.0}},
            ],
            "postTokenBalances": [
                {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": 1500.0}},
                {"mint": trades_tape.WSOL, "owner": USER, "uiTokenAmount": {"uiAmount": 1.5}},
            ],
        },
    }


class RouteTests(unittest.TestCase):
    def setUp(self):
        trades_tape.reset()
        self._trades = trader.trades
        self._rpc = sol_rpc.call
        self.rpc_calls = []

    def tearDown(self):
        trader.trades = self._trades
        sol_rpc.call = self._rpc
        trades_tape.reset()

    def test_http_429_from_gecko_returns_rpc_tape(self):
        trader.trades = lambda pool, token=None: {
            "available": False,
            "reason": "Trades tape unavailable right now (HTTP 429).",
            "trades": [], "traders": [],
        }

        def call(method, params, timeout=8, gap=None):
            self.rpc_calls.append(method)
            if method == "getSignaturesForAddress":
                return [{"signature": SIG, "err": None, "blockTime": 1730000000}]
            if method == "getTransaction":
                return _tx()
            raise AssertionError(method)

        sol_rpc.call = call
        trades_tape._sol.update(usd=150.0, t=time.time())
        import app as appmod
        from fastapi.testclient import TestClient
        with TestClient(appmod.app) as client:
            r = client.get(f"/api/trades/{POOL}?token={MINT}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["source"], "solana_rpc")
        self.assertEqual(body["trades"][0]["k"], "buy")
        self.assertEqual(body["trades"][0]["usd"], 75.0)
        self.assertEqual(body["trades"][0]["w"], USER)
        self.assertEqual(self.rpc_calls, ["getSignaturesForAddress", "getTransaction"])
        self.assertIn("Solana RPC", body["note"])


if __name__ == "__main__":
    unittest.main()
