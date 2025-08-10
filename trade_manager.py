import csv
import yfinance as yf
from datetime import datetime

TICKERS_FILE = "tickers.csv"
TRADE_HISTORY_FILE = "trade_history.csv"

def add_ticker(symbol):
    ticker_info = yf.Ticker(symbol)
    name = ticker_info.info.get("shortName", "Unknown")
    strategy = classify_strategy(name)
    with open(TICKERS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([symbol, name, strategy])
    return name, strategy

def classify_strategy(name):
    name_lower = name.lower()
    if "3x" in name_lower or "triple" in name_lower:
        return "3x Bull"
    elif "2x" in name_lower:
        return "2x Bull"
    elif "bear" in name_lower or "inverse" in name_lower:
        return "Inverse"
    elif "bitcoin" in name_lower or "crypto" in name_lower:
        return "Crypto ETF"
    return "Unknown"

def log_trade(trade_type, ticker, price, qty, source="manual"):
    with open(TRADE_HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([datetime.now().isoformat(), trade_type, ticker, price, qty, source])
