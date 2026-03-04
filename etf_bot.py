"""
SIGNAL v4 — ETF & Crypto Intelligence
Raspberry Pi | Flask (consolidated: signal-trader v3 + etfTradeSignals)
"""

from flask import Flask, request, jsonify, render_template, Response, abort
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv, schedule, threading, time, os, re
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import requests as req_lib
import yfinance as yf
from textblob import TextBlob
from config import get_free_port, NEWS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ALLOWED_SMS_NUMBERS
from trade_manager import (
    add_ticker as _add_ticker, log_trade, TICKERS_FILE,
    classify_strategy, get_removed_tickers, persist_remove, persist_unremove,
    get_positions, upsert_position, remove_position, clear_positions,
    get_monthly_pnl, record_monthly_pnl, clear_monthly_pnl,
)
from alerts import send_alert

app = Flask(__name__)

# ── Tracked assets ────────────────────────────────────────────────────────────
BUILTIN_ETFS = {
    "SPY":  "S&P 500 ETF",    "QQQ":  "Nasdaq 100 ETF",
    "IWM":  "Russell 2000",   "GLD":  "Gold ETF",
    "TLT":  "20yr Treasury",  "XLE":  "Energy ETF",
    "ARKK": "ARK Innovation", "SOXX": "Semiconductors",
}
TRACKED_ETFS   = dict(BUILTIN_ETFS)   # mutable; extended from CSV at startup
BUILTIN_CRYPTO = {"bitcoin", "ethereum", "solana", "binancecoin"}
TRACKED_CRYPTO = {
    "bitcoin": "BTC", "ethereum": "ETH",
    "solana":  "SOL", "binancecoin": "BNB",
}
CRYPTO_YF = {
    "bitcoin": "BTC-USD", "ethereum": "ETH-USD",
    "solana":  "SOL-USD", "binancecoin": "BNB-USD",
}
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/technologyNews",
    "https://www.marketwatch.com/rss/topstories",
    "https://feeds.feedburner.com/wsj/xml/rss/3_7085",
]
REDDIT_RSS = {
    "etf":    "https://www.reddit.com/r/investing+wallstreetbets+stocks.rss?limit=25",
    "crypto": "https://www.reddit.com/r/CryptoCurrency+bitcoin+ethfinance.rss?limit=25",
}
_last_signals = {}
_HEADERS = {"User-Agent": "signal-bot/4.0 (raspberry-pi; personal use)"}


# ── Load user-added tickers from CSV into watchlist ───────────────────────────
def _load_csv_tickers():
    removed = get_removed_tickers()
    # Remove blocked tickers from built-ins
    for t in list(TRACKED_ETFS.keys()):
        if t in removed:
            del TRACKED_ETFS[t]
    for c in list(TRACKED_CRYPTO.keys()):
        if TRACKED_CRYPTO[c] in removed or c.upper() in removed:
            del TRACKED_CRYPTO[c]
    # Add CSV-added tickers (skip removed)
    try:
        with open(TICKERS_FILE, newline="") as f:
            for row in csv.DictReader(f):
                t = row["Ticker"].strip().upper()
                if t and t not in TRACKED_ETFS and t not in removed:
                    TRACKED_ETFS[t] = row["Name"] or t
    except FileNotFoundError:
        pass

_load_csv_tickers()


