import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


def normalize_close_series(prices) -> pd.Series:
    """
    Normalize yfinance close output into a 1D float Series.
    yfinance may return Series, DataFrame (including multi-column), or unexpected scalar-like values.
    """
    if isinstance(prices, pd.DataFrame):
        if prices.empty:
            return pd.Series(dtype="float64")
        series = prices.iloc[:, 0]
    elif isinstance(prices, pd.Series):
        series = prices
    elif np.isscalar(prices):
        return pd.Series(dtype="float64")
    else:
        try:
            series = pd.Series(prices)
        except Exception:
            return pd.Series(dtype="float64")

    series = pd.to_numeric(series, errors="coerce").dropna()
    return series.astype("float64")


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


def compute_signals(prices, regime_fast_ma: int, regime_slow_ma: int, regime_vol_window: int, regime_vol_max: float, cooldown_days: int) -> pd.DataFrame:
    close = normalize_close_series(prices)
    if close.empty:
        return pd.DataFrame(columns=[
            "close", "ret", "momentum", "rsi", "rsi_norm", "score", "raw_signal",
            "regime_risk_on", "signal", "position", "blocked_buy", "blocked_sell"
        ])

    frame = close.to_frame(name="close")
    frame["ret"] = frame["close"].pct_change().fillna(0)
    frame["momentum"] = (frame["close"].pct_change(5).fillna(0) * 10).clip(-1, 1)
    frame["rsi"] = compute_rsi(frame["close"])
    frame["rsi_norm"] = rsi_to_normalized_score(frame["rsi"])

    frame["score"] = (0.5 * frame["momentum"]) + (0.5 * frame["rsi_norm"])

    frame["raw_signal"] = "HOLD"
    frame.loc[frame["score"] > 0.15, "raw_signal"] = "BUY"
    frame.loc[frame["score"] < -0.15, "raw_signal"] = "SELL"

    frame["sma_fast"] = frame["close"].rolling(regime_fast_ma).mean()
    frame["sma_slow"] = frame["close"].rolling(regime_slow_ma).mean()
    frame["vol_ann"] = frame["ret"].rolling(regime_vol_window).std() * np.sqrt(252)

    # Risk-on regime requires positive trend and capped annualized volatility.
    frame["regime_risk_on"] = (
        (frame["close"] > frame["sma_slow"]) &
        (frame["sma_fast"] > frame["sma_slow"]) &
        (frame["vol_ann"] <= regime_vol_max)
    ).fillna(False)

    frame["signal"] = frame["raw_signal"]
    blocked_buy = (frame["signal"] == "BUY") & (~frame["regime_risk_on"])
    frame.loc[blocked_buy, "signal"] = "HOLD"

    # Long-only with cooldown: BUY enters, SELL exits only after minimum hold period.
    position = []
    blocked_sell = []
    current = 0
    days_in_position = 0

    for signal in frame["signal"]:
        blocked_this_sell = 0

        if current == 1:
            days_in_position += 1

        if signal == "BUY" and current == 0:
            current = 1
            days_in_position = 0
        elif signal == "SELL" and current == 1:
            if days_in_position >= cooldown_days:
                current = 0
                days_in_position = 0
            else:
                blocked_this_sell = 1

        position.append(current)
        blocked_sell.append(blocked_this_sell)

    frame["position"] = pd.Series(position, index=frame.index)
    frame["blocked_buy"] = blocked_buy.astype(int)
    frame["blocked_sell"] = pd.Series(blocked_sell, index=frame.index)

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


