# ETF Trade Signals Bot — Project Summary

---

## What This Bot Does

This is a personal tool that watches your brokerage email account and sends trade signal alerts directly to your phone.

### The Problem It Solves

Tracking ETF positions across leveraged, inverse, and crypto funds is time-consuming. Checking charts, reading news, and deciding when to act takes discipline and attention. This bot automates that monitoring and delivers a clear recommendation — **BUY, SELL, or HOLD** — so you can act quickly without staring at screens all day.

### How It Works (Plain English)

1. **Watches your email** — The bot monitors a dedicated Gmail inbox for trade confirmation emails from your broker. When one arrives, it parses the key details automatically.

2. **Analyzes the market** — It pulls live data from several sources: market volatility (VIX), price momentum, technical indicators (RSI), options market sentiment (Put/Call ratio), and news headlines. Each factor gets a score and they're combined into a single signal.

3. **Texts you the result** — A BUY, SELL, or HOLD recommendation lands on your phone via SMS, with a link to the full dashboard for more detail.

4. **You can text it back** — Reply with simple commands to manage your tracked tickers, log manual trades, or check what's being monitored.

5. **Runs itself** — It runs 24/7 on a Raspberry Pi sitting at home. No cloud subscription, no monthly fee beyond your existing Twilio and data costs.

### What It Tracks

- Standard US ETFs
- Leveraged ETFs (2x and 3x bull)
- Inverse ETFs (for hedging)
- Crypto ETFs (Bitcoin, Ethereum, etc.)

It does **not** track individual stocks — ETFs only.

### Risk Controls Built In

- Daily loss limit (stops signaling if daily losses exceed a set threshold)
- Monthly gain target (tracks progress toward a goal)
- Monthly loss cap (circuit breaker for the month)

### SMS Commands

| You send | Bot does |
|----------|----------|
| `ADD: SOXL` | Adds SOXL to the watchlist, fetches its name and strategy |
| `REMOVE: BITX` | Removes BITX from tracking |
| `BUY: UVXY` | Logs a manual buy signal |
| `SELL: TQQQ` | Logs a manual sell signal |
| `LIST` | Returns all currently tracked tickers |

### Who Can Use It

Only phone numbers you pre-approve can send commands. Everyone else gets a 403 rejection. The web dashboard is behind Cloudflare's network — there are no open ports on your home network.

---

## Technical Details

### Architecture Overview

```
Broker Email (Gmail IMAP)
        │
        ▼
  email_parser.py  ──►  trade_manager.py (log trade, classify ticker)
                                │
                                ▼
                     Signal Computation
                     (VIX + RSI + Momentum + Put/Call + News)
                                │
                         ┌──────┴──────┐
                         ▼             ▼
                    alerts.py      Flask Dashboard
                  (Twilio SMS)    (Cloudflare Tunnel)
                         │
                         ▼
                  Your Phone (SMS)
```

### Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3 |
| Web framework | Flask + Flask-SocketIO |
| Hosting | Raspberry Pi 3B+ (systemd service) |
| Email ingestion | Gmail IMAP (`imaplib2`) |
| SMS delivery | Twilio REST API |
| Market data | `yfinance` (Yahoo Finance) |
| Data analysis | `pandas`, `matplotlib` |
| Remote access | Cloudflare Tunnel (`cloudflared`) |
| Secrets management | `python-dotenv` (`.env` file, excluded from git) |
| Storage (v3) | CSV flat files (`tickers.csv`, `trade_history.csv`) |
| Storage (v4, planned) | SQLite (`data/signals.db`) |

### Key Files

| File | Purpose |
|------|---------|
| `etf_bot.py` | Flask app entry point; SMS webhook; background scheduler |
| `config.py` | Loads all env vars; auto-detects a free port on startup |
| `email_parser.py` | Gmail IMAP polling; parses broker confirmation emails |
| `trade_manager.py` | Adds tickers via yfinance; classifies strategy; logs trade history |
| `alerts.py` | Sends SMS via Twilio; appends dashboard URL to messages |
| `requirements.txt` | Python dependencies |
| `.env` (not in git) | All credentials and runtime config |
| `SIGNAL_v4_PLAN.md` | Full v4 refactor roadmap |

### Signal Computation

Signals are scored across 5 equally-weighted inputs (20% each):

| Input | Source | Status |
|-------|--------|--------|
| VIX (volatility index) | Yahoo Finance | Active |
| RSI (relative strength index) | Calculated from price history | Active |
| Price momentum | Calculated from price history | Active |
| Put/Call ratio | CBOE | Intermittent (blocked by Pi-hole) |
| News sentiment | Multi-source (see below) | In repair (v4) |

