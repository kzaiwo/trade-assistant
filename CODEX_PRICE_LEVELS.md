# Codex: Implement Price Levels & Targets

Read ARCHITECTURE.md for the project design. Follow existing patterns. Consult Claude for architectural questions.

## Overview

A support/resistance and price target system that identifies key price levels from multiple sources, detects confluence zones, and provides targets for rally watch. This is an **informational layer, not a trade signal**.

## Step 1: Price Levels Module

File: `signals/price_levels.py`

```python
@dataclass
class PriceLevel:
    price: float
    level_type: str             # "support" or "resistance"
    source: str                 # "swing_high", "swing_low", "vwap", "bb_upper", "bb_mid", "bb_lower", "session_high", "session_low", "prev_day_high", "prev_day_low"
    strength: float             # 0.0-1.0, based on recency + confluence
    recency: int                # how many candles ago this level was established
    label: str                  # "S1", "S2", "R1", "R2", etc.
    confluent_with: list[str]   # other sources at same zone, e.g. ["vwap", "swing_high"]

@dataclass
class PriceTarget:
    price: float
    direction: str              # "up" or "down"
    label: str                  # "T1", "T2", "T3"
    source: str                 # which level this target comes from
    distance_dollars: float
    distance_pct: float

@dataclass
class InvalidationLevel:
    price: float
    direction: str              # which rally direction this invalidates
    reason: str                 # "Loses BB mid", "Loses VWAP"

@dataclass
class PriceLevelsResult:
    supports: list[PriceLevel]
    resistances: list[PriceLevel]
    targets_up: list[PriceTarget]
    targets_down: list[PriceTarget]
    invalidations: list[InvalidationLevel]
    current_price: float
    timestamp: str
    timeframe: str
```

### Level sources to compute

**Swing highs/lows:**
- Detect local swing highs and lows over a configurable lookback (default 50 candles)
- A swing high: candle high is higher than N candles before and after (default N=3)
- A swing low: candle low is lower than N candles before and after (default N=3)
- Keep the most recent 3-5 swing highs and 3-5 swing lows

**Session levels:**
- Session high: highest high of the current trading session
- Session low: lowest low of the current trading session

**Previous day levels (if data available):**
- Previous day high
- Previous day low

**Indicator levels (read from already-computed indicator columns):**
- VWAP
- BB upper, BB mid, BB lower

### Recency weighting

Older swing levels are weaker. Apply a decay:

```python
recency_weight = max(0.3, 1.0 - (candles_ago / max_lookback))
```

- A swing high from 5 candles ago: strength ~0.9
- A swing high from 40 candles ago: strength ~0.4
- Indicator levels (VWAP, BB) are always current, so strength = 1.0
- Session high/low: strength = 1.0

### Confluence detection

When multiple levels cluster within 0.1% of each other, merge them into a confluence zone:

```python
def detect_confluence(levels: list[PriceLevel], threshold_pct: float = 0.001) -> list[PriceLevel]:
    """
    Group levels within threshold_pct of each other.
    The resulting level gets:
    - price: average of the cluster
    - strength: boosted (min 1.0, sum of individual strengths capped at 1.0)
    - confluent_with: list of all sources in the cluster
    - source: primary source (highest individual strength)
    """
```

A confluent level (e.g. VWAP + swing high at the same price) is significantly stronger than either alone. Boost strength by 0.2 per additional confluent source, capped at 1.0.

### Labeling

After computing and sorting:
- Supports below current price, sorted closest first: S1, S2, S3
- Resistances above current price, sorted closest first: R1, R2, R3
- Max 4 support and 4 resistance levels displayed

### Price targets

Targets are derived FROM the levels — do not compute them separately.

**For rally up targets:**
- T1: nearest resistance above current price (R1)
- T2: next resistance (R2)
- T3: furthest resistance shown (R3)

