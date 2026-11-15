#!/usr/bin/env python3
"""xScanner X Live Feed ingest: merge raw X search results into data/x_feed.json.

Usage:
  python3 xfeed_ingest.py [--topic memecoins|politics|tech] FILE [FILE ...]
  python3 xfeed_ingest.py --prune-hours 48 data/raw/*.txt

FILE can be:
  * a raw X API v2 search response ({"data":[...], "includes":{"users":[...]}, "meta":{...}}),
    optionally wrapped in tool-output text (the first JSON object in the file is used), or
  * a JSON list of already-normalised posts (same shape as x_feed.json posts).
If --topic is omitted, the topic is taken from the filename prefix (memecoins_1.txt -> memecoins).

Output schema (data/x_feed.json):
  {"version":1, "updated_at": ISO, "topics": {topic: {"last_ingest": ISO, "count": n}},
   "posts": [{topic, id, author, handle, verified, followers, text, created_at,
              metrics:{likes,reposts,replies,quotes,views,bookmarks}, url, cas:[...], fetched_at}]}
Dedupe key is (topic, id); re-seen posts get fresh metrics. Newest first, capped per topic (--cap, default 300).
Read-only toward X: this script never calls X, never posts. Writes atomically (tmp + rename).
"""
import argparse, html, json, os, re, sys, tempfile
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(HERE, "data", "x_feed.json")
TOPICS = ("memecoins", "politics", "tech")
B58 = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
URLISH = re.compile(r"https?://\S+")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def find_cas(text):
    """Solana mint-looking strings (base58, 32-44 chars). Ignores URLs; needs a digit + mixed case
    to avoid plain long words. Keeps order, dedupes."""
    t = URLISH.sub(" ", text or "")
    out = []
    for m in B58.findall(t):
        if not (re.search(r"\d", m) and re.search(r"[a-z]", m) and re.search(r"[A-Z]", m)):
            continue
        if m not in out:
            out.append(m)
    return out


def load_any_json(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    s = txt.lstrip()
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s, strict=False)
        except Exception:
            pass
    i = min([k for k in (txt.find("{"), txt.find("[")) if k >= 0], default=-1)
    if i < 0:
        raise ValueError("no JSON found")
    obj, _ = json.JSONDecoder(strict=False).raw_decode(txt[i:])
    return obj


def normalise_api(resp, topic, fetched):
    users = {u.get("id"): u for u in ((resp.get("includes") or {}).get("users") or []) if isinstance(u, dict)}
    out = []
    for p in resp.get("data") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        u = users.get(p.get("author_id")) or {}
        pm = p.get("public_metrics") or {}
        um = u.get("public_metrics") or {}
        handle = u.get("username") or ""
        if not handle and p.get("url"):
            m = re.search(r"x\.com/([^/]+)/status", p["url"])
            handle = m.group(1) if m else ""
        text = html.unescape(p.get("note_tweet", {}).get("text") if isinstance(p.get("note_tweet"), dict) else p.get("text") or "")
        refs = [r.get("type") for r in (p.get("referenced_tweets") or []) if isinstance(r, dict)]
        out.append({
            "topic": topic, "id": str(p["id"]), "author": u.get("name") or handle, "handle": handle,
            "verified": bool(u.get("verified")), "followers": um.get("followers_count"),
            "text": text, "created_at": p.get("created_at"), "lang": p.get("lang"),
            "metrics": {"likes": pm.get("like_count", 0), "reposts": pm.get("retweet_count", 0),
                        "replies": pm.get("reply_count", 0), "quotes": pm.get("quote_count", 0),
                        "views": pm.get("impression_count"), "bookmarks": pm.get("bookmark_count", 0)},
            "url": p.get("url") or (f"https://x.com/{handle or 'i'}/status/{p['id']}"),
            "cas": find_cas(text), "ref": refs[0] if refs else None, "fetched_at": fetched,
        })
    return out


def normalise_list(items, topic, fetched):
    out = []
    for p in items:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        q = dict(p)
        q["topic"] = q.get("topic") or topic
        q["id"] = str(q["id"])
        q.setdefault("cas", find_cas(q.get("text", "")))
        q.setdefault("metrics", {})
        q.setdefault("fetched_at", fetched)
        out.append(q)
    return out


def load_feed(path=FEED):
    try:
        d = json.load(open(path, encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("posts"), list):
            d.setdefault("topics", {})
            return d
    except Exception:
        pass
    return {"version": 1, "updated_at": None, "topics": {}, "posts": []}


def merge(feed, new_posts, cap=300, prune_hours=None):
    by = {(p.get("topic"), p.get("id")): p for p in feed["posts"]}
    added = 0
    for p in new_posts:
        k = (p.get("topic"), p.get("id"))
        if k in by:
            old = by[k]
            old.update({kk: vv for kk, vv in p.items() if vv not in (None, "", [])})
        else:
            by[k] = p; added += 1
    posts = sorted(by.values(), key=lambda p: (p.get("created_at") or "", p.get("id") or ""), reverse=True)
    if prune_hours:
        cut = (datetime.now(timezone.utc) - timedelta(hours=prune_hours)).isoformat().replace("+00:00", "Z")
        posts = [p for p in posts if (p.get("created_at") or "") >= cut[:19]]
    kept, per = [], {}
    for p in posts:
        t = p.get("topic") or "other"
        if per.get(t, 0) < cap:
            per[t] = per.get(t, 0) + 1; kept.append(p)
    feed["posts"] = kept
    return added, per


def write_atomic(obj, path=FEED):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".x_feed.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--topic", choices=TOPICS)
    ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--prune-hours", type=float, default=None, help="drop posts older than N hours")
    ap.add_argument("--feed", default=FEED)
    a = ap.parse_args()
    feed = load_feed(a.feed)
    fetched = now_iso()
    new, touched = [], set()
    for f in a.files:
        topic = a.topic or os.path.basename(f).split("_")[0].split(".")[0].lower()
        if topic not in TOPICS:
            print(f"skip {f}: unknown topic '{topic}' (use --topic)", file=sys.stderr); continue
        try:
            obj = load_any_json(f)
        except Exception as e:
            print(f"skip {f}: {e}", file=sys.stderr); continue
        ps = normalise_list(obj, topic, fetched) if isinstance(obj, list) else normalise_api(obj, topic, fetched)
        print(f"{f}: {len(ps)} posts -> {topic}")
        new += ps; touched.add(topic)
    added, per = merge(feed, new, a.cap, a.prune_hours)
    feed["updated_at"] = fetched
    for t in TOPICS:
        info = feed["topics"].setdefault(t, {})
        if t in touched: info["last_ingest"] = fetched
        info["count"] = per.get(t, 0)
        newest = next((p.get("created_at") for p in feed["posts"] if p.get("topic") == t), None)
        info["newest"] = newest
    write_atomic(feed, a.feed)
    print(json.dumps({"added": added, "counts": per, "updated_at": fetched}))


if __name__ == "__main__":
    main()