# ── RSS / News ────────────────────────────────────────────────────────────────
def _parse_rss(xml_text, keyword=""):
    articles = []
    try:
        root = ET.fromstring(xml_text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        for item in items[:15]:
            def tag(name, fb=""):
                el = item.find(name) or item.find(f"atom:{name}", ns)
                return (el.text or "").strip() if el is not None else fb
            title = tag("title")
            desc  = re.sub(r"<[^>]+>", " ", tag("description") or tag("summary")).strip()
            link  = tag("link")
            pub   = tag("pubDate") or tag("published") or tag("updated")
            if keyword and keyword.lower() not in (title + desc).lower():
                continue
            if title:
                articles.append({"title": title, "description": desc[:300],
                                  "source": {"name": tag("source") or "RSS"},
                                  "url": link or "#", "publishedAt": pub})
    except Exception:
        pass
    return articles

def _fetch_url(url):
    try:
        return req_lib.get(url, headers=_HEADERS, timeout=8).text
    except Exception:
        return ""

def fetch_rss_articles(keyword, reddit_key=""):
    articles = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_url, u): u for u in RSS_FEEDS}
        for f in as_completed(futures):
            articles.extend(_parse_rss(f.result(), keyword=keyword))
    if reddit_key in REDDIT_RSS:
        for a in _parse_rss(_fetch_url(REDDIT_RSS[reddit_key]), keyword=keyword):
            a["source"]["name"] = "Reddit"
            articles.append(a)
    return articles[:20]

def fetch_newsapi(query):
    if not NEWS_API_KEY:
        return []
    try:
        r = req_lib.get("https://newsapi.org/v2/everything", timeout=10, params={
            "q": query, "sortBy": "publishedAt", "pageSize": 10, "language": "en",
            "from": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
            "apiKey": NEWS_API_KEY,
        })
        return r.json().get("articles", [])
    except Exception:
        return []

def fetch_all_news(query, reddit_key=""):
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fetch_newsapi, query)
        f2 = ex.submit(fetch_rss_articles, query, reddit_key)
        n, r = f1.result(), f2.result()
    seen, merged = set(), []
    for a in n + r:
        t = a.get("title", "")
        if t and t not in seen:
            seen.add(t)
            merged.append(a)
    return merged[:20]


# ── Sentiment ─────────────────────────────────────────────────────────────────
def analyze_sentiment(articles):
    if not articles:
        return 0.0, []
    scored, total = [], 0.0
    for a in articles[:15]:
        pol = TextBlob(f"{a.get('title','')} {a.get('description','') or ''}").sentiment.polarity
        total += pol
        scored.append({
            "title":     a.get("title", ""),
            "source":    a.get("source", {}).get("name", "Unknown"),
            "url":       a.get("url", "#"),
            "published": a.get("publishedAt", ""),
            "sentiment": round(pol, 3),
            "label":     "Positive" if pol > 0.05 else ("Negative" if pol < -0.05 else "Neutral"),
        })
    return round(total / len(articles[:15]), 3), scored


# ── Market indicators ─────────────────────────────────────────────────────────
def get_crypto_fear_greed():
    try:
        d = req_lib.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]
        v = int(d["value"])
        return {"value": v, "label": d["value_classification"],
                "normalised": round((v - 50) / 50, 3), "available": True}
    except Exception:
        return {"value": 50, "label": "Neutral", "normalised": 0.0, "available": False}

def get_vix():
    try:
        v = float(yf.Ticker("^VIX").fast_info.last_price)
        n = 0.3 if v < 15 else 0.0 if v < 20 else -0.3 if v < 30 else -0.8
        return {"value": round(v, 2), "normalised": n, "available": True}
    except Exception:
        return {"value": None, "normalised": 0.0, "available": False}

