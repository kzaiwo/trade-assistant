# Codex: Implement Rally Watch

Read ARCHITECTURE.md for the project design. Follow existing patterns. Consult Claude for architectural questions.

## Overview

A lightweight scoring system that estimates when a rally up/down is forming on 1m/3m timeframes. This is an **alert/probability system, not a trade strategy** — it does not produce BUY/SELL signals. It scores up and down directions separately.

## Step 1: Rally Watch Module

File: `signals/rally_watch.py`

This is a scoring signal, not a trade signal. It returns a score object, not SignalDirection.

```python
@dataclass
class RallyScore:
    direction: str                # "up" or "down"
    score: int                    # 0-100
    state: str                    # "no_setup" / "building" / "likely" / "strong"
    reasons: list[str]            # ["BB mid rising faster", "MACD hist expanding"]
    warnings: list[str]           # ["Overextended", "Low volume", "Against VWAP"]

@dataclass
class RallyWatchResult:
    up: RallyScore
    down: RallyScore
    timestamp: str
    timeframe: str
```

### Scoring weights (configurable dict, not hardcoded)

```python
RALLY_WEIGHTS = {
    "bb_mid_slope_steepening": 15,
    "close_vs_bb_mid": 10,
    "close_vs_vwap": 10,
    "macd_hist_expanding": 15,
    "stoch_rsi_turn": 10,
    "bb_bandwidth_expanding": 15,
    "volume_above_avg": 10,
    "candle_body_strength": 10,
    "higher_tf_agreement": 15,
}
# Total possible: 110, normalize to 0-100
```

### Scoring logic per factor

**bb_mid_slope_steepening (15 pts):**
- BB mid slope is in the rally direction AND getting steeper (current slope > previous slope)
- Partial credit if slope is in direction but not steepening

**close_vs_bb_mid (10 pts):**
- For rally up: close is above bb_mid
- For rally down: close is below bb_mid
- More points the further away from bb_mid (relative to bandwidth)

**close_vs_vwap (10 pts):**
- For rally up: close is above VWAP
- For rally down: close is below VWAP
- More points the further away

**macd_hist_expanding (15 pts):**
- MACD histogram is in the rally direction AND expanding (getting larger in absolute terms)
- Partial credit if histogram is in direction but not expanding

**stoch_rsi_turn (10 pts):**
- For rally up: StochRSI K is turning up from below 30 (not already exhausted above 80)
- For rally down: StochRSI K is turning down from above 70 (not already exhausted below 20)
- 0 points if already in exhausted zone

**bb_bandwidth_expanding (15 pts):**
- Bandwidth is expanding after a compression period
- More points if expansion is accelerating
- 0 if bandwidth is contracting

**volume_above_avg (10 pts):**
- Current volume is above the 20-candle average
- Scale: 1.0x avg = partial, 1.5x+ = full points

**candle_body_strength (10 pts):**
- Consecutive strong closes in rally direction (body > 60% of candle range)
- Small wicks against the rally direction
- Look at last 3 candles

**higher_tf_agreement (15 pts):**
- If evaluating 1m, check if 3m or 5m shows the same direction
- BB mid slope, MACD, and price position on higher timeframe agree
- Full points if higher TF agrees, 0 if it disagrees, partial if neutral

### State thresholds

```python
def get_state(score: int) -> str:
    if score >= 75:
        return "strong"
    elif score >= 60:
        return "likely"
    elif score >= 40:
        return "building"
    else:
        return "no_setup"
```

### Warning flags

Add warnings when the score might be misleading:
- **"Overextended"**: StochRSI already above 80 (for up) or below 20 (for down)
- **"Low volume"**: volume below 0.7x average despite other factors scoring high
- **"Against VWAP"**: rally direction is opposite to VWAP position
- **"Flat BB mid"**: BB mid slope is flat despite other factors
- **"Narrow bandwidth"**: BB bandwidth is very narrow and not expanding (could go either way)

### Reasons

For each factor that scores > 0, add a short human-readable reason string:
- "BB mid rising faster"
- "Price above VWAP (0.3% above)"
- "MACD hist expanding (+0.05)"
- "StochRSI turning up from 22"
- "Bandwidth expanding after squeeze"
- "Volume 1.4x average"
- "3 strong bullish candles"
- "3m timeframe agrees"

## Step 2: Dashboard integration

### API

Add to dashboard_server.py a route or include in existing response:
```python
rally_watch: {
    "up": {"score": 72, "state": "likely", "reasons": [...], "warnings": [...]},
    "down": {"score": 15, "state": "no_setup", "reasons": [], "warnings": []}
}
```

### UI — Rally Watch card

- Place near the Strategy Signal area in the dashboard
- Show both up and down scores
- Color: subtle green for up building/likely/strong, subtle red for down, gray for no setup
- Display state label prominently: "Building Up", "Rally Up Likely", "Rally Up Strong", "No Setup"
- Show reasons as compact bullet list
- Show warnings with a caution icon
- Keep it visually distinct from BUY/SELL signals — this is informational, not an action

### Chart overlay

- Add a small marker or subtle shaded background when state is "likely" or "strong"
- Green shade for rally up, red shade for rally down
- Keep it clearly separate from BUY/SELL markers so it doesn't look like an entry signal
- Make it toggleable in the chart controls

## Step 3: Future strategy integration (do NOT implement now, just ensure the interface supports it)

The module should be importable so a strategy could eventually use it:
```python
rule = And([
    BBMidCross(),
    Not(ChopFilter()),
    RallyWatch(min_score=60),  # only enter when rally is building
])
```

This means the `evaluate()` method should also be callable as a filter that returns NEUTRAL (score below threshold) or BUY/SELL (score above threshold). Add this as a secondary interface but don't wire it into any strategy yet.

## Checklist

- [ ] `signals/rally_watch.py` with RallyScore, RallyWatchResult dataclasses
- [ ] Configurable RALLY_WEIGHTS dict
- [ ] All 9 scoring factors implemented
- [ ] Warning flags implemented
- [ ] Human-readable reason strings for each factor
- [ ] State thresholds: no_setup / building / likely / strong
- [ ] API returns rally_watch data
- [ ] Dashboard Rally Watch card with color coding
- [ ] Chart overlay for likely/strong states (toggleable)
- [ ] Visually distinct from BUY/SELL signals
- [ ] Interface supports future strategy integration
- [ ] Syntax-check Python and dashboard JS
