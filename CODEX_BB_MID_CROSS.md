# Codex: Implement bb_mid_cross Strategy

Read ARCHITECTURE.md for the project design. Follow the existing patterns exactly. If unsure about any architectural decision, consult Claude before proceeding.

## Overview

Add a new strategy called "Bollinger Midline Cross" — enters when price crosses the BB middle line with directional confirmation, filtered by an anti-chop detector.

## Step 1: Extend Bollinger Bands Indicator

File: `indicators/bollinger_bands.py`

Add these columns to the existing `compute()` method (do not create a new indicator file):
- `bb_mid_slope`: `bb_mid[i] - bb_mid[i-1]`
- `bb_mid_flat`: boolean, `abs(bb_mid_slope) <= close * 0.0002`
- `bb_bandwidth`: `(bb_upper - bb_lower) / bb_mid`
- `bb_bandwidth_expanding`: boolean, `bb_bandwidth[i] > bb_bandwidth[i-1]`

## Step 2: New Signal — BBMidCross

File: `signals/bb_mid_cross.py`

```python
@register
class BBMidCross(Signal):
    name = "bb_mid_cross"
    display_name = "Bollinger Midline Cross"
    description = "Detects price crossing the BB middle line with directional confirmation from BB mid slope."
    category = "mean_reversion"
    required_indicators = [BollingerBands]
    default_params = {"body_threshold": 0.5}  # for non-1m majority-body test
    cooldown_bars = 1  # use existing cooldown mechanism
```

### Evaluation logic

**For 1m timeframe:**
- BUY: previous candle close < bb_mid, current candle close > bb_mid, bb_mid_slope turns upward on current candle
- SELL: previous candle close > bb_mid, current candle close < bb_mid, bb_mid_slope turns downward on current candle

**For non-1m timeframes:**
- BUY: previous candle body mostly below bb_mid (>50% of body below), current candle close crosses above bb_mid with >50% of body above, bb_mid_slope is upward OR flat
- SELL: previous candle body mostly above bb_mid (>50% of body above), current candle close crosses below bb_mid with >50% of body below, bb_mid_slope is downward OR flat
- Skip doji-like candles where `abs(close - open) < atr * 0.1` (body too small for majority test)

**Candle body calculations:**
- Body top: `max(open, close)`
- Body bottom: `min(open, close)`
- Body above bb_mid: `max(0, body_top - bb_mid) / (body_top - body_bottom)`
- Body below bb_mid: `max(0, bb_mid - body_bottom) / (body_top - body_bottom)`

**Cold start / positioning entry (when no position is open and no recent cross):**

This handles the case where the strategy starts and price is already on one side of BB mid with no crossover event. Only fires when:
- No position is currently open
- No crossover has occurred in the last 10 candles
- Not choppy (chop filter still applies)
- Price is clearly on one side of BB mid — close is more than 0.3× bandwidth away from bb_mid
- BB mid slope confirms the direction (not flat)
- BUY if close > bb_mid and slope is up
- SELL if close < bb_mid and slope is down
- This entry gets **lower confidence** (0.3-0.5 range) than a crossover entry (0.6-0.9 range) since there's no cross event confirming momentum shift
- Once positioned, normal crossover rules handle exits/reversals

**Confidence scoring:**
- Base confidence from how far past the midline the close is (relative to bandwidth)
- Boost if bb_mid_slope strongly confirms direction
- Reduce if bb_mid is flat (weaker conviction)

**Cooldown:**
- For 1m: `cooldown_bars = 1`
- For non-1m: `cooldown_bars = 0` (unless the framework default is different)
- Cooldown blocks new entries only — does not prevent exits/reversals (this should already be handled by the runner, not the signal)

## Step 3: New Signal — ChopFilter

File: `signals/chop_filter.py`

This is a reusable filter, not tied to bb_mid_cross. Other strategies can use it too.

```python
@register
class ChopFilter(Signal):
    name = "chop_filter"
    display_name = "Chop Filter"
    description = "Detects choppy price action around BB midline. Returns BUY when choppy (use with Not() to block entries)."
    category = "filter"
    required_indicators = [BollingerBands]
    default_params = {"lookback": 8, "flip_threshold": 3, "narrow_bw_pct": 0.02}
```

### Evaluation logic

- Look back over last `lookback` candles (default 8, range 6-10 is fine)
- Count BB midline side flips: how many times `close` switches from above to below bb_mid or vice versa
- Market is choppy (return BUY/signal active) when:
  - Flips >= `flip_threshold` AND bb_mid is mostly flat over the lookback period
  - OR bb_bandwidth is below `narrow_bw_pct` AND bandwidth is NOT expanding
- Otherwise return NEUTRAL

**Important:** This signal returns BUY when chop is detected. Strategies use `Not(ChopFilter())` to block entries during chop. This way the filter is composable.

## Step 4: New Strategy — BBMidCrossStrategy

File: `strategies/bb_mid_cross.py`

```python
class BBMidCrossStrategy(Strategy):
    name = "bb_mid_cross"
    display_name = "Bollinger Midline Cross"
    description = "Enters on BB midline cross with slope confirmation, filtered by anti-chop detection."

    rule = And([
        BBMidCross(),
        Not(ChopFilter()),
    ])
```

## Step 5: Position behavior

This should be handled by the runner, NOT inside the signal/strategy. Verify the existing runner supports:
- Close + reverse into opposite direction on same candle when opposite signal fires
- No new position on final candle of session/day
- Session-close and end-of-day close behavior matching other strategies

If the runner doesn't support close+reverse on same candle, add it to the runner — not to the strategy.

## Step 6: Backtest and record results

- Run backtest on all 4 symbols (AAPL, TSLA, MU, INTC) for June 2026
- Append results to `results/backtest_summary.json` in the same format as other strategies
- Update `results/backtest_summary.txt` comparison table

## Step 7: Dashboard integration

- The strategy should appear in the dashboard via `/api/strategies` automatically (through the registry)
- Chart should show buy/sell pairs highlighted like other strategies
- No special dashboard code needed — if the existing dashboard reads from the strategy registry and results format, it should just work
- Verify it loads correctly

## Step 8: Add strategy_notes to ALL strategies and signals

Add a `strategy_notes` field to every strategy and signal (not just bb_mid_cross — all existing ones too). This should be bullet-point plain English covering:
- **Entry rules**: exact conditions that trigger a BUY or SELL
- **Exit rules**: what closes the position
- **Filters**: what blocks an entry (chop filter, cooldown, etc.)
- **Best conditions**: what market environment this works best in (trending, ranging, volatile, etc.)
- **Weaknesses**: when this strategy tends to fail

Display these notes in the dashboard under each strategy name. Format as a bullet list, not a paragraph.

## Checklist

- [ ] BB indicator extended with slope/bandwidth columns
- [ ] `signals/bb_mid_cross.py` with @register decorator and full metadata
- [ ] `signals/chop_filter.py` with @register decorator — reusable by other strategies
- [ ] `strategies/bb_mid_cross.py` using And/Not combinators
- [ ] Position close+reverse logic lives in runner, not strategy
- [ ] Cooldown uses existing `cooldown_bars` mechanism
- [ ] Backtest results recorded
- [ ] Dashboard loads the new strategy
- [ ] `strategy_notes` added to ALL existing strategies and signals (not just new ones)
- [ ] Notes displayed in dashboard as bullet list
- [ ] Syntax-check Python and dashboard JS
