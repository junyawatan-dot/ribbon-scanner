"""
Larsson-style ribbon scanner for the CoinGecko top 500.

Runs once a day after the 00:00 UTC daily close:
  1. Gets the top N coins by market cap from CoinGecko (2 API calls).
  2. Finds each coin on a major exchange and downloads its daily candles.
  3. Computes the 15/19/25/29 SMMA ribbon on hl2 and the grey-flip events.
  4. Sends a Telegram digest and rebuilds the dashboard in docs/.

Colours: gold = fast above slow and ribbon ordered, blue = fast below slow and
ordered, grey = ribbon mixed. This is an approximation of the invite-only
Larsson Line, not the original.
"""
import html
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ------------------------------------------------------------------ settings
TOP_N = int(os.getenv("TOP_N", "500"))
LENGTHS = (15, 19, 25, 29)
MIN_BARS = 60                     # skip coins with less daily history than this
CANDLES = 300                     # daily candles requested per coin
PRICE_TOLERANCE = 0.25            # reject an exchange match if its price is >25% off CoinGecko's
ALERT_EVENTS = [e.strip() for e in os.getenv("ALERT_EVENTS", "blue_to_grey").split(",") if e.strip()]
SEND_EMPTY = os.getenv("SEND_EMPTY", "1") == "1"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
CG_KEY = os.getenv("COINGECKO_API_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OUT_DIR = os.getenv("OUT_DIR", "docs")
HISTORY_DAYS = 90

# exchanges tried in order; ones blocked from the runner's region are skipped automatically
EXCHANGES = ["binance", "coinbase", "kraken", "okx", "bybit", "kucoin", "gateio", "bitget", "mexc"]
QUOTES = ["USDT", "USD", "USDC"]
TV_PREFIX = {"binance": "BINANCE", "coinbase": "COINBASE", "kraken": "KRAKEN", "okx": "OKX", "bybit": "BYBIT",
             "kucoin": "KUCOIN", "gateio": "GATEIO", "bitget": "BITGET", "mexc": "MEXC"}

STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "USDD", "FRAX", "USDP", "GUSD",
           "LUSD", "BUSD", "USD0", "USDY", "RLUSD", "USDX", "SUSD", "EURC", "EURS", "USDB", "USDG", "USD1",
           "BFUSD", "USDTB", "SUSDE", "CRVUSD", "GHO", "XAUT", "PAXG"}

EVENT_LABEL = {
    "blue_to_grey": "Blue → grey (buy signal)",
    "back_to_gold": "Gold → grey → gold (re-buy signal)",
    "gold_to_grey": "Gold → grey (sell signal)",
    "back_to_blue": "Blue → grey → blue (sell signal)",
    "grey_to_gold": "Grey → gold (trend turned up)",
    "grey_to_blue": "Grey → blue (trend turned down)",
}
COLOUR_NAME = {1: "gold", -1: "blue", 0: "grey"}


# ------------------------------------------------------------------ indicator
def rma(x, n):
    out = [math.nan] * len(x)
    if len(x) < n:
        return out
    out[n - 1] = sum(x[:n]) / n
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def colours(highs, lows):
    hl2 = [(h + l) / 2 for h, l in zip(highs, lows)]
    f, m1, m2, s = (rma(hl2, n) for n in LENGTHS)
    out = []
    for i in range(len(hl2)):
        if math.isnan(s[i]):
            out.append(0)
            continue
        mixed = ((f[i] < m1[i]) != (f[i] < s[i])) or ((m2[i] < s[i]) != (f[i] < s[i]))
        out.append(0 if mixed else (-1 if f[i] < s[i] else 1))
    return out, f, s


