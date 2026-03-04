# Changelog — Phase 1 & Phase 2

> Changes made outside Claude Code, manually authored and pushed directly to `main`.
> Covers commits `9856715` → `c495f95` (2026-03-04).

---

## [Phase 2 — Step 3] Volatility-aware position sizing + leveraged ETF cap
**Commit:** `c495f95`

### What changed
- **`scripts/phase1_backtest_scaffold.py`** — added dynamic position sizing based on realized volatility:
  - `position_size_target = target_annual_vol / realized_vol`, clipped to max cap
  - Separate `--leveraged-max-position` cap (default `0.35`) applied to any symbol in the known leveraged set (`TQQQ`, `SOXL`, `UPRO`, etc.)
  - Non-leveraged symbols use `--max-position` (default `1.00`)
- Added per-symbol sizing diagnostics to JSON/CSV output:
  - `avg_position` — mean position size over the full period
  - `max_position_used` — peak position actually deployed
  - `position_cap` — the cap that applied (leveraged or standard)
- Portfolio summary now includes `avg_position` across all valid symbols

### New CLI flags
| Flag | Default | Description |
|------|---------|-------------|
| `--position-vol-window` | `20` | Lookback window (days) for sizing volatility estimate |
| `--target-annual-vol` | `0.18` | Target annualized vol used to scale position size |
| `--max-position` | `1.00` | Max position fraction for non-leveraged ETFs |
| `--leveraged-max-position` | `0.35` | Hard cap for 2x/3x leveraged ETFs |

### Example run
```powershell
python3 scripts/phase1_backtest_scaffold.py \
  --symbols SPY,QQQ,TQQQ,SOXL \
  --start 2022-01-01 --end 2026-03-01 \
  --target-annual-vol 0.18 \
  --leveraged-max-position 0.25
```

---

## [Phase 2 — Steps 1 & 2] Regime filter + cooldown controls
**Commit:** `e35838a`

### What changed
- **`scripts/phase1_backtest_scaffold.py`** — two new guard layers on top of the raw signal:

**Regime gate (Step 1):**
- A BUY is only executed if the symbol is in a *risk-on* regime
- Risk-on requires all three conditions simultaneously:
  1. Close price > slow SMA
  2. Fast SMA > slow SMA (golden-cross trend)
  3. Annualized 20-day realized vol ≤ `--regime-vol-max` (default `0.40`)
- BUYs blocked by the regime gate are recorded as `blocked_buy_signals` rather than silently dropped

**Cooldown / minimum hold (Step 2):**
- A SELL is only executed after the position has been held for at least `--cooldown-days` (default `5`)
- Premature SELL attempts are recorded as `blocked_sell_signals`

**Diagnostics added to output:**
- `regime_days` — count of calendar days where the regime was risk-on
- `blocked_buy_signals` — BUY signals suppressed by the regime gate
- `blocked_sell_signals` — SELL signals suppressed by the cooldown
- `model_config` block in JSON — records all parameters used for reproducibility

### New CLI flags
| Flag | Default | Description |
|------|---------|-------------|
| `--cooldown-days` | `5` | Minimum hold days before a SELL is allowed |
| `--regime-fast-ma` | `50` | Fast SMA window for regime trend check |
| `--regime-slow-ma` | `200` | Slow SMA window for regime trend check |
| `--regime-vol-window` | `20` | Rolling window for annualized vol in regime check |
| `--regime-vol-max` | `0.40` | Max annualized vol threshold for risk-on |

### Example run
```powershell
python3 scripts/phase1_backtest_scaffold.py \
  --symbols SPY,QQQ,TQQQ \
  --start 2022-01-01 --end 2026-03-01 \
  --cooldown-days 5 \
  --regime-vol-max 0.35
```

---

## [Phase 1] Fix: yfinance close-series normalization
**Commit:** `e510cd8`

### What changed
- **`scripts/phase1_backtest_scaffold.py`** — added `normalize_close_series()` helper
- Handles all yfinance `Close` return shapes safely:
  - `pd.DataFrame` (multi-column or single-column) → takes `iloc[:, 0]`
  - `pd.Series` → used directly
  - Scalar or unexpected type → returns empty Series (symbol skipped gracefully)
- `pd.to_numeric(..., errors="coerce").dropna()` strips any non-numeric rows before computation
- Prevents the `ValueError: If using all scalar values, you must pass an index` crash that occurred with newer yfinance versions

---

## [Phase 1] Initial implementation plan, baseline metrics, and backtest scaffold
**Commit:** `9856715`

### Files added

#### `docs/IMPLEMENTATION_PLAN.md`
Phase 1 planning document covering:
- Objectives and deliverables
- Baseline metric definitions
- Backtest scaffold design (momentum + RSI proxy, long-only, transaction costs)
- Runbook with exact commands
- Exit criteria and Phase 2 next steps

