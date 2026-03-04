import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class BaselineSummary:
    generated_at: str
    watchlist_count: int
    trade_count_total: int
    trade_count_buy: int
    trade_count_sell: int
    trade_count_manual: int
    trade_count_email: int
    buy_sell_ratio: Optional[float]
    active_trade_days: int
    avg_trades_per_active_day: float
    signal_flip_rate: Optional[float]
    top_traded_tickers: List[Dict[str, int]]
    avg_confidence: Optional[float]
    false_buy_rate: Optional[float]
    false_sell_rate: Optional[float]


def read_watchlist_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return len(rows)


def read_trades(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def safe_iso_date(value: str) -> Optional[str]:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except Exception:
        return None


def compute_flip_rate(trades: List[dict]) -> Optional[float]:
    if len(trades) < 2:
        return None

    # Flip rate is measured on directional changes per ticker sequence.
    by_ticker = {}
    for trade in trades:
        ticker = (trade.get("Ticker") or "").upper().strip()
        side = (trade.get("Type") or "").upper().strip()
        if ticker and side in {"BUY", "SELL"}:
            by_ticker.setdefault(ticker, []).append(side)

    transitions = 0
    flips = 0
    for sequence in by_ticker.values():
        for index in range(1, len(sequence)):
            transitions += 1
            if sequence[index] != sequence[index - 1]:
                flips += 1

    if transitions == 0:
        return None
    return round(float(flips) / float(transitions), 4)


def build_summary(tickers_path: Path, trades_path: Path) -> BaselineSummary:
    trades = read_trades(trades_path)

    side_counter = Counter((t.get("Type") or "").upper().strip() for t in trades)
    source_counter = Counter((t.get("Source") or "unknown").lower().strip() for t in trades)
    ticker_counter = Counter((t.get("Ticker") or "").upper().strip() for t in trades if t.get("Ticker"))

    trade_dates = {safe_iso_date(t.get("Date", "")) for t in trades}
    trade_dates.discard(None)

    buy_count = side_counter.get("BUY", 0)
    sell_count = side_counter.get("SELL", 0)
    ratio = None if sell_count == 0 else round(float(buy_count) / float(sell_count), 4)

    active_days = len(trade_dates)
    avg_trades = 0.0 if active_days == 0 else round(float(len(trades)) / float(active_days), 4)

    top_tickers = [
        {"ticker": symbol, "trade_count": count}
        for symbol, count in ticker_counter.most_common(10)
    ]

    return BaselineSummary(
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        watchlist_count=read_watchlist_count(tickers_path),
        trade_count_total=len(trades),
        trade_count_buy=buy_count,
        trade_count_sell=sell_count,
        trade_count_manual=source_counter.get("manual", 0),
        trade_count_email=source_counter.get("email", 0),
        buy_sell_ratio=ratio,
        active_trade_days=active_days,
        avg_trades_per_active_day=avg_trades,
        signal_flip_rate=compute_flip_rate(trades),
        top_traded_tickers=top_tickers,
        avg_confidence=None,
        false_buy_rate=None,
        false_sell_rate=None,
    )


def write_outputs(summary: BaselineSummary, output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / "baseline_metrics_latest.json"
    csv_path = output_dir / "baseline_metrics_latest.csv"
    archive_json_path = output_dir / ("baseline_metrics_" + timestamp + ".json")
    archive_csv_path = output_dir / ("baseline_metrics_" + timestamp + ".csv")

    payload = asdict(summary)

    for path in (json_path, archive_json_path):
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    for path in (csv_path, archive_csv_path):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "value"])
            for key, value in payload.items():
                if key == "top_traded_tickers":
                    writer.writerow([key, json.dumps(value)])
                else:
                    writer.writerow([key, value])

    return archive_json_path, archive_csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1 baseline metrics collector")
    parser.add_argument("--tickers", default="tickers.csv", help="Path to tickers.csv")
    parser.add_argument("--trade-history", default="trade_history.csv", help="Path to trade_history.csv")
    parser.add_argument("--output-dir", default="reports/phase1", help="Directory for generated reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary(Path(args.tickers), Path(args.trade_history))
    json_path, csv_path = write_outputs(summary, Path(args.output_dir))

    print("Phase 1 baseline metrics generated")
    print("- JSON: " + str(json_path))
    print("- CSV:  " + str(csv_path))
    print("- Trades: " + str(summary.trade_count_total))
    print("- Watchlist: " + str(summary.watchlist_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
