"""Token image resolver + proxy cache for xScanner (read-only market metadata, no wallets/trading).

Resolution order per mint (first hit wins):
  1. DexScreener token pairs API  -> pairs[].info.imageUrl   (batched: up to 30 mints per call)
  2. GeckoTerminal tokens/multi   -> data.attributes.image_url (batched: up to 30 mints per call)
  3. pump.fun frontend API        -> image_uri               (often Cloudflare-blocked; tried quietly)
  4. On-chain metadata URI (via RugCheck tokenMeta.uri) -> JSON .image
  5. RugCheck fileMeta.image
  6. generated letter avatar (SVG)
The image-URL map is persisted in data/img_cache.json; the downloaded bytes are re-encoded to a
256px WebP under data/img/<mint>.webp and served from GET /api/img/{mint} with long cache headers.
Both servers (8787/8788) share the files; writes are atomic. Upstream calls are budgeted.
Failed lookups are negative-cached only when every source answered with no image (not on 429/errors);
NEG_TTL is 30 minutes. Placeholder URLs such as missing.png are ignored.
"""
import hashlib, io, ipaddress, json, os, re, socket, threading, time, urllib.error, urllib.parse, urllib.request
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE_PATH = os.path.join(DATA, "img_cache.json")
IMG_DIR = os.path.join(DATA, "img")
B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
UA = "Mozilla/5.0 (X11; Linux x86_64) xScanner/1.0"
NEG_TTL = 30 * 60           # retry a mint with no image after 30 min
META_N, META_WIN = 20, 60   # metadata (DexScreener/GT/pump/RugCheck) calls per minute per process
DL_N = 40                   # image downloads per minute per process
MAX_BYTES = 4 * 1024 * 1024
SIZE = 256
GT_BACKOFF_S = 600          # skip GeckoTerminal for 10 min after a 429

os.makedirs(IMG_DIR, exist_ok=True)
_lock = threading.RLock()
_map = {"mtime": None, "data": {}}
_inflight, _inflight_lock = {}, threading.Lock()
_meta_q, _dl_q, _q_lock = deque(), deque(), threading.Lock()


def valid_mint(m): return bool(B58_RE.match(m or ""))


def _budget(q, n):
    now = time.time()
    with _q_lock:
        while q and now - q[0] > META_WIN: q.popleft()
        if len(q) >= n: return False
        q.append(now); return True


# ---------------------------------------------------------------- persisted map
def _load():
    with _lock:
        try: mt = os.path.getmtime(CACHE_PATH)
        except OSError: return _map["data"]
        if mt != _map["mtime"]:
            try:
                d = json.load(open(CACHE_PATH, encoding="utf-8"))
                if isinstance(d, dict): _map.update(mtime=mt, data=d)
            except Exception: pass
        return _map["data"]


def _save_entry(mint, entry):
    with _lock:
        d = dict(_load()); d[mint] = entry
        tmp = f"{CACHE_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, indent=0, sort_keys=True)
        os.replace(tmp, CACHE_PATH)
        _map.update(mtime=os.path.getmtime(CACHE_PATH), data=d)


def entry(mint): return _load().get(mint)


def file_path(mint): return os.path.join(IMG_DIR, f"{mint}.webp")


def has_file(mint): return os.path.exists(file_path(mint))


# ---------------------------------------------------------------- HTTP helpers
_EGRESS_NET = ipaddress.ip_network("198.18.0.0/15")


def _is_ip_literal(h):
    try: ipaddress.ip_address(h.strip("[]")); return True
    except ValueError: return False