def get_put_call_ratio():
    try:
        r = req_lib.get(
            "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/combinedpc.csv",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        lines = [l for l in r.text.strip().split("\n") if l.strip()]
        pcr = float(lines[-1].split(",")[6])
        return {"value": round(pcr, 3),
                "normalised": round(max(-1, min(1, (1.0 - pcr) * 1.2)), 3),
                "available": True}
    except Exception:
        return {"value": None, "normalised": 0.0, "available": False}

def compute_rsi(ticker, period=14):
    try:
        hist = yf.Ticker(ticker).history(period=f"{period * 3}d")
        if len(hist) < period + 1:
            return {"value": None, "normalised": 0.0, "available": False}
        d = hist["Close"].diff()
        g = d.clip(lower=0).rolling(period).mean()
        l = (-d.clip(upper=0)).rolling(period).mean()
        v = float((100 - (100 / (1 + g / l))).iloc[-1])
        n = 0.6 if v < 30 else 0.2 if v < 45 else 0.0 if v < 55 else -0.2 if v < 70 else -0.6
        return {"value": round(v, 1), "normalised": n, "available": True}
    except Exception:
        return {"value": None, "normalised": 0.0, "available": False}

def get_etf_momentum(ticker):
    try:
        h = yf.Ticker(ticker).history(period="10d")
        if len(h) < 5:
            return 0.0
        pct = (h["Close"].iloc[-1] - h["Close"].iloc[-5]) / h["Close"].iloc[-5]
        return round(max(-1, min(1, float(pct) * 10)), 3)
    except Exception:
        return 0.0

def get_crypto_data(coin_id):
    try:
        r = req_lib.get("https://api.coingecko.com/api/v3/simple/price", timeout=10, params={
            "ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"})
        d   = r.json().get(coin_id, {})
        pct = d.get("usd_24h_change", 0) or 0
        return {"price": d.get("usd"), "change24": round(pct, 2),
                "momentum": round(max(-1, min(1, pct / 10)), 3)}
    except Exception:
        return {"price": None, "change24": 0.0, "momentum": 0.0}


# ── Signal builder ────────────────────────────────────────────────────────────
def build_signal(news, momentum, rsi, vix=None, fear_greed=None, put_call=None, asset_type="ETF"):
    rn = rsi.get("normalised", 0.0)
    if asset_type == "Crypto":
        fn  = fear_greed["normalised"] if fear_greed and fear_greed["available"] else 0.0
        s   = news * 0.20 + momentum * 0.20 + rn * 0.20 + fn * 0.40
        fac = {"news_sentiment": round(news, 3), "momentum": round(momentum, 3),
               "rsi": rsi, "fear_greed": fear_greed}
    else:
        vn  = vix["normalised"]      if vix      and vix["available"]      else 0.0
        pn  = put_call["normalised"] if put_call and put_call["available"] else 0.0
        s   = news * 0.20 + momentum * 0.20 + rn * 0.20 + vn * 0.20 + pn * 0.20
        fac = {"news_sentiment": round(news, 3), "momentum": round(momentum, 3),
               "rsi": rsi, "vix": vix, "put_call": put_call}
    s    = round(s, 3)
    sig, col = ("BUY", "green") if s > 0.15 else ("SELL", "red") if s < -0.15 else ("HOLD", "yellow")
    conf = min(int(abs(s) * 250), 95) if sig != "HOLD" else max(35, 65 - int(abs(s) * 100))
    return {"signal": sig, "color": col, "confidence": conf, "score": s, "factors": fac}


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        req_lib.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=8)
    except Exception:
        pass

def maybe_alert(ticker, signal, price, asset_type):
    prev = _last_signals.get(ticker)
    _last_signals[ticker] = signal
    if prev and prev != signal:
        e = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
        p = f"${price:,.2f}" if price else "N/A"
        msg = f"{e} *SIGNAL CHANGE*\n`{ticker}` ({asset_type})\n{prev} → *{signal}*\nPrice: {p}"
        threading.Thread(target=send_telegram, args=(msg,), daemon=True).start()


# ── Macro cache (5-min TTL) ───────────────────────────────────────────────────
_macro_cache = {}
_macro_ts    = 0.0
MACRO_TTL    = 300

def get_macro():
    global _macro_cache, _macro_ts
    if _macro_cache and (datetime.utcnow().timestamp() - _macro_ts) < MACRO_TTL:
        return _macro_cache
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_vix = ex.submit(get_vix)
        f_pc  = ex.submit(get_put_call_ratio)
    _macro_cache = {"vix": f_vix.result(), "put_call": f_pc.result()}
    _macro_ts    = datetime.utcnow().timestamp()
    return _macro_cache