#### `scripts/phase1_baseline_metrics.py`
Reads existing app artifacts and produces a reproducible baseline snapshot.

**Inputs:**
- `tickers.csv` — watchlist (default path, overridable)
- `trade_history.csv` — trade log (default path, overridable)

**Outputs** (written to `reports/phase1/`):
- `baseline_metrics_latest.json` + timestamped archive copy
- `baseline_metrics_latest.csv` + timestamped archive copy

**Metrics computed:**

| Metric | Description |
|--------|-------------|
| `watchlist_count` | Number of tickers in `tickers.csv` |
| `trade_count_total` | Total rows in trade history |
| `trade_count_buy` / `trade_count_sell` | Directional split |
| `trade_count_manual` / `trade_count_email` | Source split |
| `buy_sell_ratio` | `buy / sell` (null if no sells) |
| `active_trade_days` | Distinct calendar days with any trade |
| `avg_trades_per_active_day` | `total / active_days` |
| `signal_flip_rate` | BUY↔SELL direction changes per ticker sequence |
| `top_traded_tickers` | Top 10 by trade count |
| `avg_confidence` / `false_buy_rate` / `false_sell_rate` | `null` — not yet persisted in logs |

**Run:**
```powershell
python3 scripts/phase1_baseline_metrics.py
# or with custom paths:
python3 scripts/phase1_baseline_metrics.py \
  --tickers tickers.csv \
  --trade-history trade_history.csv \
  --output-dir reports/phase1
```

---

#### `scripts/phase1_backtest_scaffold.py`
Simulation engine for historical signal evaluation.

**Signal model (proxy for production logic):**
- Momentum factor: 5-day price return, scaled and clipped to `[-1, +1]`
- RSI factor: 14-day RSI mapped piecewise to `[-0.6, +0.6]` normalized score
- Combined score: `0.5 × momentum + 0.5 × rsi_norm`
- Thresholds: score > 0.15 → BUY, score < -0.15 → SELL, else HOLD

**Simulation rules:**
- Long-only (BUY = enter, SELL = exit to cash, HOLD = maintain)
- Transaction cost: `position_change × (bps / 10000)`, default 2 bps

**Outputs** (written to `reports/phase1/`):
- Per-symbol JSON + portfolio summary + benchmark comparison
- CSV row per symbol with all stats

**Per-symbol stats:**
- `total_return`, `cagr`, `max_drawdown`, `sharpe_like`, `trade_count`
- `rows`, `regime_days`, `blocked_buy_signals`, `blocked_sell_signals`
- `avg_position`, `max_position_used`, `position_cap`

**Benchmark:** SPY buy-and-hold (default, overridable with `--benchmark`)

**Run:**
```powershell
# Quick run
python3 scripts/phase1_backtest_scaffold.py \
  --symbols SPY,QQQ,TQQQ \
  --start 2022-01-01 --end 2026-03-01

# From tickers.csv
python3 scripts/phase1_backtest_scaffold.py \
  --symbols-file tickers.csv \
  --start 2022-01-01 --end 2026-03-01 \
  --benchmark SPY \
  --transaction-cost-bps 2.0
```

---

#### `requirements.txt` — dependency additions
| Package | Reason |
|---------|--------|
| `numpy` | Required by backtest computations (vol, Sharpe, array ops) |
| `textblob` | Required by planned news sentiment scoring (v4) |

---

## Full run commands (copy-paste)

```powershell
# Step 1: generate baseline metrics from current app files
python3 scripts/phase1_baseline_metrics.py

# Step 2: run the full Phase 2 backtest with all controls enabled
python3 scripts/phase1_backtest_scaffold.py \
  --symbols SPY,QQQ,TQQQ,SOXL,SOXS,UVXY \
  --start 2022-01-01 --end 2026-03-01 \
  --cooldown-days 5 \
  --regime-fast-ma 50 \
  --regime-slow-ma 200 \
  --regime-vol-max 0.40 \
  --target-annual-vol 0.18 \
  --max-position 1.00 \
  --leveraged-max-position 0.35 \
  --transaction-cost-bps 2.0

# Step 3: run from your existing watchlist
python3 scripts/phase1_backtest_scaffold.py \
  --symbols-file tickers.csv \
  --start 2022-01-01 --end 2026-03-01
```

Reports land in `reports/phase1/` with timestamped filenames for archiving.

---

## Commit reference

| SHA | Message |
|-----|---------|
| `c495f95` | Add volatility-based position sizing and leveraged ETF cap |
| `e35838a` | Add regime filter and cooldown controls to backtest scaffold |
| `e510cd8` | Fix backtest scaffold close-series normalization for yfinance outputs |
| `9856715` | Add Phase 1 implementation plan, baseline metrics, and backtest scaffold |