**For rally down targets:**
- T1: nearest support below current price (S1)
- T2: next support (S2)
- T3: furthest support shown (S3)

Include `distance_dollars` and `distance_pct` from current price for each target.

### Invalidation levels

Simple rules:
- Upside rally invalidation: current price loses BB mid or VWAP (whichever is closer below)
- Downside rally invalidation: current price reclaims BB mid or VWAP (whichever is closer above)
- Include the price and distance

## Step 2: Connect to Rally Watch

File: `signals/rally_watch.py`

Rally watch targets should pull from price_levels, not compute their own:

```python
# In rally watch result, reference PriceLevelsResult
@dataclass
class RallyWatchResult:
    up: RallyScore
    down: RallyScore
    targets: PriceLevelsResult    # from price_levels module
    timestamp: str
    timeframe: str
```

The rally watch card in the dashboard should show targets inline:
- "Rally Up Likely — Targets: R1 1158.60 (+0.7%), R2 1166.20 (+1.4%)"
- "Invalidated if price loses 1152.30 (BB mid)"

## Step 3: Dashboard integration

### API

Add to the response (or extend existing endpoint):
```python
price_levels: {
    "supports": [
        {"price": 1145.20, "label": "S1", "source": "swing_low", "strength": 0.85, "confluent_with": []},
        {"price": 1138.80, "label": "S2", "source": "session_low", "strength": 1.0, "confluent_with": ["prev_day_low"]}
    ],
    "resistances": [...],
    "targets_up": [
        {"price": 1158.60, "label": "T1", "source": "vwap", "distance_dollars": 5.40, "distance_pct": 0.47}
    ],
    "targets_down": [...],
    "invalidations": [
        {"price": 1152.30, "direction": "up", "reason": "Loses BB mid"}
    ]
}
```

### UI — Price Levels card

- Place near the Rally Watch card
- Show support and resistance tables as Codex suggested:
```
Support                         Resistance
S1  1145.20  swing low          R1  1158.60  VWAP + swing high ★
S2  1138.80  session low        R2  1166.20  BB mid
S3  1132.40  BB lower           R3  1174.90  session high
```
- Mark confluent levels with a star or highlight
- Stronger levels (higher strength) get bolder text or a thicker indicator
- Show targets with distance in dollars and percent
- Show invalidation levels with caution styling

### Chart lines

- Draw horizontal lines at each support/resistance level
- Color: green for support, red for resistance
- Line thickness proportional to strength (confluent levels are thicker)
- **Toggleable per level type** — user can show/hide:
  - Swing levels
  - BB levels (upper/mid/lower)
  - VWAP
  - Session levels
  - Previous day levels
- Labels on the right edge of chart: "S1 1145.20", "R1 1158.60"
- Default: show swing levels + VWAP + session levels. BB levels off by default (they're already on the chart as bands).

## Step 4: Skip for now

- **Measured move targets** (range breakout projections) — complex to get right, add later
- **Fibonacci levels** — can add as another source later, same interface
- **Volume profile levels** (high volume nodes) — future enhancement

## Checklist

- [ ] `signals/price_levels.py` with all dataclasses
- [ ] Swing high/low detection with configurable lookback
- [ ] Session high/low, previous day high/low
- [ ] Indicator levels from existing columns (VWAP, BB)
- [ ] Recency weighting for swing levels
- [ ] Confluence detection at 0.1% threshold
- [ ] Strength boosting for confluent levels
- [ ] S1/S2/S3/R1/R2/R3 labeling sorted by distance
- [ ] Price targets derived from levels (not computed separately)
- [ ] Invalidation levels computed
- [ ] Rally watch updated to reference price_levels targets
- [ ] API returns price_levels data
- [ ] Dashboard Price Levels card
- [ ] Chart horizontal lines, toggleable per level type
- [ ] Confluent levels visually distinguished (star, thicker line)
- [ ] Syntax-check Python and dashboard JS