# ── Per-asset signal helpers ──────────────────────────────────────────────────
def _compute_etf_signal(ticker, macro):
    try:
        name = TRACKED_ETFS.get(ticker, ticker)
        arts = fetch_all_news(f"{ticker} ETF {name}", reddit_key="etf")
        ns, _ = analyze_sentiment(arts)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_mom = ex.submit(get_etf_momentum, ticker)
            f_rsi = ex.submit(compute_rsi, ticker)
        mom = f_mom.result()
        rsi = f_rsi.result()
        sig = build_signal(ns, mom, rsi, vix=macro["vix"], put_call=macro["put_call"], asset_type="ETF")
        try:
            price = round(float(yf.Ticker(ticker).fast_info.last_price), 2)
        except Exception:
            price = None
        maybe_alert(ticker, sig["signal"], price, "ETF")
        return {"ticker": ticker, "name": name, "type": "ETF", "price": price, "signal": sig}
    except Exception as e:
        print(f"ETF {ticker}: {e}")
        return None

def _compute_crypto_signal(coin_id, fg):
    try:
        sym  = TRACKED_CRYPTO[coin_id]
        arts = fetch_all_news(f"{coin_id} {sym} cryptocurrency", reddit_key="crypto")
        ns, _ = analyze_sentiment(arts)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_cd  = ex.submit(get_crypto_data, coin_id)
            f_rsi = ex.submit(compute_rsi, CRYPTO_YF.get(coin_id, f"{sym}-USD"))
        cd  = f_cd.result()
        rsi = f_rsi.result()
        sig = build_signal(ns, cd["momentum"], rsi, fear_greed=fg, asset_type="Crypto")
        maybe_alert(sym, sig["signal"], cd["price"], "Crypto")
        return {"ticker": sym, "coin_id": coin_id, "name": f"{coin_id.title()} ({sym})",
                "type": "Crypto", "price": cd["price"], "change24": cd["change24"], "signal": sig}
    except Exception as e:
        print(f"Crypto {coin_id}: {e}")
        return None


