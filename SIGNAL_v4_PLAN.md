# SIGNAL v4 — Implementation Plan

**Date:** 2026-03-03
**Based on:** SIGNAL v3 project summary + v4 refactored structure proposal
**Key problem to fix first:** News feed is not factored in during signal computation (20% weight is dead weight)

---

## 1. Current Problems (Priority Order)

| # | Problem | Impact |
|---|---------|--------|
| 1 | **News feed always fails** — RSS/Reddit blocked by Pi-hole, NewsAPI not configured | Signals lose 20% input — accuracy drops |
| 2 | Put/Call ratio failing — CBOE blocks Pi IP | Signals lose another 20% input |
| 3 | Telegram not configured | No change alerts |
| 4 | Flat file code — no caching, no history, no regime detection | Technical debt, repeated API calls, no signal tracking |

---

## 2. The News Feed Problem — Root Cause & Fix

### Why it's broken
- **RSS feeds** (Reuters, MarketWatch, Reddit) rely on outbound HTTPS from the Pi.
  Pi-hole intercepts/blocks these DNS queries.
  `urllib` (the current fetcher) has no retry, no fallback, no timeout.
- **NewsAPI** has a valid free tier (100 req/day) but `NEWS_API_KEY` was never added to the service env.
- **In test mode**, there is no mock/stub for news — the score silently becomes 0.

### Fix strategy (layered fallbacks, in order)

```
1. Yahoo Finance RSS    ← already works (same domain as yfinance)
2. NewsAPI             ← free, reliable, just needs key
3. Alpha Vantage News  ← free tier, 25 req/day
4. Finviz headlines    ← simple HTML scrape, no auth needed
5. Neutral fallback    ← score = 0.0, flagged as "news unavailable"
```

Each fetcher has:
- 5-second timeout
- Catches all exceptions silently
- Returns `None` on failure so the next source is tried

In **test mode**: inject synthetic headlines via a `data/test_news.json` fixture so the full 20% weight is exercised.

---

## 3. v4 Folder Structure

```
signal-trader/
│
├── app/
│   ├── main.py                  ← Entry point (Flask app factory)
│   ├── routes/
│   │   ├── dashboard.py         ← GET / (main dashboard)
│   │   ├── api.py               ← GET /api/dashboard (JSON)
│   │   └── diagnostic.py        ← GET /diagnostic
│   │
│   ├── services/
│   │   ├── data_fetchers.py     ← Prices, VIX, Fear & Greed, RSI inputs
│   │   ├── news_fetcher.py      ← NEW: layered news with fallbacks (see §2)
│   │   ├── indicators.py        ← RSI, momentum, SMA, ATR calculations
│   │   ├── scoring.py           ← Weight system → BUY/SELL/HOLD
│   │   ├── regime.py            ← Market condition (bull/bear/neutral)
│   │   └── alerts.py            ← Telegram alerts on signal change
│   │
│   ├── database/
│   │   ├── models.py            ← SignalHistory table definition
│   │   └── storage.py           ← SQLite read/write helpers
│   │
│   ├── cache/
│   │   └── cache_manager.py     ← TTL cache: prices=5min, news=30min
│   │
│   └── utils/
│       └── helpers.py           ← Shared utilities
│
├── data/
│   └── test_news.json           ← Fixture headlines for test mode
│
├── templates/
│   ├── index.html               ← Main dashboard
│   └── diagnostic.html
│
├── static/                      ← CSS, JS, PWA manifest
│
├── tests/
│   ├── test_news_fetcher.py     ← Unit tests for each news source
│   ├── test_scoring.py          ← Verify weight calculations
│   └── test_indicators.py
│
├── requirements.txt
├── .env.example
└── systemd/
    └── signal.service
```

---

## 4. Implementation Phases

### Phase 1 — Fix News Feed (do this first, unblocks 20% signal weight)

**Files to create/modify:**

1. **`app/services/news_fetcher.py`** — new file
   - `fetch_news_yahoo(ticker)` — RSS from finance.yahoo.com/rss/headline (same HTTP stack as yfinance, bypasses Pi-hole block)
   - `fetch_news_newsapi(query)` — NewsAPI.org, reads `NEWS_API_KEY` from env
   - `fetch_news_finviz(ticker)` — lightweight HTML scrape of finviz.com news panel
   - `get_news_sentiment(ticker)` — tries sources in order, TextBlob polarity on titles, returns float [-1, +1] or `None`

2. **`data/test_news.json`** — new file
   - 10 synthetic headlines per ticker category (bullish/bearish mix)
   - Used when `MODE=test` so news weight is always exercised in CI

3. **`app/services/scoring.py`** — update
   - Import `news_fetcher.get_news_sentiment`
   - If sentiment returns `None`, redistribute the 20% weight proportionally across working indicators (not silently set to 0)
   - Log which sources are live vs fallback

