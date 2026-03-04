import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gains / losses.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def rsi_to_normalized_score(rsi: pd.Series) -> pd.Series:
    # Mirrors the rough piecewise shape used in the app signal logic.
    out = pd.Series(0.0, index=rsi.index)
    out = out.mask(rsi < 30, 0.6)
    out = out.mask((rsi >= 30) & (rsi < 45), 0.2)
    out = out.mask((rsi >= 55) & (rsi < 70), -0.2)
    out = out.mask(rsi >= 70, -0.6)
    return out


def compute_signals(prices: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({"close": prices.dropna()})
    frame["ret"] = frame["close"].pct_change().fillna(0)
    frame["momentum"] = (frame["close"].pct_change(5).fillna(0) * 10).clip(-1, 1)
    frame["rsi"] = compute_rsi(frame["close"])
    frame["rsi_norm"] = rsi_to_normalized_score(frame["rsi"])

    frame["score"] = (0.5 * frame["momentum"]) + (0.5 * frame["rsi_norm"])

    frame["signal"] = "HOLD"
    frame.loc[frame["score"] > 0.15, "signal"] = "BUY"
    frame.loc[frame["score"] < -0.15, "signal"] = "SELL"

    # Long-only scaffold: BUY enters, SELL exits, HOLD keeps prior state.
    position = []
    current = 0
    for signal in frame["signal"]:
        if signal == "BUY":
            current = 1
        elif signal == "SELL":
            current = 0
        position.append(current)

    frame["position"] = pd.Series(position, index=frame.index)
    return frame


def compute_stats(equity: pd.Series, daily_ret: pd.Series, trades: int) -> dict:
    if equity.empty:
        return {}

    total_return = float(equity.iloc[-1] - 1)
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    cagr = float((equity.iloc[-1] ** (365.0 / days)) - 1)

    roll_max = equity.cummax()
    drawdown = (equity / roll_max) - 1
    max_dd = float(drawdown.min())

    vol = float(daily_ret.std())
    sharpe_like = 0.0 if vol == 0 else float((daily_ret.mean() / vol) * np.sqrt(252))

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe_like": round(sharpe_like, 6),
        "trade_count": int(trades),
    }


def run_symbol_backtest(symbol: str, start: str, end: str, transaction_cost_bps: float) -> dict:
    data = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if data.empty or "Close" not in data.columns:
        return {"symbol": symbol, "error": "No price data"}

    signals = compute_signals(data["Close"])
    position_change = signals["position"].diff().abs().fillna(0)
    tx_cost = position_change * (transaction_cost_bps / 10000.0)

    strat_ret = (signals["position"].shift(1).fillna(0) * signals["ret"]) - tx_cost
    strat_equity = (1 + strat_ret).cumprod()

    out = {
        "symbol": symbol,
        "stats": compute_stats(strat_equity, strat_ret, int(position_change.sum())),
        "rows": int(len(signals)),
    }
    return out


def extract_symbols_from_tickers_csv(path: Path) -> list:
    if not path.exists():
        return []
    symbols = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("Ticker") or "").strip().upper()
            if symbol:
                symbols.append(symbol)
    return sorted(set(symbols))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1 backtest scaffold")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. SPY,QQQ,TQQQ")
    parser.add_argument("--symbols-file", default="tickers.csv", help="CSV file with Ticker column")
    parser.add_argument("--start", default="2022-01-01", help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.utcnow().date().isoformat(), help="Backtest end date YYYY-MM-DD")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol")
    parser.add_argument("--transaction-cost-bps", type=float, default=2.0, help="Cost per position change in basis points")
    parser.add_argument("--output-dir", default="reports/phase1", help="Directory for generated reports")
    return parser.parse_args()


def resolve_symbols(args: argparse.Namespace) -> list:
    if args.symbols:
        parsed = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return sorted(set(parsed))

    from_file = extract_symbols_from_tickers_csv(Path(args.symbols_file))
    if from_file:
        return from_file

    return ["SPY"]


def write_outputs(payload: dict, output_dir: Path) -> tuple:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"backtest_scaffold_{ts}.json"
    csv_path = output_dir / f"backtest_scaffold_{ts}.csv"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "total_return", "cagr", "max_drawdown", "sharpe_like", "trade_count", "rows", "error"])
        for row in payload["symbols"]:
            stats = row.get("stats", {})
            writer.writerow([
                row.get("symbol"),
                stats.get("total_return"),
                stats.get("cagr"),
                stats.get("max_drawdown"),
                stats.get("sharpe_like"),
                stats.get("trade_count"),
                row.get("rows"),
                row.get("error", ""),
            ])

    return json_path, csv_path


def main() -> int:
    args = parse_args()
    symbols = resolve_symbols(args)

    symbol_results = []
    for symbol in symbols:
        symbol_results.append(run_symbol_backtest(symbol, args.start, args.end, args.transaction_cost_bps))

    valid = [r for r in symbol_results if "stats" in r]

    portfolio_summary = {}
    if valid:
        portfolio_summary = {
            "avg_total_return": round(float(np.mean([r["stats"]["total_return"] for r in valid])), 6),
            "avg_cagr": round(float(np.mean([r["stats"]["cagr"] for r in valid])), 6),
            "avg_max_drawdown": round(float(np.mean([r["stats"]["max_drawdown"] for r in valid])), 6),
            "avg_sharpe_like": round(float(np.mean([r["stats"]["sharpe_like"] for r in valid])), 6),
            "symbol_count": len(valid),
        }

    benchmark_result = run_symbol_backtest(args.benchmark.upper(), args.start, args.end, 0.0)

    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "start": args.start,
        "end": args.end,
        "transaction_cost_bps": args.transaction_cost_bps,
        "symbols": symbol_results,
        "portfolio_summary": portfolio_summary,
        "benchmark": benchmark_result,
        "notes": [
            "Scaffold model uses momentum + RSI proxy, not full production signal stack.",
            "Use this baseline to compare changes before switching to model v2.",
        ],
    }

    json_path, csv_path = write_outputs(payload, Path(args.output_dir))

    print("Phase 1 backtest scaffold generated")
    print(f"- JSON: {json_path}")
    print(f"- CSV:  {csv_path}")
    print(f"- Symbols tested: {len(symbols)}")
    print(f"- Valid results: {len(valid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