# ── Routes: HTML pages ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/diagnostic")
def diagnostic():
    return render_template("diagnostic.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "4.0.0", "ts": datetime.utcnow().isoformat()})


# ── Routes: Signal API ────────────────────────────────────────────────────────
@app.route("/api/dashboard")
def api_dashboard():
    _load_csv_tickers()
    macro = get_macro()
    fg    = get_crypto_fear_greed()
    with ThreadPoolExecutor(max_workers=8) as ex:
        etf_futs    = [ex.submit(_compute_etf_signal,    t,  macro) for t in list(TRACKED_ETFS)]
        crypto_futs = [ex.submit(_compute_crypto_signal, c,  fg)    for c in list(TRACKED_CRYPTO)]
    items = [f.result() for f in etf_futs + crypto_futs if f.result()]
    return jsonify({
        "items": items,
        "summary": {
            "buy":   sum(1 for i in items if i["signal"]["signal"] == "BUY"),
            "sell":  sum(1 for i in items if i["signal"]["signal"] == "SELL"),
            "hold":  sum(1 for i in items if i["signal"]["signal"] == "HOLD"),
            "total": len(items),
        },
        "macro":      {**macro, "fear_greed": fg},
        "updated_at": datetime.utcnow().isoformat(),
    })

@app.route("/api/etf/<ticker>")
def api_etf(ticker):
    ticker = ticker.upper()
    if ticker not in TRACKED_ETFS:
        abort(404)
    macro    = get_macro()
    arts     = fetch_all_news(f"{ticker} ETF {TRACKED_ETFS[ticker]}", reddit_key="etf")
    ns, scored = analyze_sentiment(arts)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_mom = ex.submit(get_etf_momentum, ticker)
        f_rsi = ex.submit(compute_rsi, ticker)
    sig = build_signal(ns, f_mom.result(), f_rsi.result(),
                       vix=macro["vix"], put_call=macro["put_call"], asset_type="ETF")
    try:
        price = round(float(yf.Ticker(ticker).fast_info.last_price), 2)
    except Exception:
        price = None
    maybe_alert(ticker, sig["signal"], price, "ETF")
    return jsonify({"ticker": ticker, "name": TRACKED_ETFS[ticker], "type": "ETF",
                    "price": price, "signal": sig, "articles": scored, "macro": macro,
                    "updated_at": datetime.utcnow().isoformat()})

@app.route("/api/crypto/<coin>")
def api_crypto(coin):
    coin = coin.lower()
    if coin not in TRACKED_CRYPTO:
        abort(404)
    sym  = TRACKED_CRYPTO[coin]
    fg   = get_crypto_fear_greed()
    arts = fetch_all_news(f"{coin} {sym} cryptocurrency", reddit_key="crypto")
    ns, scored = analyze_sentiment(arts)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_cd  = ex.submit(get_crypto_data, coin)
        f_rsi = ex.submit(compute_rsi, CRYPTO_YF.get(coin, f"{sym}-USD"))
    cd  = f_cd.result()
    rsi = f_rsi.result()
    sig = build_signal(ns, cd["momentum"], rsi, fear_greed=fg, asset_type="Crypto")
    maybe_alert(sym, sig["signal"], cd["price"], "Crypto")
    return jsonify({"ticker": sym, "coin_id": coin, "name": f"{coin.title()} ({sym})",
                    "type": "Crypto", "price": cd["price"], "change24": cd["change24"],
                    "signal": sig, "articles": scored, "fear_greed": fg,
                    "updated_at": datetime.utcnow().isoformat()})

@app.route("/api/macro")
def api_macro():
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_vix = ex.submit(get_vix)
        f_pc  = ex.submit(get_put_call_ratio)
        f_fg  = ex.submit(get_crypto_fear_greed)
    return jsonify({"vix": f_vix.result(), "put_call": f_pc.result(),
                    "fear_greed": f_fg.result(), "updated_at": datetime.utcnow().isoformat()})

@app.route("/api/watchlist")
def api_watchlist():
    _load_csv_tickers()
    return jsonify({
        "etfs":   [{"ticker": k, "name": v} for k, v in TRACKED_ETFS.items()],
        "crypto": [{"coin_id": k, "symbol": v} for k, v in TRACKED_CRYPTO.items()],
    })

@app.route("/api/telegram/test")
def api_telegram_test():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({"status": "not_configured",
                        "message": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"})
    send_telegram("🔔 *SIGNAL v4 connected!*\nYour Pi is live and sending alerts.")
    return jsonify({"status": "sent"})


# ── Routes: Add / Remove Asset (watchlist management) ─────────────────────────
@app.route("/api/tickers")
def api_tickers():
    """Return ALL tracked assets: built-in ETFs + CSV-added ETFs + crypto."""
    _load_csv_tickers()
    csv_tickers = set()
    try:
        with open(TICKERS_FILE, newline="") as f:
            csv_tickers = {row["Ticker"].strip().upper() for row in csv.DictReader(f)}
    except FileNotFoundError:
        pass

    tickers = []
    # ETFs (built-in + CSV-added)
    for ticker, name in TRACKED_ETFS.items():
        source   = "builtin" if ticker in BUILTIN_ETFS else "custom"
        strategy = classify_strategy(name)
        tickers.append({"ticker": ticker, "name": name,
                         "strategy": strategy, "source": source, "type": "ETF"})
    # Crypto (always built-in)
    for coin_id, sym in TRACKED_CRYPTO.items():
        tickers.append({"ticker": sym, "coin_id": coin_id,
                         "name": f"{sym} ({coin_id.title()})",
                         "strategy": "Crypto", "source": "builtin", "type": "Crypto"})
    return jsonify({"tickers": tickers})

@app.route("/api/add-ticker", methods=["POST"])
def api_add_ticker():
    data   = request.get_json(silent=True) or {}
    symbol = (data.get("ticker") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "Ticker symbol is required."}), 400
    try:
        persist_unremove(symbol)          # un-block if previously removed
        name, strategy = _add_ticker(symbol)
        TRACKED_ETFS[symbol] = name       # live-add to watchlist
        return jsonify({"ticker": symbol, "name": name, "strategy": strategy}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/remove-ticker", methods=["POST"])
def api_remove_ticker():
    data   = request.get_json(silent=True) or {}
    symbol = (data.get("ticker") or "").strip().upper()
    coin   = (data.get("coin_id") or "").strip().lower()
    if not symbol:
        return jsonify({"error": "Ticker required"}), 400
    persist_remove(symbol)
    TRACKED_ETFS.pop(symbol, None)
    if coin and coin in TRACKED_CRYPTO:
        del TRACKED_CRYPTO[coin]
    return jsonify({"removed": symbol})


# ── Routes: PWA ───────────────────────────────────────────────────────────────
@app.route("/favicon.ico")
def favicon():
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' viewBox='0 0 32 32'>"
           "<rect width='32' height='32' fill='#080c10'/>"
           "<text x='50%' y='60%' font-family='monospace' font-weight='bold' font-size='18' "
           "fill='#00d4ff' text-anchor='middle'>S</text></svg>")
    return Response(svg, mimetype="image/svg+xml")

@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "SIGNAL", "short_name": "SIGNAL",
        "description": "ETF & Crypto Trading Signals",
        "start_url": "/", "display": "standalone",
        "background_color": "#080c10", "theme_color": "#00d4ff",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    })