def run_symbol_backtest(
    symbol: str,
    start: str,
    end: str,
    transaction_cost_bps: float,
    regime_fast_ma: int,
    regime_slow_ma: int,
    regime_vol_window: int,
    regime_vol_max: float,
    cooldown_days: int,
) -> dict:
    data = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if data.empty or "Close" not in data.columns:
        return {"symbol": symbol, "error": "No price data"}

    signals = compute_signals(
        data["Close"],
        regime_fast_ma=regime_fast_ma,
        regime_slow_ma=regime_slow_ma,
        regime_vol_window=regime_vol_window,
        regime_vol_max=regime_vol_max,
        cooldown_days=cooldown_days,
    )

    if signals.empty:
        return {"symbol": symbol, "error": "No usable close data"}

    position_change = signals["position"].diff().abs().fillna(0)
    tx_cost = position_change * (transaction_cost_bps / 10000.0)

    strat_ret = (signals["position"].shift(1).fillna(0) * signals["ret"]) - tx_cost
    strat_equity = (1 + strat_ret).cumprod()

    out = {
        "symbol": symbol,
        "stats": compute_stats(strat_equity, strat_ret, int(position_change.sum())),
        "rows": int(len(signals)),
        "regime_days": int(signals["regime_risk_on"].sum()),
        "blocked_buy_signals": int(signals["blocked_buy"].sum()),
        "blocked_sell_signals": int(signals["blocked_sell"].sum()),
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
    parser = argparse.ArgumentParser(description="Phase 2 scaffold backtest (regime + cooldown)")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. SPY,QQQ,TQQQ")
    parser.add_argument("--symbols-file", default="tickers.csv", help="CSV file with Ticker column")
    parser.add_argument("--start", default="2022-01-01", help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.utcnow().date().isoformat(), help="Backtest end date YYYY-MM-DD")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol")
    parser.add_argument("--transaction-cost-bps", type=float, default=2.0, help="Cost per position change in basis points")
    parser.add_argument("--cooldown-days", type=int, default=5, help="Minimum hold days before a SELL can exit")
    parser.add_argument("--regime-fast-ma", type=int, default=50, help="Fast moving average window for regime filter")
    parser.add_argument("--regime-slow-ma", type=int, default=200, help="Slow moving average window for regime filter")
    parser.add_argument("--regime-vol-window", type=int, default=20, help="Volatility lookback window for regime filter")
    parser.add_argument("--regime-vol-max", type=float, default=0.40, help="Max annualized volatility allowed for risk-on")
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
        writer.writerow([
            "symbol", "total_return", "cagr", "max_drawdown", "sharpe_like", "trade_count",
            "rows", "regime_days", "blocked_buy_signals", "blocked_sell_signals", "error"
        ])
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
                row.get("regime_days"),
                row.get("blocked_buy_signals"),
                row.get("blocked_sell_signals"),
                row.get("error", ""),
            ])

    return json_path, csv_path


def main() -> int:
    args = parse_args()
    symbols = resolve_symbols(args)

    symbol_results = []
    for symbol in symbols:
        symbol_results.append(run_symbol_backtest(
            symbol,
            args.start,
            args.end,
            args.transaction_cost_bps,
            args.regime_fast_ma,
            args.regime_slow_ma,
            args.regime_vol_window,
            args.regime_vol_max,
            args.cooldown_days,
        ))

    valid = [r for r in symbol_results if "stats" in r]

    portfolio_summary = {}
    if valid:
        portfolio_summary = {
            "avg_total_return": round(float(np.mean([r["stats"]["total_return"] for r in valid])), 6),
            "avg_cagr": round(float(np.mean([r["stats"]["cagr"] for r in valid])), 6),
            "avg_max_drawdown": round(float(np.mean([r["stats"]["max_drawdown"] for r in valid])), 6),
            "avg_sharpe_like": round(float(np.mean([r["stats"]["sharpe_like"] for r in valid])), 6),
            "avg_blocked_buy_signals": round(float(np.mean([r.get("blocked_buy_signals", 0) for r in valid])), 2),
            "avg_blocked_sell_signals": round(float(np.mean([r.get("blocked_sell_signals", 0) for r in valid])), 2),
            "symbol_count": len(valid),
        }

    benchmark_result = run_symbol_backtest(
        args.benchmark.upper(),
        args.start,
        args.end,
        0.0,
        args.regime_fast_ma,
        args.regime_slow_ma,
        args.regime_vol_window,
        args.regime_vol_max,
        args.cooldown_days,
    )

    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "start": args.start,
        "end": args.end,
        "transaction_cost_bps": args.transaction_cost_bps,
        "model_config": {
            "cooldown_days": args.cooldown_days,
            "regime_fast_ma": args.regime_fast_ma,
            "regime_slow_ma": args.regime_slow_ma,
            "regime_vol_window": args.regime_vol_window,
            "regime_vol_max": args.regime_vol_max,
        },
        "symbols": symbol_results,
        "portfolio_summary": portfolio_summary,
        "benchmark": benchmark_result,
        "notes": [
            "Phase 2 step 1: regime filter blocks BUY during risk-off periods.",
            "Phase 2 step 2: cooldown prevents early SELL exits before minimum hold days.",
            "Scaffold model still uses momentum + RSI proxy, not full production signal stack.",
        ],
    }

    json_path, csv_path = write_outputs(payload, Path(args.output_dir))

    print("Phase 2 backtest scaffold generated (regime + cooldown)")
    print(f"- JSON: {json_path}")
    print(f"- CSV:  {csv_path}")
    print(f"- Symbols tested: {len(symbols)}")
    print(f"- Valid results: {len(valid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
