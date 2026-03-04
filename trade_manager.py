import csv
import yfinance as yf
from datetime import datetime

TICKERS_FILE       = "tickers.csv"
TRADE_HISTORY_FILE = "trade_history.csv"
POSITIONS_FILE     = "open_positions.csv"
MONTHLY_PNL_FILE   = "monthly_pnl.csv"


# ── Ticker watchlist ──────────────────────────────────────────────────────────
def add_ticker(symbol):
    ticker_info = yf.Ticker(symbol)
    name        = ticker_info.info.get("shortName", "Unknown")
    strategy    = classify_strategy(name)
    try:
        with open(TICKERS_FILE, "rb") as f:
            f.seek(-1, 2)
            needs_newline = f.read(1) != b"\n"
    except (OSError, IOError):
        needs_newline = False
    with open(TICKERS_FILE, "a", newline="") as f:
        if needs_newline:
            f.write("\n")
        csv.writer(f).writerow([symbol, name, strategy])
    return name, strategy

def classify_strategy(name):
    n = name.lower()
    if "3x" in n or "triple" in n: return "3x Bull"
    if "2x" in n:                  return "2x Bull"
    if "bear" in n or "inverse" in n: return "Inverse"
    if "bitcoin" in n or "crypto" in n: return "Crypto ETF"
    return "Unknown"

def log_trade(trade_type, ticker, price, qty, source="manual"):
    with open(TRADE_HISTORY_FILE, "a", newline="") as f:
        csv.writer(f).writerow(
            [datetime.now().isoformat(), trade_type, ticker, price, qty, source])


# ── Portfolio positions ───────────────────────────────────────────────────────
def get_positions():
    """Return list of dicts: {ticker, qty, avg_price}"""
    rows = []
    try:
        with open(POSITIONS_FILE, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    rows.append({
                        "ticker":    row["Ticker"].strip().upper(),
                        "qty":       float(row["Quantity"]),
                        "avg_price": float(row["AvgPrice"]),
                    })
                except (ValueError, KeyError):
                    pass
    except FileNotFoundError:
        pass
    return rows

def save_positions(positions):
    """Overwrite positions file with list of dicts."""
    with open(POSITIONS_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Ticker", "Quantity", "AvgPrice"])
        for p in positions:
            w.writerow([p["ticker"], p["qty"], p["avg_price"]])

def upsert_position(ticker, qty, avg_price):
    """Add new position or update existing one (weighted average price)."""
    ticker    = ticker.strip().upper()
    positions = get_positions()
    for p in positions:
        if p["ticker"] == ticker:
            total_cost  = p["qty"] * p["avg_price"] + qty * avg_price
            p["qty"]       += qty
            p["avg_price"]  = round(total_cost / p["qty"], 4) if p["qty"] else 0
            save_positions(positions)
            return p
    positions.append({"ticker": ticker, "qty": qty, "avg_price": avg_price})
    save_positions(positions)
    return positions[-1]

def remove_position(ticker):
    ticker    = ticker.strip().upper()
    positions = [p for p in get_positions() if p["ticker"] != ticker]
    save_positions(positions)

def clear_positions():
    save_positions([])


# ── Monthly P&L ───────────────────────────────────────────────────────────────
def get_monthly_pnl():
    """Return list of dicts: {month, total_value, gain, gain_pct}"""
    rows = []
    try:
        with open(MONTHLY_PNL_FILE, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    rows.append({
                        "month":       row["Month"],
                        "total_value": float(row["TotalValue"]),
                        "gain":        float(row["Gain"]),
                        "gain_pct":    float(row["GainPct"]),
                    })
                except (ValueError, KeyError):
                    pass
    except FileNotFoundError:
        pass
    return rows

def record_monthly_pnl(month, total_value, gain, gain_pct):
    """Append or update a monthly P&L record."""
    rows = get_monthly_pnl()
    for r in rows:
        if r["month"] == month:
            r["total_value"] = total_value
            r["gain"]        = gain
            r["gain_pct"]    = gain_pct
            _save_monthly(rows)
            return
    rows.append({"month": month, "total_value": total_value,
                 "gain": gain, "gain_pct": gain_pct})
    _save_monthly(rows)

def clear_monthly_pnl():
    _save_monthly([])

def _save_monthly(rows):
    with open(MONTHLY_PNL_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Month", "TotalValue", "Gain", "GainPct"])
        for r in rows:
            w.writerow([r["month"], r["total_value"], r["gain"], r["gain_pct"]])