@app.route("/sw.js")
def service_worker():
    sw = """const CACHE='signal-v6';const OFFLINE=['/'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(OFFLINE)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',e=>{if(e.request.url.includes('/api/'))return;e.respondWith(fetch(e.request).catch(()=>caches.match(e.request).then(r=>r||caches.match('/'))));});"""
    return Response(sw, mimetype="application/javascript")

@app.route("/icons/<icon_name>")
def serve_icon(icon_name):
    size = 192 if "192" in icon_name else 512
    svg  = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' "
            f"viewBox='0 0 {size} {size}'>"
            f"<rect width='{size}' height='{size}' fill='#080c10'/>"
            f"<text x='50%' y='52%' font-family='monospace' font-weight='bold' "
            f"font-size='{size//4}' fill='#00d4ff' text-anchor='middle' "
            f"dominant-baseline='middle'>SIG</text></svg>")
    return Response(svg, mimetype="image/svg+xml")


# ── Routes: Portfolio ────────────────────────────────────────────────────────
_CRYPTO_TICKERS = {"BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD"}

def _fetch_price(ticker):
    try:
        return round(float(yf.Ticker(ticker).fast_info.last_price), 4)
    except Exception:
        return None

@app.route("/api/portfolio")
def api_portfolio():
    positions = get_positions()
    enriched, total_cost, total_value = [], 0.0, 0.0
    for p in positions:
        price     = _fetch_price(p["ticker"])
        cost      = round(p["qty"] * p["avg_price"], 2)
        value     = round(p["qty"] * price, 2) if price else None
        pnl       = round(value - cost, 2)       if value is not None else None
        pnl_pct   = round((pnl / cost) * 100, 2) if (pnl is not None and cost) else None
        total_cost  += cost
        total_value += value if value is not None else cost
        enriched.append({**p, "current_price": price, "cost": cost,
                          "value": value, "pnl": pnl, "pnl_pct": pnl_pct})
    total_pnl     = round(total_value - total_cost, 2)
    total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost else 0.0
    return jsonify({
        "positions": enriched,
        "summary": {
            "total_cost":    round(total_cost, 2),
            "total_value":   round(total_value, 2),
            "total_pnl":     total_pnl,
            "total_pnl_pct": total_pnl_pct,
        },
        "updated_at": datetime.utcnow().isoformat(),
    })

@app.route("/api/portfolio/add", methods=["POST"])
def api_portfolio_add():
    data = request.get_json(silent=True) or {}
    ticker    = (data.get("ticker") or "").strip().upper()
    try:
        qty       = float(data.get("qty", 0))
        avg_price = float(data.get("avg_price", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "qty and avg_price must be numbers."}), 400
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400
    if qty <= 0 or avg_price <= 0:
        return jsonify({"error": "qty and avg_price must be positive."}), 400
    pos = upsert_position(ticker, qty, avg_price)
    log_trade("BUY", ticker, avg_price, qty, source="portfolio-ui")
    return jsonify(pos), 201