**Scoring output:** A composite float score maps to BUY / SELL / HOLD based on configurable thresholds.

### News Sentiment — Layered Fallback Chain (v4)

The news feed was previously unreliable (Pi-hole blocking RSS feeds, missing API key). v4 implements a waterfall of sources tried in order:

```
1. Yahoo Finance RSS      ← same HTTP stack as yfinance, Pi-hole safe
2. NewsAPI.org            ← free tier (100 req/day), key now configured
3. Alpha Vantage News     ← free tier (25 req/day), key now configured
4. Finviz HTML scrape     ← lightweight, no auth required
5. Neutral fallback       ← score = 0.0, flagged as "news unavailable"
```

Each source has a 5-second timeout and catches all exceptions silently. If news is unavailable, the 20% weight is redistributed proportionally across the active indicators rather than silently dropped to zero.

### Caching (v4, planned)

An in-memory TTL cache eliminates repeated API calls within a refresh window:

| Data type | Cache TTL |
|-----------|-----------|
| Prices / RSI | 5 minutes |
| News sentiment | 30 minutes |
| Fear & Greed index | 60 minutes |

### Regime Detection (v4, planned)

SPY's 50-day vs 200-day SMA (golden/death cross) classifies the current market regime:

- **BULL** — buy confirmation requires RSI > 50 to filter false positives
- **BEAR** — SELL threshold tightens (-0.10 instead of -0.15)
- **NEUTRAL** — standard thresholds apply

### Data Storage (v4, planned)

SQLite database at `data/signals.db` with a `signal_history` table:

```
signal_history(id, ts, ticker, score, signal, news_source, news_available)
```

Enables a `/history` endpoint and future backtesting.

### Security

| Control | Implementation |
|---------|---------------|
| Secrets | `.env` file; never committed to git |
| SMS access control | Commands rejected from non-allowlisted numbers (HTTP 403) |
| Dashboard access | Cloudflare Tunnel — no open ports on home network |
| Test mode | `MODE=test` processes only emails with `TEST` in subject |

### Environment Variables

```
GMAIL_USER          Gmail address for IMAP polling
GMAIL_PASS          Gmail App Password (not account password)
TWILIO_SID          Twilio account SID
TWILIO_AUTH         Twilio auth token
TWILIO_FROM         Twilio phone number
ALLOWED_SMS_NUMBERS Comma-separated list of authorized numbers
DASHBOARD_URL       Cloudflare Tunnel public URL
LOSS_LIMIT_DAILY    Daily loss circuit breaker (default: $50)
GAIN_TARGET_MONTHLY Monthly profit goal (default: $2000)
LOSS_LIMIT_MONTHLY  Monthly loss cap (default: $500)
NEWS_API_KEY        NewsAPI.org key (free tier)
ALPHA_VANTAGE_KEY   Alpha Vantage key (free tier)
TELEGRAM_BOT_TOKEN  Telegram bot token (v4 alerts)
TELEGRAM_CHAT_ID    Telegram chat ID (v4 alerts)
MODE                "test" or "production"
```

### Deployment

The bot runs as a systemd service on Raspberry Pi OS:

```bash
# Install
git clone https://github.com/niket4u/etfTradeSignals.git
cd etfTradeSignals && ./install.sh

# Configure
cp .env.example .env && nano .env

# Run
python3 etf_bot.py   # or via systemd: systemctl start signal
```

Auto-port detection (`get_free_port()` in `config.py`) ensures the Flask server starts even if port 5000 is occupied.

### Scheduled Jobs

A background thread runs `schedule` tasks:

| Job | Frequency | Action |
|-----|-----------|--------|
| Weekly housekeeping | Saturday 11 AM | Updates ticker names & strategies from Yahoo Finance; sends SMS + email summary |

### v4 Refactor Roadmap Summary

The codebase is being reorganized from flat files into a modular structure:

```
signal-trader/
├── app/
│   ├── routes/        ← dashboard, /api/dashboard, /diagnostic
│   ├── services/      ← data_fetchers, news_fetcher, indicators, scoring, regime, alerts
│   ├── database/      ← SQLite models + storage helpers
│   └── cache/         ← TTL cache manager
├── data/              ← SQLite DB + test fixtures
├── tests/             ← pytest suite with mocked data sources
└── systemd/           ← signal.service unit file
```

**Priority order:**
1. Fix news feed (unblocks 20% signal weight) — Week 1
2. Refactor to v4 folder structure — Week 2
3. Regime detection + confidence score — Week 3
4. Put/Call ratio fix or weight redistribution — Week 4
5. Test infrastructure (ongoing alongside each phase)
