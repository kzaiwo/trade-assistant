# Codex Implementation Prompt

Read ARCHITECTURE.md for the full design. Implement the trade-assistant project following that architecture exactly.

## Data
- Source: `../_trade_data/{SYMBOL}/{SYMBOL}_*.json` (1-min OHLCV bars with pre-computed fields)
- Available symbols: AAPL, TSLA, MU, INTC
- Backtest date range: June 15-19, 2026 only

## Build order — implement and backtest ONE at a time

Build the core infrastructure first (models/types.py, data/, indicators/base.py, signals/base.py, strategies/base.py, runner/backtest.py, journal/logger.py, notifiers/console.py, config.py, main.py).

Then implement each indicator + signal + strategy individually. After each one is working, run a backtest on all 4 symbols for June 15-19 and record the results before moving to the next:

1. Bollinger Bands indicator → bb_squeeze signal → backtest → record results
2. Stochastic RSI indicator → stoch_cross signal → backtest → record results
3. VWAP indicator → vwap_bounce signal → backtest → record results
4. MACD indicator → macd_cross signal → backtest → record results
5. Combine all four into the mean_reversion strategy → backtest → record results

## Backtest rules
- Entry on BUY signal, exit on SELL signal (or opposite direction signal)
- Track each trade: entry/exit time, entry/exit price, direction, confidence, P&L
- No overlapping trades per symbol — must exit before re-entering
- Use close price for entries and exits

## Results file
After each backtest, append results to `results/backtest_summary.json` with this structure per run:

```json
{
    "strategy": "bb_squeeze",
    "date_range": "2026-06-15 to 2026-06-19",
    "symbols": ["AAPL", "TSLA", "MU", "INTC"],
    "per_symbol": {
        "AAPL": {
            "total_trades": 12,
            "wins": 8,
            "losses": 4,
            "win_rate": 0.667,
            "total_pnl": 3.45,
            "total_pnl_pct": 1.12,
            "avg_confidence": 0.75,
            "trades": [...]
        }
    },
    "overall": {
        "total_trades": 48,
        "wins": 30,
        "losses": 18,
        "win_rate": 0.625,
        "total_pnl": 12.30,
        "total_pnl_pct": 0.98
    }
}
```

Also generate a human-readable `results/backtest_summary.txt` with a comparison table at the end showing all strategies side by side ranked by win rate and P&L.

## Important
- Follow ARCHITECTURE.md class signatures and structure exactly
- Each indicator/signal in its own file with metadata (name, display_name, description, category)
- Use the @register decorator for signals
- Recompute indicators from OHLCV — don't use pre-computed fields from the JSON
- Keep it clean and readable
- If you need architectural advice or are unsure about a design decision, consult Claude (the architect for this project) before proceeding