@app.route("/api/portfolio/remove", methods=["POST"])
def api_portfolio_remove():
    data   = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400
    remove_position(ticker)
    return jsonify({"removed": ticker})

@app.route("/api/portfolio/monthly")
def api_portfolio_monthly():
    return jsonify({"monthly": get_monthly_pnl()})

@app.route("/api/portfolio/snapshot", methods=["POST"])
def api_portfolio_snapshot():
    """Record current portfolio value as this month's P&L entry."""
    positions = get_positions()
    total_cost, total_value = 0.0, 0.0
    for p in positions:
        price        = _fetch_price(p["ticker"])
        total_cost  += p["qty"] * p["avg_price"]
        total_value += p["qty"] * price if price else p["qty"] * p["avg_price"]
    history     = get_monthly_pnl()
    prev_value  = history[-1]["total_value"] if history else total_cost
    gain        = round(total_value - prev_value, 2)
    gain_pct    = round((gain / prev_value) * 100, 2) if prev_value else 0.0
    month       = datetime.utcnow().strftime("%Y-%m")
    record_monthly_pnl(month, round(total_value, 2), gain, gain_pct)
    return jsonify({"month": month, "total_value": round(total_value, 2),
                    "gain": gain, "gain_pct": gain_pct})

@app.route("/api/portfolio/test-data", methods=["POST"])
def api_portfolio_test_data():
    """Load fake positions + monthly history for testing."""
    clear_positions()
    clear_monthly_pnl()
    test_positions = [
        ("SPY",    10,   600.00),
        ("QQQ",     5,   380.00),
        ("BTC-USD", 0.1, 45000.00),
        ("ETH-USD", 1.0, 2500.00),
    ]
    for ticker, qty, avg_price in test_positions:
        upsert_position(ticker, qty, avg_price)
    test_monthly = [
        ("2025-11", 16200, 200,  1.25),
        ("2025-12", 17800, 1600, 9.88),
        ("2026-01", 18500, 700,  3.93),
        ("2026-02", 17100, -1400, -7.57),
    ]
    for month, total_value, gain, gain_pct in test_monthly:
        record_monthly_pnl(month, total_value, gain, gain_pct)
    return jsonify({"loaded": len(test_positions), "months": len(test_monthly)})

@app.route("/api/portfolio/clear", methods=["POST"])
def api_portfolio_clear():
    clear_positions()
    clear_monthly_pnl()
    return jsonify({"cleared": True})


# ── Routes: SMS webhook ───────────────────────────────────────────────────────
@app.route("/sms", methods=["POST"])
def sms_webhook():
    from_number = request.form.get("From")
    body        = request.form.get("Body", "").strip()
    if from_number not in ALLOWED_SMS_NUMBERS:
        return "Not authorized", 403
    response_msg = handle_sms_command(body)
    send_alert(response_msg, from_number)
    return "OK", 200

def handle_sms_command(body):
    parts = body.split(":")
    cmd   = parts[0].strip().upper()
    arg   = parts[1].strip() if len(parts) > 1 else ""
    if cmd == "ADD":
        name, strategy = _add_ticker(arg)
        TRACKED_ETFS[arg.upper()] = name
        return f"✅ Added {arg} — {name}, {strategy}"
    elif cmd in ["BUY", "SELL"]:
        log_trade(cmd, arg, 0.0, 0)
        return f"✅ Manual {cmd} signal sent for {arg}"
    elif cmd == "LIST":
        return _list_tickers()
    return "Unknown command"

def _list_tickers():
    try:
        with open(TICKERS_FILE) as f:
            rows = list(csv.reader(f))
        if len(rows) <= 1:
            return "No tickers currently tracked."
        return "\n".join(f"{r[0]} — {r[1]} — {r[2]}" for r in rows[1:])
    except FileNotFoundError:
        return "No tickers file found."


# ── Scheduler ─────────────────────────────────────────────────────────────────
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    port = get_free_port()
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