4. **`/etc/systemd/system/signal.service`** — add env vars
   ```
   Environment="NEWS_API_KEY=<your key from newsapi.org>"
   Environment="TELEGRAM_BOT_TOKEN=<from @BotFather>"
   Environment="TELEGRAM_CHAT_ID=<your chat id>"
   ```

5. **`tests/test_news_fetcher.py`** — unit tests
   - `test_yahoo_rss_returns_sentiment()` — real HTTP, skipped in offline env
   - `test_fallback_chain_on_failure()` — mock all sources to fail, verify neutral score + flag
   - `test_test_mode_uses_fixture()` — verify `MODE=test` loads `test_news.json`

---

### Phase 2 — Refactor to v4 Structure

Move existing code into the new folder layout without changing behaviour:

| Old file | New location |
|----------|-------------|
| `main.py` | `app/main.py` (Flask factory) + `app/routes/` |
| Signal computation logic | `app/services/scoring.py` |
| `data_fetchers` | `app/services/data_fetchers.py` |
| RSI/momentum math | `app/services/indicators.py` |
| Telegram logic | `app/services/alerts.py` |

Add **`app/cache/cache_manager.py`**:
- In-memory TTL dict (no extra deps)
- `cache.get(key)` / `cache.set(key, value, ttl_seconds)`
- Prices: 5-min TTL, News: 30-min TTL, Fear & Greed: 60-min TTL

Add **`app/database/`**:
- SQLite file at `data/signals.db`
- Table: `signal_history(id, ts, ticker, score, signal, news_source, news_available)`
- Enables the `/history` endpoint and future backtesting page

---

### Phase 3 — Regime Detection & Signal Quality

**`app/services/regime.py`**:
- Uses SPY 50-day vs 200-day SMA (golden/death cross) to classify regime as `BULL`, `BEAR`, or `NEUTRAL`
- In `BEAR` regime: sell thresholds tighten (-0.10 triggers SELL instead of -0.15)
- In `BULL` regime: buy confirmation requires RSI > 50 to reduce false positives

**Signal confidence score** (new dashboard column):
- Shows how many of the 5 inputs are live (e.g., `4/5 sources active`)
- When news is unavailable: shown as `⚠ news offline` not silently ignored

---

### Phase 4 — Put/Call Ratio Fix

Current issue: CBOE blocks the Pi's IP.
Alternative sources (no Pi-block observed):
- **Alpaca Markets** market data API (free tier)
- **FRED** (Federal Reserve) — some vol data available
- **Barchart** `/free-market-data` endpoint (check rate limits)

If all fail: remove Put/Call from signal weights entirely and redistribute to VIX (40%) + RSI (20%) + Momentum (20%) + News (20%) until a reliable source is found.

---

### Phase 5 — Testing Infrastructure

The biggest gap: currently there's no way to test the full signal pipeline without real market data and real news.

**`tests/` setup:**
- `pytest` + `pytest-mock`
- Environment variable `MODE=test` already exists — extend it
- `conftest.py` loads `data/test_news.json` and patches `yfinance` with fixture prices
- `test_scoring.py` verifies that with known inputs → expected BUY/SELL/HOLD output
- CI: GitHub Actions workflow that runs tests on push (Pi stays for production)

**`data/test_news.json` format:**
```json
{
  "SPY": ["Markets rally on strong jobs data", "Fed signals pause in rate hikes"],
  "QQQ": ["Tech selloff continues as rates rise", "Nasdaq drops 2%"],
  "BTC": ["Bitcoin breaks above 50k", "Crypto fear index at extreme fear"]
}
```

---

## 5. What Changes on the Pi

```bash
# After deploying Phase 1+2:
sudo systemctl edit signal          # add NEWS_API_KEY, TELEGRAM vars
sudo systemctl daemon-reload
cd ~/signal-trader && git pull
sudo systemctl restart signal
sudo journalctl -u signal -f        # verify news_fetcher logs show a live source
```

Diagnostic page at `https://tradesignals.trade/diagnostic` should show:
- `News (Yahoo RSS): ✅`  or  `News (NewsAPI): ✅`
- `News sentiment: -0.12 (bearish lean)`

---

## 6. Priority Summary

```
Week 1:  Phase 1 — Fix news feed (new news_fetcher.py + env vars + tests)
Week 2:  Phase 2 — Refactor to v4 folder structure
Week 3:  Phase 3 — Regime detection + confidence score on dashboard
Week 4:  Phase 4 — Put/Call fix or weight redistribution
Ongoing: Phase 5 — Tests added alongside each phase
```

---

## 7. Open Questions

1. **NewsAPI key** — ✅ Configured (`NEWS_API_KEY` in `.env`)
2. **Alpha Vantage key** — ✅ Configured (`ALPHA_VANTAGE_KEY` in `.env`)
3. **Pi-hole rule** — can you add `newsapi.org` and `www.alphavantage.co` to the Pi-hole allowlist? This ensures these don't get blocked like the RSS feeds were.
4. **Telegram bot** — created via @BotFather? Need `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to enable change alerts.
