"""Tiny read-only Solana JSON-RPC client for xScanner (keyless public endpoints, GET-like reads only).

Endpoints tried in order: $SOLANA_RPC_URL (optional, set in the host env only, never in the repo), then
publicnode, then the official mainnet-beta endpoint. Each endpoint has its own back-off after 429/403/5xx
(30 s doubling to 10 min). A global minimum gap between calls keeps us polite. Calls raise RpcError on failure so callers
degrade to "unavailable" instead of blocking. Methods used: getAccountInfo, getMultipleAccounts,
getSignaturesForAddress, getTransaction. No signing, no sending, no wallets.
"""
import json, os, threading, time, urllib.error, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (xScanner read-only)", "Content-Type": "application/json", "Accept": "application/json"}
GAP_S = float(os.environ.get("SOL_RPC_GAP_S", "0.12"))
_lock = threading.Lock()
_last = 0.0
_down = {}      # url -> (until_epoch, n)
stats = {"calls": 0, "errors": 0, "last_error": None, "last_ok_url": None}


class RpcError(Exception):
    pass


def endpoints():
    eps = []
    env = os.environ.get("SOLANA_RPC_URL", "").strip()
    if env.startswith("https://"): eps.append(env)
    eps += ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]
    return eps


def call(method, params, timeout=8):
    global _last
    last_err = "no endpoint available"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in endpoints():
        with _lock:
            until, n = _down.get(url, (0, 0))
            if time.time() < until: continue
            wait = _last + GAP_S - time.time()
            _last = time.time() + max(0.0, wait)
        if wait > 0: time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=body, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            stats["calls"] += 1
            if isinstance(d, dict) and d.get("error"):
                err = d["error"]
                code = err.get("code") if isinstance(err, dict) else None
                if code == 429: raise urllib.error.HTTPError(url, 429, "rate limited", None, None)
                last_err = f"rpc error {code}"
                continue
            stats["last_ok_url"] = url.split("/")[2]
            with _lock: _down.pop(url, None)
            return d.get("result")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (429, 403) or e.code >= 500:
                with _lock:
                    n = min(n + 1, 5); _down[url] = (time.time() + min(30 * 2 ** (n - 1), 600), n)
        except Exception as e:
            last_err = type(e).__name__
            with _lock:
                n = min(n + 1, 5); _down[url] = (time.time() + min(15 * 2 ** (n - 1), 300), n)
    stats["errors"] += 1; stats["last_error"] = last_err
    raise RpcError(last_err)


def mint_info(mint):
    """-> {supply_raw, decimals, mint_auth, freeze_auth, owner_program} straight from the chain."""
    r = call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    v = (r or {}).get("value")
    if not v: raise RpcError("mint account not found")
    info = ((v.get("data") or {}).get("parsed") or {}).get("info") or {}
    if "supply" not in info: raise RpcError("not a token mint")
    return {"supply_raw": float(info["supply"]), "decimals": int(info.get("decimals", 0)),
            "mint_auth": info.get("mintAuthority"), "freeze_auth": info.get("freezeAuthority"),
            "program": v.get("owner")}


def token_balances(accounts):
    """Chain balances (raw units) for up to 100 token accounts via ONE getMultipleAccounts call -> {account: raw_amount}.
    (getTokenLargestAccounts is not served by the free public endpoints, so we verify the holder list another
    source reported instead of discovering our own.)"""
    accounts = list(accounts)[:100]
    if not accounts: return {}
    r = call("getMultipleAccounts", [accounts, {"encoding": "jsonParsed"}])
    out = {}
    for addr, v in zip(accounts, (r or {}).get("value") or []):
        info = (((v or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        ta = info.get("tokenAmount") or {}
        if "amount" in ta: out[addr] = float(ta["amount"])
    return out


def first_funding(owner, max_sigs=150):
    """Who created/funded this wallet: look at its OLDEST transaction. Returns (funder, slot) or None when the wallet has
    more history than max_sigs (an old, active wallet is not a fresh bundler wallet)."""
    sigs = call("getSignaturesForAddress", [owner, {"limit": max_sigs}]) or []
    if not sigs or len(sigs) >= max_sigs: return None
    old = sigs[-1]
    tx = call("getTransaction", [old["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not tx: return None
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in (msg.get("accountKeys") or [])]
    payer = keys[0] if keys else None
    funder = payer
    for ix in msg.get("instructions") or []:
        p = ix.get("parsed")
        if isinstance(p, dict) and p.get("type") in ("transfer", "createAccount") and (p.get("info") or {}).get("destination", p.get("info", {}).get("newAccount")) == owner:
            funder = (p.get("info") or {}).get("source") or funder
            break
    if funder == owner: return None   # self-funded first tx tells us nothing
    return funder, old.get("slot") or tx.get("slot")
