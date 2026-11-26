"""Per-host pacing: startup budget, interactive reserve, 429 clocks stay apart."""
import os, tempfile, threading, time, unittest, urllib.error, urllib.request
from io import BytesIO

import net
import pace


GT = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
DEX = "https://api.dexscreener.com/latest/dex/tokens/mint"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


class PaceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self._env = os.environ.get("PACE_STATE")
        self._urlopen = urllib.request.urlopen
        pace.reset(memory=True, now=self.clock.now, sleep=self.clock.sleep, jitter=False)
        pace._uninstalled_for_tests()

    def tearDown(self):
        pace._uninstalled_for_tests()
        urllib.request.urlopen = self._urlopen
        pace.reset(memory=True, jitter=False)
        if self._env is None:
            os.environ.pop("PACE_STATE", None)
        else:
            os.environ["PACE_STATE"] = self._env

    def test_background_burst_then_startup_gap_on_gecko(self):
        # capacity 3, reserve 1: two background calls fit, the third waits out a refill
        pace.before_request(GT, "bg")
        pace.before_request(GT, "bg")
        self.assertEqual(self.clock.t, 1000.0)
        pace.before_request(GT, "bg")
        pol = pace.HOSTS["api.geckoterminal.com"]
        gap = 1.0 / (pol["rate"] * pol["startup_scale"])
        self.assertGreaterEqual(self.clock.t - 1000.0, gap - 0.05)
        self.assertLess(self.clock.t - 1000.0, gap + 1.0)

    def test_user_does_not_wait_on_background_cooldown_or_reserve(self):
        pace.note_status(GT, 429, "bg")
        t = self.clock.t
        pace.before_request(GT, "user")
        self.assertEqual(self.clock.t, t)
        self.assertGreater(pace.snapshot()["hosts"]["api.geckoterminal.com"]["bg_cool_s"], 0)
        self.assertEqual(pace.snapshot()["hosts"]["api.geckoterminal.com"]["user_cool_s"], 0)
        pace.before_request(GT, "bg")
        self.assertGreaterEqual(self.clock.t - t, 8.0)
        self.assertEqual(pace.counters["user_wait_s"], 0.0)
        self.assertGreater(pace.counters["bg_wait_s"], 0.0)

    def test_user_429_does_not_cool_background(self):
        pace.note_status(GT, 429, "user")
        snap = pace.snapshot()["hosts"]["api.geckoterminal.com"]
        self.assertGreater(snap["user_cool_s"], 0)
        self.assertEqual(snap["bg_cool_s"], 0)
        t = self.clock.t
        pace.before_request(GT, "bg")
        self.assertEqual(self.clock.t, t)

    def test_user_can_spend_the_reserve_immediately(self):
        pace.before_request(GT, "bg")
        pace.before_request(GT, "bg")
        t = self.clock.t
        pace.before_request(GT, "user")
        self.assertEqual(self.clock.t, t)
        self.assertEqual(pace.counters["user_grant"], 1)

    def test_startup_thread_offset_is_once(self):
        pace.reset(memory=True, now=self.clock.now, sleep=self.clock.sleep, jitter=True)
        me = threading.current_thread()
        old = me.name
        me.name = "pulse"
        try:
            pace.before_request(GT, "bg")
            self.assertGreaterEqual(self.clock.t - 1000.0, pace.THREAD_OFFSET_S["pulse"])
            mark = self.clock.t
            pace.before_request(GT, "bg")
            # second call is inside the burst (capacity), so no second offset
            self.assertEqual(self.clock.t, mark)
        finally:
            me.name = old

    def test_dex_startup_burst_fits_a_scan(self):
        # 40 background DexScreener GETs is one scan-sized burst. It has to
        # finish well inside the 150s live-scan timeout, and a lookup still
        # gets a token without waiting behind that sweep.
        for _ in range(40):
            pace.before_request(DEX, "bg")
        # steady rate would finish this burst in ~37s; the startup scale must
        # stretch it past that, and still keep it under the 150s scan timeout
        self.assertGreaterEqual(self.clock.t - 1000.0, 48.0)
        self.assertLess(self.clock.t - 1000.0, 90.0)
        t = self.clock.t
        pace.before_request(DEX, "user")
        self.assertEqual(self.clock.t, t)

    def test_file_bucket_survives_a_second_caller(self):
        fd, path = tempfile.mkstemp(prefix="pace-", suffix=".json")
        os.close(fd)
        os.environ["PACE_STATE"] = path
        try:
            pace.reset(memory=False, now=self.clock.now, sleep=self.clock.sleep, jitter=False)
            pace.before_request(GT, "bg")
            pace._memory = {"hosts": {}, "started_at": None}
            pace.before_request(GT, "bg")
            snap = pace.snapshot()["hosts"]["api.geckoterminal.com"]
            # started at capacity 3, two background grants, one left (the reserve)
            self.assertAlmostEqual(snap["tokens"], 1.0, places=3)
        finally:
            os.remove(path)

    def test_install_records_background_429_and_lets_user_through(self):
        seen = []

        def orig(url, data=None, timeout=None, *args, **kwargs):
            seen.append(str(url))
            raise urllib.error.HTTPError(str(url), 429, "rate limited", None, BytesIO(b""))

        urllib.request.urlopen = orig
        pace._installed = False
        pace.install()
        try:
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(GT, timeout=1)
        finally:
            # swap the origin so the user call succeeds, without re-wrapping
            def ok(url, data=None, timeout=None, *args, **kwargs):
                seen.append("ok")

                class R:
                    status = 200
                    def read(self):
                        return b"{}"
                return R()

            pace._orig_urlopen = ok
        self.assertEqual(pace.counters["bg_429"], 1)
        tok = pace.PRIORITY.set("user")
        try:
            t = self.clock.t
            urllib.request.urlopen(GT, timeout=1)
            self.assertEqual(self.clock.t, t)
        finally:
            pace.PRIORITY.reset(tok)
        self.assertIn("ok", seen)

    def test_worker_thread_does_not_inherit_user_priority(self):
        # A request that starts the confidence worker copies its contextvars.
        # That worker must still sit on the background cooldown.
        pace.note_status(GT, 429, "bg")
        me = threading.current_thread()
        old = me.name
        me.name = "confidence-worker"
        tok = pace.PRIORITY.set("user")
        try:
            t = self.clock.t
            pace.before_request(GT)
            self.assertGreaterEqual(self.clock.t - t, 8.0)
        finally:
            pace.PRIORITY.reset(tok)
            me.name = old

    def test_request_thread_stays_interactive(self):
        pace.note_status(GT, 429, "bg")
        me = threading.current_thread()
        old = me.name
        me.name = "AnyIO worker thread"
        tok = pace.PRIORITY.set("user")
        try:
            t = self.clock.t
            pace.before_request(GT)
            self.assertEqual(self.clock.t, t)
        finally:
            pace.PRIORITY.reset(tok)
            me.name = old

    def test_net_uses_the_urlopen_installed_after_import(self):
        # net used to bind urlopen at import, so pace.install() never saw
        # confidence / bundle / lookup calls. A wrapper installed later must run.
        seen = []

        def fake(url, data=None, timeout=None, *args, **kwargs):
            seen.append(getattr(url, "full_url", str(url)))

            class R:
                status = 200
                def read(self):
                    return b'{"pairs": []}'
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    return False
            return R()

        old = urllib.request.urlopen
        urllib.request.urlopen = fake
        try:
            net.get_json("https://api.dexscreener.com/latest/dex/tokens/pace-bind-check", timeout=1, fresh_s=0)
        finally:
            urllib.request.urlopen = old
        self.assertEqual(seen, ["https://api.dexscreener.com/latest/dex/tokens/pace-bind-check"])

    def test_localhost_and_render_are_not_paced(self):
        pace.before_request("http://127.0.0.1:8000/api/health", "bg")
        pace.before_request("https://xscanner-c8jo.onrender.com/api/health", "bg")
        self.assertEqual(self.clock.t, 1000.0)
        self.assertEqual(pace.counters["bg_grant"], 0)


if __name__ == "__main__":
    unittest.main()