def last_event(c):
    """Event on the last bar (the candle that just closed), plus days in the current colour."""
    i = len(c) - 1
    cur, prev = c[i], c[i - 1]
    days = 1
    while days <= i and c[i - days] == cur:
        days += 1
    if cur == 0 and prev == -1:
        return "blue_to_grey", days
    if cur == 0 and prev == 1:
        return "gold_to_grey", days
    if cur != 0 and prev == 0:
        j = i - 1
        while j > 0 and c[j] == 0:
            j -= 1
        before = c[j]
        if cur == 1:
            return ("back_to_gold" if before == 1 else "grey_to_gold"), days
        return ("back_to_blue" if before == -1 else "grey_to_blue"), days
    return None, days


# ------------------------------------------------------------------ data
def cg_get(path, params):
    headers = {"accept": "application/json"}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for attempt in range(5):
        r = requests.get("https://api.coingecko.com/api/v3" + path, params=params, headers=headers, timeout=30)
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("CoinGecko rate limit")


def top_coins(n):
    coins = []
    for page in range(1, math.ceil(n / 250) + 1):
        coins += cg_get("/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc",
                                           "per_page": 250, "page": page})
        time.sleep(3)
    out = []
    for c in coins[:n]:
        sym = (c.get("symbol") or "").upper()
        price = c.get("current_price") or 0
        if sym in STABLES or ("USD" in sym and 0.97 < price < 1.03):
            continue
        out.append({"id": c["id"], "name": c["name"], "symbol": sym, "rank": c.get("market_cap_rank"),
                    "price": price, "image": c.get("image", "")})
    return out


def load_exchanges():
    import ccxt
    loaded = []
    for name in EXCHANGES:
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 30000})
            ex.load_markets()
            if not ex.has.get("fetchOHLCV"):
                continue
            loaded.append(ex)
            print(f"[ok] {name}: {len(ex.markets)} markets", flush=True)
        except Exception as e:  # geo-blocked, down, etc.
            print(f"[skip] {name}: {type(e).__name__}: {str(e)[:120]}", flush=True)
    return loaded


def candidates(exchanges, sym):
    for ex in exchanges:
        for q in QUOTES:
            m = ex.markets.get(f"{sym}/{q}")
            if m and m.get("spot") and m.get("active", True) is not False:
                yield ex, m["symbol"]


def daily_candles(ex, pair):
    rows = ex.fetch_ohlcv(pair, "1d", limit=CANDLES)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [r for r in rows if r[0] < today.timestamp() * 1000]   # drop the still-open candle
    return rows


# ------------------------------------------------------------------ scan
def scan(coins, fetch):
    """fetch(coin) -> (exchange_id, pair, rows) or None. Returns per-coin results."""
    results, skipped = [], []
    for k, coin in enumerate(coins, 1):
        try:
            got = fetch(coin)
        except Exception as e:
            got = None
            print(f"  {coin['symbol']}: error {type(e).__name__}", flush=True)
        if not got:
            skipped.append(coin)
            continue
        ex_id, pair, rows = got
        hi = [r[2] for r in rows]
        lo = [r[3] for r in rows]
        c, f, s = colours(hi, lo)
        ev, days = last_event(c)
        last_date = datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).date().isoformat()
        results.append({**coin, "exchange": ex_id, "pair": pair, "close": rows[-1][4], "date": last_date,
                        "colour": COLOUR_NAME[c[-1]], "prev": COLOUR_NAME[c[-2]], "days": days, "event": ev,
                        "recent": [COLOUR_NAME[x] for x in c[-30:]],
                        "tv": f"{TV_PREFIX.get(ex_id, ex_id.upper())}:{pair.replace('/', '')}"})
        if k % 50 == 0:
            print(f"  scanned {k}/{len(coins)}", flush=True)
    return results, skipped


def live_fetch_factory(exchanges):
    def fetch(coin):
        for ex, pair in candidates(exchanges, coin["symbol"]):
            try:
                rows = daily_candles(ex, pair)
            except Exception:
                continue
            if len(rows) < MIN_BARS:
                continue
            ref = coin["price"]
            if ref and abs(rows[-1][4] / ref - 1) > PRICE_TOLERANCE:
                continue          # same ticker, different token
            return ex.id, pair, rows
        return None
    return fetch


