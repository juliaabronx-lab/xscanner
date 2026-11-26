"""Measurement-only urllib hook. Loaded via PYTHONPATH for the cold-boot harness.

Not part of the app. Counts every outbound urlopen and, when MEASURE_MODE is
sim, returns synthetic bodies instead of touching the network. Localhost and
the public site are passed through so the harness can call the app.
"""
import io, json, os, threading, time, urllib.error, urllib.request

_ORIG = urllib.request.urlopen
_LOCK = threading.Lock()
_LOG = os.environ.get("MEASURE_LOG", "")
_T0 = float(os.environ.get("MEASURE_T0", "0") or 0)
_MODE = os.environ.get("MEASURE_MODE", "sim-open")  # sim-open | sim-gt429
_GT_OK = int(os.environ.get("MEASURE_GT_OK", "4") or 4)
_gt_n = 0

POOL = "FcsUythNUWbfT7RP2yuFp1kiWjft15du9WtgLUrrqx3p"
MINT = "9cmXfuC26anZtH9AUWVzpUxU1UJ7PPogYxftYTepump"
USER = "7VfU1sC4vYkQ2mE9rT3wL8nB6pH4aJxD5cF2gS8uZ1yK"
WSOL = "So11111111111111111111111111111111111111112"
SIG = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW"


def _host(url):
    try:
        return url.split("/")[2].lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def _log(rec):
    if not _LOG:
        return
    line = json.dumps(rec, separators=(",", ":"))
    with _LOCK:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class _Resp(io.BytesIO):
    def __init__(self, raw, status=200):
        super().__init__(raw if isinstance(raw, bytes) else raw.encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status


def _ok(obj):
    return _Resp(json.dumps(obj).encode())


def _tx(pool):
    return {
        "blockTime": 1730000000,
        "slot": 250000000,
        "transaction": {
            "signatures": [SIG],
            "message": {
                "accountKeys": [
                    {"pubkey": USER, "signer": True, "writable": True},
                    {"pubkey": pool, "signer": False, "writable": True},
                ]
            },
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [2_000_000_000, 1_000_000_000],
            "postBalances": [1_500_000_000, 1_499_995_000],
            "preTokenBalances": [
                {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": 1000.0, "decimals": 6}},
                {"mint": MINT, "owner": pool, "uiTokenAmount": {"uiAmount": 500000.0, "decimals": 6}},
                {"mint": WSOL, "owner": USER, "uiTokenAmount": {"uiAmount": 2.0, "decimals": 9}},
                {"mint": WSOL, "owner": pool, "uiTokenAmount": {"uiAmount": 10.0, "decimals": 9}},
            ],
            "postTokenBalances": [
                {"mint": MINT, "owner": USER, "uiTokenAmount": {"uiAmount": 1500.0, "decimals": 6}},
                {"mint": MINT, "owner": pool, "uiTokenAmount": {"uiAmount": 499500.0, "decimals": 6}},
                {"mint": WSOL, "owner": USER, "uiTokenAmount": {"uiAmount": 1.5, "decimals": 9}},
                {"mint": WSOL, "owner": pool, "uiTokenAmount": {"uiAmount": 10.5, "decimals": 9}},
            ],
        },
    }


def _rpc(data):
    try:
        body = json.loads(data or b"{}")
    except Exception:
        body = {}
    method = body.get("method")
    params = body.get("params") or []
    if method == "getSignaturesForAddress":
        result = [{"signature": SIG, "slot": 250000000, "err": None, "blockTime": 1730000000}]
    elif method == "getTransaction":
        result = _tx(POOL)
    elif method == "getMultipleAccounts":
        result = {"value": []}
    else:
        result = None
    return _ok({"jsonrpc": "2.0", "id": 1, "result": result})


def _body_for(url, host, data):
    if "solana" in host or host.endswith("publicnode.com"):
        return _rpc(data)
    if "lite-api.jup.ag" in host and "/price/" in url:
        return _ok({WSOL: {"usdPrice": 150.0}, MINT: {"usdPrice": 0.15}})
    if "datapi.jup.ag" in host:
        return _ok({"candles": []})
    if "geckoterminal.com" in host and "/trades" in url:
        return _ok({"data": []})
    if "geckoterminal.com" in host and "/ohlcv/" in url:
        return _ok({"data": {"attributes": {"ohlcv_list": []}}, "meta": {}})
    if "geckoterminal.com" in host:
        return _ok({"data": [], "included": []})
    if "dexscreener.com" in host:
        if "/tokens/v1/" in url:
            return _ok([])
        return _ok({"pairs": []})
    if "rugcheck.xyz" in host:
        return _ok({"mint": MINT, "score_normalised": 1, "risks": [], "topHolders": [], "markets": []})
    return _ok({})


def _wrapped(url, data=None, timeout=None, *args, **kwargs):
    global _gt_n
    req = url if isinstance(url, urllib.request.Request) else None
    u = req.full_url if req is not None else str(url)
    payload = data if data is not None else (req.data if req is not None else None)
    host = _host(u)
    if host in ("127.0.0.1", "localhost", "0.0.0.0") or host.endswith(".onrender.com"):
        return _ORIG(url, data, timeout, *args, **kwargs)
    t = time.time() - _T0 if _T0 else time.time()
    status = 200
    try:
        if "geckoterminal.com" in host and _MODE == "sim-gt429":
            with _LOCK:
                _gt_n += 1
                n = _gt_n
            if n > _GT_OK:
                status = 429
                raise urllib.error.HTTPError(u, 429, "rate limited", None, io.BytesIO(b""))
        if timeout is None:
            resp = _body_for(u, host, payload)
        else:
            resp = _body_for(u, host, payload)
        return resp
    except urllib.error.HTTPError:
        raise
    finally:
        path = u.split("/", 3)[-1][:180] if "://" in u else u[:180]
        _log({"t": round(t, 3), "pid": os.getpid(), "thr": threading.current_thread().name,
              "host": host, "status": status, "path": path})


urllib.request.urlopen = _wrapped
_log({"t": 0 if not _T0 else round(time.time() - _T0, 3), "pid": os.getpid(), "thr": "sitecustomize",
      "host": "", "status": 0, "path": "loaded"})