def _safe_url(u):
    """Only public http(s) hosts (blocks SSRF to localhost/private ranges). ipfs:// -> gateway."""
    if not isinstance(u, str) or not u.strip(): return None
    u = u.strip()
    if u.startswith("ipfs://"): u = "https://ipfs.io/ipfs/" + u[7:].lstrip("/").removeprefix("ipfs/")
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname: return None
    try:
        for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip in _EGRESS_NET and not _is_ip_literal(p.hostname): continue   # box egress fake-DNS range
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast: return None
    except Exception:
        return None
    return u


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_url(newurl): raise ValueError("unsafe redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def _get(url, timeout=8, limit=MAX_BYTES, accept="application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with _opener.open(req, timeout=timeout) as r:
        ct = r.headers.get("Content-Type", "")
        data = r.read(limit + 1)
        if len(data) > limit: raise ValueError("too large")
        return data, ct


IPFS_GWS = ("https://gateway.pinata.cloud/ipfs/", "https://ipfs.io/ipfs/")


def _alts(u):
    """Public IPFS gateways often 429; try the given URL then the same CID on other gateways."""
    out = [u]
    m = re.search(r"/ipfs/([A-Za-z0-9]{40,}[^?#]*)", u or "")
    if m:
        for g in IPFS_GWS:
            if g + m.group(1) not in out: out.append(g + m.group(1))
    return out


def _json(url, timeout=8):
    if not _budget(_meta_q, META_N): raise RuntimeError("budget")
    data, _ = _get(url, timeout=timeout, limit=2 * 1024 * 1024)
    return json.loads(data.decode("utf-8", "replace"))


def _ok_img(u):
    """Return a usable image URL, or None for empty / placeholder missing.png links."""
    if not isinstance(u, str) or not u.strip(): return None
    u = u.strip()
    if "missing.png" in u.lower(): return None
    return u


def _unpack_hint(hint):
    """hint is a dict (treated as a complete lookup) or (map, complete)."""
    if isinstance(hint, tuple):
        return hint[0] or {}, bool(hint[1])
    return hint or {}, True


# ---------------------------------------------------------------- resolvers
def ds_batch(mints):
    """DexScreener tokens/v1 accepts up to 30 comma-separated addresses: one call for a whole feed page.

    Returns (map, complete). complete is False if a chunk 429'd or errored (do not negative-cache).
    """
    out, complete = {}, True
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try: pairs = _json("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk))
        except RuntimeError: raise
        except Exception:
            complete = False
            continue
        for p in pairs if isinstance(pairs, list) else []:
            m = (p.get("baseToken") or {}).get("address")
            img = _ok_img((p.get("info") or {}).get("imageUrl"))
            if m in chunk and img and m not in out: out[m] = img
            if m in chunk: out.setdefault(m + ":sym", (p.get("baseToken") or {}).get("symbol"))
    return out, complete


_pump_off = [0.0]
_gt_backoff = [0.0]


def gt_batch(mints):
    """GeckoTerminal tokens/multi accepts up to 30 comma-separated addresses.

    Returns (map, complete). A 429 sets a 10-minute backoff and complete=False (try again later).
    """
    out, complete = {}, True
    if not mints: return out, True
    if time.time() < _gt_backoff[0]:
        return out, False
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            d = _json("https://api.geckoterminal.com/api/v2/networks/solana/tokens/multi/" + ",".join(chunk))
        except RuntimeError: raise
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _gt_backoff[0] = time.time() + GT_BACKOFF_S
            complete = False
            continue
        except Exception:
            complete = False
            continue
        for item in (d.get("data") if isinstance(d, dict) else None) or []:
            if not isinstance(item, dict): continue
            attrs = item.get("attributes") or {}
            m = attrs.get("address")
            if not m and isinstance(item.get("id"), str) and item["id"].startswith("solana_"):
                m = item["id"][7:]
            img = _ok_img(attrs.get("image_url"))
            if m in chunk and img and m not in out: out[m] = img
            if m in chunk and attrs.get("symbol"): out.setdefault(m + ":sym", attrs.get("symbol"))
    return out, complete


def _pump(mint):
    if time.time() < _pump_off[0]: raise OSError("pump backoff")
    try:
        d = _json(f"https://frontend-api-v3.pump.fun/coins/{mint}", timeout=6)
        return _ok_img(d.get("image_uri") if isinstance(d, dict) else None)
    except RuntimeError: raise
    except Exception:
        _pump_off[0] = time.time() + 1800
        raise


def _rugcheck(mint):
    """-> (metadata-uri image, fileMeta.image, symbol). Raises OSError if the report/URI could not be read."""
    try: d = _json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=12)
    except RuntimeError: raise
    except Exception as e:
        raise OSError("rugcheck") from e
    fm = d.get("fileMeta") or {}
    tm = d.get("tokenMeta") or {}
    meta_img, uri_failed = None, False
    uri = _safe_url(tm.get("uri"))
    if uri:
        got = False
        try:
            for alt in _alts(uri)[:3]:
                if not _budget(_meta_q, META_N): raise RuntimeError("budget")
                try:
                    j = json.loads(_get(alt, timeout=10, limit=512 * 1024)[0].decode("utf-8", "replace"))
                    meta_img = _ok_img(j.get("image") if isinstance(j, dict) else None)
                    got = True
                    break
                except RuntimeError: raise
                except Exception: continue
        except RuntimeError: raise
        except Exception:
            uri_failed = True
        if not got: uri_failed = True
    fi = _ok_img(fm.get("image"))
    if uri_failed and not fi and not meta_img:
        raise OSError("rugcheck metadata")
    return meta_img, fi, tm.get("symbol") or fm.get("symbol")


def _download(url):
    for alt in _alts(url)[:3]:
        try:
            d = _download1(alt)
            if d: return d
        except RuntimeError: raise
        except Exception: continue
    return None


def _download1(url):
    u = _safe_url(url)
    if not u: return None
    if "cdn.dexscreener.com" in u:  # ask their CDN for a small rendition
        u = re.sub(r"width=\d+&height=\d+", f"width={SIZE}&height={SIZE}", u)
    if not _budget(_dl_q, DL_N): raise RuntimeError("budget")
    data, ct = _get(u, timeout=12, accept="image/*")
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    im = im.convert("RGBA")
    w, h = im.size; s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "WEBP", quality=82, method=4)
    return buf.getvalue()


def _store(mint, url, src, sym=None):
    """Download+encode; returns True when a real image file exists afterwards."""
    data = None
    try: data = _download(url)
    except RuntimeError: raise
    except Exception: data = None
    if not data: return False
    tmp = file_path(mint) + f".{os.getpid()}.tmp"
    with open(tmp, "wb") as f: f.write(data)
    os.replace(tmp, file_path(mint))
    _save_entry(mint, {"url": url, "src": src, "sym": sym, "ts": int(time.time())})
    return True