# ------------------------------------------------------------------ outputs
def update_history(results, path):
    hist = []
    if os.path.exists(path):
        with open(path) as fh:
            hist = json.load(fh)
    seen = {(h["date"], h["id"], h["event"]) for h in hist}
    for r in results:
        if r["event"] and (r["date"], r["id"], r["event"]) not in seen:
            hist.append({"date": r["date"], "id": r["id"], "symbol": r["symbol"], "name": r["name"],
                         "rank": r["rank"], "event": r["event"], "close": r["close"], "tv": r["tv"]})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).date().isoformat()
    hist = sorted([h for h in hist if h["date"] >= cutoff], key=lambda h: (h["date"], h["rank"] or 9999), reverse=True)
    with open(path, "w") as fh:
        json.dump(hist, fh)
    return hist


def telegram_digest(results, skipped_n, run_date):
    lines = [f"<b>Ribbon scan, daily close {run_date} (UTC)</b>",
             f"{len(results)} coins scanned, {skipped_n} skipped (no exchange data)"]
    any_hit = False
    for ev in ALERT_EVENTS:
        hits = sorted([r for r in results if r["event"] == ev], key=lambda r: r["rank"] or 9999)
        lines.append("")
        lines.append(f"<b>{html.escape(EVENT_LABEL.get(ev, ev))}: {len(hits)}</b>")
        for r in hits:
            any_hit = True
            url = f"https://www.tradingview.com/chart/?symbol={r['tv']}"
            lines.append(f"• <a href=\"{url}\">{html.escape(r['symbol'])}</a> #{r['rank']} "
                         f"{html.escape(r['name'])}, close {fmt_price(r['close'])} ({r['exchange']})")
        if not hits:
            lines.append("none")
    if DASHBOARD_URL:
        lines += ["", f"<a href=\"{DASHBOARD_URL}\">Open dashboard</a>"]
    return "\n".join(lines), any_hit


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram not configured; message below\n" + text)
        return
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for ch in chunks:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": ch, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=30)
        if not r.ok:
            print("Telegram error:", r.status_code, r.text[:300])
        time.sleep(1)


def fmt_price(p):
    if p is None:
        return "-"
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6g}"


def build_dashboard(results, skipped, hist, run_date, path):
    data = {"run_date": run_date, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "rows": results, "skipped": [{"symbol": s["symbol"], "name": s["name"], "rank": s["rank"]} for s in skipped],
            "history": hist, "labels": EVENT_LABEL}
    with open(os.path.join(os.path.dirname(path), "data.json"), "w") as fh:
        json.dump(data, fh)
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")
    with open(tpl_path) as fh:
        tpl = fh.read()
    payload = json.dumps(data).replace("</", "<\\/")
    with open(path, "w") as fh:
        fh.write(tpl.replace("/*__DATA__*/null", payload))


def main(fetch=None, coins=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    if coins is None:
        coins = top_coins(TOP_N)
        print(f"{len(coins)} coins after removing stablecoins", flush=True)
    if fetch is None:
        exchanges = load_exchanges()
        if not exchanges:
            sys.exit("No exchange reachable")
        fetch = live_fetch_factory(exchanges)
    results, skipped = scan(coins, fetch)
    if not results:
        sys.exit("No results")
    run_date = max(r["date"] for r in results)
    stale = [r for r in results if r["date"] != run_date]
    results = [r for r in results if r["date"] == run_date]      # ignore coins whose feed is behind
    skipped += stale
    hist = update_history(results, os.path.join(OUT_DIR, "history.json"))
    build_dashboard(results, skipped, hist, run_date, os.path.join(OUT_DIR, "index.html"))
    text, any_hit = telegram_digest(results, len(skipped), run_date)
    if any_hit or SEND_EMPTY:
        send_telegram(text)
    counts = {e: sum(r["event"] == e for r in results) for e in EVENT_LABEL}
    print(f"done: {len(results)} scanned, {len(skipped)} skipped, events {counts}")


if __name__ == "__main__":
    main()
