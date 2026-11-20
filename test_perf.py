"""Speed-path tests: single-flight HTTP, parallel funding, paced vs fast GeckoTerminal,
static cache headers, Pulse SSE, and a live-scan that overlaps RugCheck with DexScreener.

No real network. No wallets, signing, or trading.
"""
import json, os, threading, time, unittest
from urllib.error import HTTPError

os.environ.setdefault("SCANNER_DIR", os.path.join(os.path.dirname(__file__), "deploy", "data"))
os.environ.setdefault("LEDGER_PATH", os.path.join(os.path.dirname(__file__), "deploy", "data", "ledger.json"))
os.environ.setdefault("PUBLIC_READONLY", "1")
os.environ.setdefault("LIVE_SCAN", "0")
os.environ.setdefault("PULSE", "0")
os.environ.setdefault("SELF_PING", "0")
os.environ.setdefault("PRICEHIST", "0")
os.environ.setdefault("LIVE_DIR", "/tmp/xscanner-perf-test")
os.environ.setdefault("LOOKUP_CACHE", "/tmp/xscanner-perf-test/lookup_last.json")
os.environ.setdefault("CONF_CACHE", "/tmp/xscanner-perf-test/confidence_last.json")
os.environ.setdefault("BUNDLE_CACHE", "/tmp/xscanner-perf-test/bundle_last.json")
os.makedirs("/tmp/xscanner-perf-test", exist_ok=True)

import net
import bundle
import confidence
import lookup


MINT = "6MKD1NkLvWRqdbFtqmgACBwUV5o6GJpZry8HkNgzpump"


def _pair(mint=MINT):
    return {
        "chainId": "solana", "dexId": "pumpswap", "pairAddress": "Pair1111111111111111111111111111111111111",
        "baseToken": {"address": mint, "name": "Test Coin", "symbol": "TEST"},
        "quoteToken": {"symbol": "SOL"}, "priceUsd": "0.001",
        "liquidity": {"usd": 12000}, "volume": {"h24": 5000, "h1": 400},
        "priceChange": {"m5": 1, "h1": 2, "h6": 0, "h24": 3},
        "txns": {"m5": {"buys": 10, "sells": 8}, "h1": {"buys": 80, "sells": 70}, "h24": {"buys": 200, "sells": 180}},
        "marketCap": 40000, "fdv": 40000, "pairCreatedAt": int(time.time() * 1000) - 3_600_000,
        "boosts": {"active": 0}, "info": {"socials": []},
    }


def _report(mint=MINT):
    return {"mint": mint, "score_normalised": 4, "risks": [], "topHolders": [], "markets": [],
            "token": {"supply": 1_000_000_000, "decimals": 6}, "totalHolders": 12, "rugged": False}


def _reset_net():
    with net._lock:
        net._text.clear(); net._cool.clear(); net._flight.clear()
        net.stats.update(fetches=0, hits=0, joined=0, cooldowns=0, errors=0)


class NetTests(unittest.TestCase):
    def tearDown(self):
        _reset_net()

    def test_single_flight_one_upstream_call(self):
        _reset_net()
        gate = threading.Event()
        def fake(url, timeout, headers):
            gate.wait(2)
            body = '{"ok": true}'
            with net._lock:
                net._remember(url, 200, body)
            return json.loads(body), 200
        calls = {"n": 0}
        def counting(url, timeout, headers):
            calls["n"] += 1
            return fake(url, timeout, headers)
        orig = net._fetch
        net._fetch = counting
        try:
            errs = []
            def call():
                try:
                    self.assertEqual(net.get_json("https://example.test/a"), {"ok": True})
                except Exception as e:
                    errs.append(e)
            threads = [threading.Thread(target=call) for _ in range(8)]
            for t in threads: t.start()
            time.sleep(0.05)
            t0 = time.perf_counter()
            gate.set()
            for t in threads: t.join(2)
            wall = time.perf_counter() - t0
        finally:
            net._fetch = orig
        self.assertEqual(errs, [])
        self.assertEqual(calls["n"], 1)
        self.assertGreaterEqual(net.stats["joined"], 1)
        self.assertLess(wall, 0.5)

    def test_429_cools_host_and_does_not_call_again(self):
        _reset_net()
        def fake(url, timeout, headers):
            raise HTTPError(url, 429, "rate", {"Retry-After": "30"}, None)
        orig = net._fetch
        net._fetch = orig  # real _fetch is what records the cool-down; drive urlopen instead
        calls = {"n": 0}
        def urlopen(req, timeout=None):
            calls["n"] += 1
            raise HTTPError(req.full_url, 429, "rate", {"Retry-After": "30"}, None)
        orig_open = net.urlopen
        net.urlopen = urlopen
        try:
            a = net.get_json("https://limited.test/x", errors="dict")
            b = net.get_json("https://limited.test/y", errors="dict")
        finally:
            net.urlopen = orig_open
        self.assertIn("429", a.get("_error", ""))
        self.assertIn("429", b.get("_error", ""))
        self.assertEqual(calls["n"], 1)
        self.assertGreaterEqual(net.stats["cooldowns"], 1)
        with self.assertRaises(net.UpstreamError) as ctx:
            net.get_json("https://limited.test/z", errors="raise")
        self.assertEqual(ctx.exception.code, 429)


