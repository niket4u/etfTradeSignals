# ETF SIGNAL Phase 1 Implementation Plan

## Objective
Phase 1 establishes a measurable baseline before model changes.

Primary goals:
- define and compute baseline metrics from current app artifacts
- add a first backtest scaffold that can be iterated into model v2
- produce reproducible reports for comparison after each change

## Phase 1 Deliverables
1. Baseline metrics collector script:
   - input: `tickers.csv`, `trade_history.csv`
   - output: JSON + CSV summaries under `reports/phase1/`
2. Backtest scaffold script:
   - input: symbols list and date range
   - output: per-symbol and portfolio stats under `reports/phase1/`
3. This plan document and run commands

## Baseline Metrics (v1)
From current files/logs:
- watchlist_count
- trade_count_total
- trade_count_buy
- trade_count_sell
- trade_count_manual
- trade_count_email
- buy_sell_ratio
- active_trade_days
- avg_trades_per_active_day
- signal_flip_rate
- top_traded_tickers

Notes:
- confidence and signal quality metrics are not yet persisted in logs, so they are listed as `null` placeholders.

## Backtest Scaffold (v1)
The first scaffold uses daily OHLC data and a simple signal proxy:
- momentum factor: 5-day percent change
- RSI factor: 14-day RSI transformed to normalized score
- combined score and thresholds map to BUY/HOLD/SELL
- long-only position logic (`BUY -> in market`, `SELL -> cash`, `HOLD -> keep prior`)
- transaction cost modeled per position change

Outputs:
- per-symbol metrics: return, CAGR, max drawdown, sharpe-like score, trade count
- equal-weight portfolio metrics
- benchmark metrics (`SPY` buy-and-hold)

This is a baseline engine, not the final signal model.

## Runbook
From repository root:

```powershell
python3 scripts/phase1_baseline_metrics.py
python3 scripts/phase1_backtest_scaffold.py --symbols SPY,QQQ,TQQQ --start 2022-01-01 --end 2026-03-01
```

Optional custom inputs:

```powershell
python3 scripts/phase1_baseline_metrics.py --tickers tickers.csv --trade-history trade_history.csv
python3 scripts/phase1_backtest_scaffold.py --symbols-file tickers.csv --benchmark SPY
```

## Exit Criteria for Phase 1
1. Scripts run without code edits on local machine.
2. Reports are generated under `reports/phase1/`.
3. Baseline metrics and backtest outputs can be reused for later A/B comparisons.

## Next Step (Phase 2)
- wire signal computation logs to persistent storage (SQLite suggested)
- backtest real app signal logic (`build_signal`) with historical factor reconstruction
- add evaluation dashboard for side-by-side model versions