def resolve(mint, sym=None, ds_hint=None, gt_hint=None):
    """Ensure a real image for mint if one exists. Returns entry dict or None. Raises RuntimeError on budget."""
    if has_file(mint): return entry(mint) or {"src": "file"}
    e = entry(mint)
    if e and not e.get("url") and time.time() - e.get("ts", 0) < NEG_TTL: return None
    with _inflight_lock: lk = _inflight.setdefault(mint, threading.Lock())
    with lk:
        if has_file(mint): return entry(mint)
        cands, incomplete, dl_fail = [], False, False
        for step in ("ds", "gt", "pump", "rc"):
            if step == "ds":
                try:
                    ds, ok = _unpack_hint(ds_hint) if ds_hint is not None else ds_batch([mint])
                    if not ok: incomplete = True
                    if _ok_img((ds or {}).get(mint)): cands.append((ds[mint], "dexscreener"))
                    sym = sym or (ds or {}).get(mint + ":sym")
                except RuntimeError: raise
                except Exception: incomplete = True
            elif step == "gt":
                try:
                    gt, ok = _unpack_hint(gt_hint) if gt_hint is not None else gt_batch([mint])
                    if not ok: incomplete = True
                    if _ok_img((gt or {}).get(mint)): cands.append((gt[mint], "geckoterminal"))
                    sym = sym or (gt or {}).get(mint + ":sym")
                except RuntimeError: raise
                except Exception: incomplete = True
            elif step == "pump" and mint.endswith("pump"):
                try:
                    u = _pump(mint)
                    if u: cands.append((u, "pumpfun"))
                except RuntimeError: raise
                except Exception: incomplete = True
            elif step == "rc":
                try:
                    mi, fi, s2 = _rugcheck(mint); sym = sym or s2
                    if mi: cands.append((mi, "metadata_uri"))
                    if fi and fi != mi: cands.append((fi, "rugcheck"))
                except RuntimeError: raise
                except Exception: incomplete = True
            while cands:
                u, src = cands.pop(0)
                try:
                    if _store(mint, u, src, sym): return entry(mint)
                    dl_fail = True
                except RuntimeError: raise
                except Exception: dl_fail = True
        if not incomplete and not dl_fail:
            _save_entry(mint, {"url": None, "src": "fallback", "sym": sym, "ts": int(time.time())})
        return None


def warm(coins, gt_hint=None):
    """Background warm-up for a list of {'mint','symbol'} (feed). Batched DexScreener + GeckoTerminal calls."""
    todo = [c for c in coins if valid_mint(c.get("mint")) and not has_file(c["mint"])
            and not ((entry(c["mint"]) or {}).get("src") == "fallback" and time.time() - (entry(c["mint"]) or {}).get("ts", 0) < NEG_TTL)]
    if not todo: return
    if not _warm_lock.acquire(blocking=False): return
    def run():
        try:
            mints = [c["mint"] for c in todo]
            try: ds, ds_ok = ds_batch(mints)
            except Exception: ds, ds_ok = {}, False
            gt, gt_ok = _unpack_hint(gt_hint) if gt_hint is not None else ({}, True)
            need = [m for m in mints if m not in gt]
            if need:
                try:
                    got, ok = gt_batch(need)
                    gt.update(got); gt_ok = gt_ok and ok
                except Exception:
                    gt_ok = False
            for c in todo:
                try: resolve(c["mint"], c.get("symbol"), ds_hint=(ds, ds_ok), gt_hint=(gt, gt_ok))
                except RuntimeError: time.sleep(3)
                except Exception: pass
        finally: _warm_lock.release()
    threading.Thread(target=run, daemon=True).start()


_warm_lock = threading.Lock()

PALETTE = ["#9b6bff", "#14f195", "#ff5c7a", "#ffb547", "#2ee59d", "#4da3ff", "#ff7ad9", "#7af0ff"]


def avatar_svg(mint, sym=None):
    sym = re.sub(r"[^A-Za-z0-9]", "", (sym or (entry(mint) or {}).get("sym") or mint or "?"))[:2].upper() or "?"
    h = int(hashlib.sha1((mint or "").encode()).hexdigest(), 16)
    a, b = PALETTE[h % len(PALETTE)], PALETTE[(h // 7) % len(PALETTE)]
    if a == b: b = "#1b1430"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>'
            f'<rect width="256" height="256" fill="url(#g)"/><text x="128" y="128" dy=".35em" text-anchor="middle" '
            f'font-family="Inter,Arial,sans-serif" font-weight="800" font-size="{110 if len(sym) > 1 else 140}" fill="#0b0d12" fill-opacity=".82">{sym}</text></svg>')


def stats(mints):
    real = [m for m in mints if has_file(m)]
    srcs = {}
    for m in real: s = (entry(m) or {}).get("src", "?"); srcs[s] = srcs.get(s, 0) + 1
    return {"total": len(mints), "real": len(real), "fallback": len(mints) - len(real), "by_source": srcs}