class FundingTests(unittest.TestCase):
    def test_parallel_funding_is_faster_and_same_verdict(self):
        import sol_rpc
        orig = sol_rpc.first_funding
        def slow(owner, max_sigs=150, gap=None):
            time.sleep(0.12)
            return ("FunderWallet111111111111111111111111111", 10)
        sol_rpc.first_funding = slow
        try:
            owners = [(f"Owner{i}xxxxxxxxxxxxxxxxxxxxxxxxxxx", 1.0) for i in range(8)]
            a = {"owners": list(owners)}
            t0 = time.perf_counter()
            serial = bundle.funding_check(a, budget_s=10, workers=1)
            serial_s = time.perf_counter() - t0
            b = {"owners": list(owners)}
            t0 = time.perf_counter()
            parallel = bundle.funding_check(b, budget_s=10, workers=4)
            parallel_s = time.perf_counter() - t0
        finally:
            sol_rpc.first_funding = orig
        self.assertEqual(serial["resolved"], 8)
        self.assertEqual(parallel["resolved"], 8)
        self.assertEqual(serial["largest"], parallel["largest"])
        self.assertLess(parallel_s, serial_s * 0.55)
        # recorded so the perf report can quote a measured ratio, not a guess
        self._recorded = {"serial_s": round(serial_s, 3), "parallel_s": round(parallel_s, 3)}
        open("/tmp/xscanner-perf-test/funding.json", "w").write(json.dumps(self._recorded))


class GeckoPaceTests(unittest.TestCase):
    def test_fast_path_does_not_sleep_between_calls(self):
        orig_gap, orig_get = confidence.GT_GAP_S, confidence._get
        confidence.GT_GAP_S = 0.35
        confidence._state["gt_until"] = 0
        confidence._state["gt_last"] = 0
        def fake(url, timeout=10):
            return {"data": {"attributes": {"price_usd": "1", "decimals": 6}}}
        confidence._get = fake
        try:
            t0 = time.perf_counter()
            fast = confidence.pull_gt(MINT, timeout=2, pace=False)
            fast_s = time.perf_counter() - t0
            confidence._state["gt_last"] = 0
            confidence._state["gt_until"] = 0
            t0 = time.perf_counter()
            slow = confidence.pull_gt(MINT, timeout=2, pace=True)
            slow_s = time.perf_counter() - t0
        finally:
            confidence.GT_GAP_S = orig_gap
            confidence._get = orig_get
            confidence._state["gt_last"] = 0
            confidence._state["gt_until"] = 0
        self.assertIsNotNone(fast)
        self.assertIsNotNone(slow)
        self.assertEqual(fast["price"], slow["price"])
        self.assertLess(fast_s, 0.2)
        self.assertGreater(slow_s, 0.3)


class LookupOverlapTests(unittest.TestCase):
    def test_rugcheck_overlaps_dex_and_shape_is_stable(self):
        _reset_net()
        lookup._xcache.update(sig=None, raw=None, enriched=None)
        calls = []
        def fake(url, timeout, headers):
            calls.append(url)
            time.sleep(0.25 if "rugcheck.xyz" in url and url.endswith("/report") else 0.12)
            if "rugcheck.xyz" in url and url.endswith("/report"):
                body = json.dumps(_report())
            elif "/search" in url:
                body = '{"pairs":[]}'
            elif "/tokens/v1/" in url:
                body = json.dumps([_pair()])
            else:
                body = '{"schemaVersion":"1.0.0","pairs":null}'
            with net._lock:
                net._remember(url, 200, body)
            return json.loads(body), 200
        orig = net._fetch
        net._fetch = fake
        lookup.ON_MISS.clear()
        try:
            t0 = time.perf_counter()
            c = lookup.live_scan(MINT, deadline=time.time() + 8)
            wall = time.perf_counter() - t0
        finally:
            net._fetch = orig
        self.assertEqual(c["symbol"], "TEST")
        self.assertIsInstance(c["score"], (int, float))
        self.assertIn("flags", c)
        self.assertFalse(c["partial"])
        rc_calls = [u for u in calls if MINT in u and "rugcheck.xyz" in u and u.rstrip("/").endswith("/report")]
        # Prefetch and rugcheck() share one flight. A second call is a cache hit and is not listed.
        # (Other mints can appear here when the app's background bundle worker is running.)
        self.assertEqual(len(rc_calls), 1, calls)
        # Serial DexScreener + search + RugCheck sleeps would be 0.12+0.12+0.25 = 0.49s before local work.
        # Overlap keeps the wall under that.
        self.assertLess(wall, 0.48, calls)
        open("/tmp/xscanner-perf-test/overlap.json", "w").write(json.dumps({"wall_s": round(wall, 3), "calls": len(calls)}))


class AppSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as appmod
        cls.appmod = appmod
        # Don't let the background bundle worker spend real RugCheck calls during these tests.
        bundle._backoff_until = time.time() + 10**9
        from fastapi.testclient import TestClient
        cls.client = TestClient(appmod.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_static_cache_headers(self):
        r = self.client.get("/static/app.js?v=fix8")
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers.get("cache-control", ""))
        img = self.client.get("/static/logo.svg")
        self.assertEqual(img.status_code, 200)
        self.assertIn("max-age=86400", img.headers.get("cache-control", ""))
        self.assertNotIn("immutable", img.headers.get("cache-control", ""))

    def test_pulse_stream_same_shape_as_poll(self):
        poll = self.client.get("/api/pulse").json()
        with self.client.stream("GET", "/api/pulse/stream") as r:
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/event-stream", r.headers["content-type"])
            got = None
            for line in r.iter_lines():
                if not line:
                    continue
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("data:"):
                    got = json.loads(text[5:])
                    break
        self.assertIsNotNone(got)
        for k in ("ver", "h", "rows", "counts", "sources"):
            self.assertIn(k, got)
        self.assertEqual(got["ver"], poll["ver"])

    def test_lookup_endpoint_keeps_contract(self):
        _reset_net()
        def fake(url, timeout, headers):
            if "rugcheck.xyz" in url and url.endswith("/report"):
                body = json.dumps(_report())
            elif "/search" in url:
                body = '{"pairs":[]}'
            elif "/tokens/v1/" in url:
                body = json.dumps([_pair()])
            else:
                body = '{"pairs":[]}'
            with net._lock:
                net._remember(url, 200, body)
            return json.loads(body), 200
        orig = net._fetch
        net._fetch = fake
        self.appmod.bd.fetch_now = lambda *a, **k: False
        self.appmod.cf.fetch_now = lambda *a, **k: False
        lookup._cache.pop(MINT, None)
        try:
            r = self.client.get("/api/lookup/" + MINT)
        finally:
            net._fetch = orig
        self.assertEqual(r.status_code, 200, r.text[:400])
        j = r.json()
        for k in ("score", "symbol", "mint", "flags", "lookup", "bundle", "data_confidence", "tier"):
            self.assertIn(k, j)
        self.assertEqual(j["symbol"], "TEST")
        self.assertEqual(j["lookup"]["source"], "live")
        self.assertIn("cached", j["lookup"])


if __name__ == "__main__":
    unittest.main()
