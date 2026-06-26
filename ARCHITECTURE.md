# Trade Assistant — Architecture

## Overview

A modular trading signal engine that evaluates technical indicators, generates buy/sell signals with confidence scores, and supports composable strategies. Designed for backtesting now, live trading later.

Data source: `../_ trade_data/{SYMBOL}/{SYMBOL}_*.json` — 1-minute OHLCV bars with pre-computed indicator fields.

## Directory Structure

```
trade-assistant/
├── main.py                      # Entry point — load data, run strategy, output results
├── config.py                    # Symbols, data paths, default params, presets
│
├── data/
│   ├── base.py                  # DataSource ABC
│   └── file_loader.py           # Load JSON bars from _trade_data, resample timeframes
│
├── indicators/
│   ├── base.py                  # Indicator ABC with metadata
│   ├── bollinger_bands.py
│   ├── stoch_rsi.py
│   ├── vwap.py
│   ├── macd.py
│   ├── ema.py
│   └── rsi.py
│
├── signals/
│   ├── base.py                  # Signal ABC + SignalResult dataclass
│   ├── registry.py              # Auto-discover and register all signals
│   ├── bb_squeeze.py
│   ├── stoch_cross.py
│   └── vwap_bounce.py
│
├── strategies/
│   ├── base.py                  # Strategy ABC + And/Or/Not combinators + StrategyResult
│   └── mean_reversion.py        # Example strategy
│
├── runner/
│   ├── base.py                  # Runner ABC
│   └── backtest.py              # Run strategy against historical data
│
├── models/
│   └── types.py                 # All shared types and dataclasses
│
├── journal/
│   └── logger.py                # Log every signal and trade to JSON/SQLite
│
├── notifiers/
│   ├── base.py                  # Notifier ABC
│   └── console.py               # Print signals to terminal
│
└── api/
    └── serializers.py           # to_dict/to_json helpers for dashboard
```

## Tech Stack

- Python 3.12+
- pandas for DataFrames
- No heavy frameworks — keep it simple

---

## Layer 1: Data

### DataSource ABC (`data/base.py`)

```python
from abc import ABC, abstractmethod
import pandas as pd

class DataSource(ABC):
    @abstractmethod
    def get_bars(self, symbol: str) -> dict[str, pd.DataFrame]:
        """Return bars keyed by timeframe: {"1m": df, "5m": df, "15m": df}"""
```

### FileLoader (`data/file_loader.py`)

- Reads `../_trade_data/{SYMBOL}/{SYMBOL}_*.json`
- Parses the `bars` array into a DataFrame
- Resamples 1m bars into 5m, 15m, 1h automatically
- Normalizes column names to: `time_key, date, open, high, low, close, volume`

```python
class FileLoader(DataSource):
    def __init__(self, base_path: str = "../_trade_data"):
        self.base_path = base_path

    def get_bars(self, symbol: str) -> dict[str, pd.DataFrame]:
        df_1m = self._load_raw(symbol)
        return {
            "1m": df_1m,
            "5m": self._resample(df_1m, "5min"),
            "15m": self._resample(df_1m, "15min"),
            "1h": self._resample(df_1m, "1h"),
        }

    def _resample(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """Standard OHLCV resampling."""
```

### Future: MoomooLoader (`data/moomoo_loader.py`)

Same interface, fetches from Moomoo OpenD API. Also provides a `stream()` method for live bars.

---

## Layer 2: Indicators

### Indicator ABC (`indicators/base.py`)

Each indicator is one file, self-contained with metadata and default params.

```python
from abc import ABC, abstractmethod
import pandas as pd

class Indicator(ABC):
    name: str                    # "bollinger_bands"
    display_name: str            # "Bollinger Bands"
    description: str             # "Measures volatility using..."
    default_params: dict         # {"period": 20, "std_dev": 2}

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to df and return it. Pure computation."""
```

### Example: BollingerBands (`indicators/bollinger_bands.py`)

```python
class BollingerBands(Indicator):
    name = "bollinger_bands"
    display_name = "Bollinger Bands"
    description = "Volatility bands around a moving average. Squeeze indicates low volatility, expansion indicates breakout potential."
    default_params = {"period": 20, "std_dev": 2}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params["period"]
        s = self.params["std_dev"]
        df["bb_mid"] = df["close"].rolling(p).mean()
        df["bb_upper"] = df["bb_mid"] + s * df["close"].rolling(p).std()
        df["bb_lower"] = df["bb_mid"] - s * df["close"].rolling(p).std()
        return df
```

### Indicators to implement

| File | Columns produced |
|------|-----------------|
| `bollinger_bands.py` | `bb_mid`, `bb_upper`, `bb_lower` |
| `stoch_rsi.py` | `stoch_rsi_k`, `stoch_rsi_d` |
| `vwap.py` | `vwap` |
| `macd.py` | `macd_dif`, `macd_dea`, `macd_hist` |
| `ema.py` | `ema_{period}` for each period (8, 13, 21, 50) |
| `rsi.py` | `rsi_{period}` |

Note: the raw data already has some indicator fields pre-computed. The indicator layer should recompute them from OHLCV to ensure consistency and allow parameter tuning.

---

## Layer 3: Signals

### Signal ABC (`signals/base.py`)

Signals interpret indicator values and produce a result with direction + confidence.

```python
from abc import ABC, abstractmethod
import pandas as pd
from models.types import SignalResult

class Signal(ABC):
    name: str                              # "stoch_cross"
    display_name: str                      # "Stochastic RSI Crossover"
    description: str                       # "Triggers when K crosses D..."
    category: str                          # "momentum", "mean_reversion", "trend"
    required_indicators: list[type]        # [StochRSI] — auto-resolved by strategy
    timeframe: str = "1m"                  # which timeframe this evaluates on
    weight: float = 1.0                    # importance weight for confidence aggregation

    def __init__(self, timeframe: str = None, weight: float = None, **params):
        if timeframe:
            self.timeframe = timeframe
        if weight:
            self.weight = weight
        self.params = {**self.default_params, **params}

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Return a Series of SignalResult per row."""

    def to_dict(self) -> dict:
        """Serialize metadata for dashboard/API."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "timeframe": self.timeframe,
            "weight": self.weight,
        }
```

### SignalResult (`models/types.py`)

```python
from dataclasses import dataclass
from enum import Enum

class SignalDirection(Enum):
    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"

@dataclass
class SignalResult:
    direction: SignalDirection
    confidence: float              # 0.0 - 1.0
    weight: float                  # from the signal's weight field
    reason: str                    # "K crossed above D at 15 (below 20 threshold)"
    signal_name: str               # "stoch_cross"
    timeframe: str                 # "1m"
```

### Signal Registry (`signals/registry.py`)

Auto-discovers all Signal subclasses so the dashboard can list them without hardcoding:

```python
SIGNAL_REGISTRY: dict[str, type[Signal]] = {}

def register(cls):
    SIGNAL_REGISTRY[cls.name] = cls
    return cls
```

Usage: decorate each signal class with `@register`.

### Signals to implement

| File | Logic | Category |
|------|-------|----------|
| `bb_squeeze.py` | Price touches lower band + bandwidth narrowing | mean_reversion |
| `stoch_cross.py` | K crosses above/below D in oversold/overbought zones | momentum |
| `vwap_bounce.py` | Price reclaims VWAP with confirming volume | trend |
| `macd_cross.py` | MACD line crosses signal line, histogram confirms direction | momentum |

### Cooldown

Each signal has an optional `cooldown_bars: int` parameter. After firing, it returns NEUTRAL for the next N bars. Prevents duplicate entries and dashboard noise.

---

## Layer 4: Strategies

### Combinators (`strategies/base.py`)

Compose signals using boolean logic:

```python
from dataclasses import dataclass

@dataclass
class And:
    conditions: list    # Signal | And | Or | Not

@dataclass
class Or:
    conditions: list

@dataclass
class Not:
    condition: object   # Signal | And | Or | Not
```

### Strategy ABC (`strategies/base.py`)

```python
class Strategy(ABC):
    name: str
    display_name: str
    description: str
    rule: And | Or | Not | Signal
    valid_contexts: list[str] = None    # e.g. ["ranging"] — skip if market doesn't match

    def run(self, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        1. Walk the rule tree, collect all required indicators
        2. Compute indicators on the appropriate timeframe DataFrames
        3. Evaluate each signal on its timeframe
        4. Combine results using the rule tree
        5. Return DataFrame with StrategyResult per row
        """
```

### Confidence aggregation with weights

- **`And`**: weighted average — `sum(confidence * weight) / sum(weight)`
- **`Or`**: take the result with highest `confidence * weight`
- **`Not`**: inverts confidence — `1 - confidence`, keeps weight

### StrategyResult (`models/types.py`)

```python
@dataclass
class StrategyResult:
    direction: SignalDirection
    confidence: float                     # aggregated from signals
    signal_results: list[SignalResult]    # drill-down for dashboard/journal
    triggered_rule: str                   # "bb_squeeze AND (stoch_cross OR vwap_bounce)"
    market_context: MarketContext | None
    cooldown_active: bool                 # True = signal valid but suppressed

@dataclass
class MarketContext:
    trend: str          # "trending" / "ranging" / "choppy"
    volatility: str     # "low" / "normal" / "high"
```

### Example strategy (`strategies/mean_reversion.py`)

```python
class MeanReversion(Strategy):
    name = "mean_reversion"
    display_name = "Mean Reversion"
    description = "Looks for oversold bounces confirmed by momentum shift."
    valid_contexts = ["ranging"]

    rule = And([
        BBSqueeze(weight=2.0),
        Or([
            StochCross(timeframe="1m", weight=1.0, threshold=20),
            VWAPBounce(timeframe="1m", weight=1.5),
        ])
    ])
```

### Multi-timeframe example

```python
class ConfirmedEntry(Strategy):
    name = "confirmed_entry"
    display_name = "1m Entry + 5m Confirmation"
    description = "Enter on 1m signal, confirm trend on 5m."

    rule = And([
        Or([
            BBSqueeze(timeframe="1m"),
            StochCross(timeframe="1m"),
        ]),
        TrendFilter(timeframe="5m", direction="up"),
    ])
```

### Parameter presets (`config.py`)

```python
PRESETS = {
    "conservative": {"bb_period": 20, "stoch_threshold": 15, "cooldown_bars": 10},
    "aggressive":   {"bb_period": 14, "stoch_threshold": 25, "cooldown_bars": 5},
}
```

---

## Layer 5: Runner

### Runner ABC (`runner/base.py`)

```python
class Runner(ABC):
    def __init__(self, data_source: DataSource, strategy: Strategy,
                 notifiers: list[Notifier] = None, journal: TradeJournal = None):
        self.data_source = data_source
        self.strategy = strategy
        self.notifiers = notifiers or []
        self.journal = journal

    @abstractmethod
    def run(self, symbols: list[str]): ...
```

### BacktestRunner (`runner/backtest.py`)

```python
class BacktestRunner(Runner):
    def run(self, symbols: list[str]):
        for symbol in symbols:
            bars = self.data_source.get_bars(symbol)
            results = self.strategy.run(bars)

            for row in results.itertuples():
                if row.direction != SignalDirection.NEUTRAL:
                    self.journal.log_signal(symbol, row)
                    for notifier in self.notifiers:
                        notifier.send(row, symbol)
```

### Future: LiveRunner (`runner/live.py`)

```python
class LiveRunner(Runner):
    def run(self, symbols: list[str]):
        for bar in self.data_source.stream(symbol):
            bars = self._update_dataframes(bar)
            result = self.strategy.run(bars)

            if result.direction != SignalDirection.NEUTRAL:
                if not self.position_tracker.has_position(symbol, result.direction):
                    self.journal.log_signal(symbol, result)
                    for notifier in self.notifiers:
                        notifier.send(result, symbol)
```

---

## Layer 6: Journal

### TradeJournal (`journal/logger.py`)

Logs every signal and trade for study and future LLM context.

```python
class TradeJournal:
    def __init__(self, path: str = "journal/"):
        self.path = path

    def log_signal(self, symbol: str, result: StrategyResult):
        """Log signal with timestamp, all signal details, confidence, market context."""

    def log_trade(self, trade: TradeResult):
        """Log completed trade with entry/exit, P&L, confidence at entry."""

    def get_history(self, symbol: str = None, last_n: int = None) -> list[dict]:
        """Retrieve past signals/trades for analysis."""

    def summarize(self, symbol: str = None) -> str:
        """Generate LLM-ready summary of recent activity."""
```

### What gets logged (per signal event)

```json
{
    "timestamp": "2026-06-01T09:35:00",
    "symbol": "AAPL",
    "strategy": "mean_reversion",
    "direction": "buy",
    "confidence": 0.82,
    "signal_results": [
        {"signal": "bb_squeeze", "direction": "buy", "confidence": 0.9, "weight": 2.0, "reason": "Price at lower band, bandwidth 1.2%", "timeframe": "1m"},
        {"signal": "stoch_cross", "direction": "buy", "confidence": 0.7, "weight": 1.0, "reason": "K crossed D at 18", "timeframe": "1m"}
    ],
    "market_context": {"trend": "ranging", "volatility": "low"},
    "price_at_signal": 308.15,
    "cooldown_active": false
}
```

### TradeResult (`models/types.py`)

```python
@dataclass
class TradeResult:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    direction: SignalDirection
    entry_price: float
    exit_price: float
    confidence_at_entry: float
    pnl: float
    pnl_pct: float
    strategy_name: str
    signal_results_at_entry: list[SignalResult]
```

Storage: JSON lines files per symbol per day. Simple, greppable, easy to load into pandas for analysis.

---

## Layer 7: Notifiers

### Notifier ABC (`notifiers/base.py`)

```python
class Notifier(ABC):
    @abstractmethod
    def send(self, result: StrategyResult, symbol: str): ...
```

### ConsoleNotifier (`notifiers/console.py`) — implement now

Prints signals to terminal with color coding.

### Future notifiers

- `webhook.py` — push to dashboard via HTTP
- `sms.py` — Twilio/push notification for high-confidence signals

---

## Layer 8: Position Tracking

### Position (`models/types.py`)

```python
@dataclass
class Position:
    symbol: str
    direction: SignalDirection
    entry_price: float
    entry_time: datetime
    confidence_at_entry: float
    strategy_name: str
```

### PositionTracker (`models/types.py` or `runner/position_tracker.py`)

```python
class PositionTracker:
    def has_position(self, symbol: str, direction: SignalDirection) -> bool: ...
    def open_position(self, position: Position): ...
    def close_position(self, symbol: str) -> Position: ...
    def get_open_positions(self) -> list[Position]: ...
```

The runner checks this before emitting signals — no BUY if already long.

---

## LLM Integration (future)

### StrategyResult.summarize()

Every result can produce an LLM-ready summary:

```python
@dataclass
class StrategyResult:
    ...
    def summarize(self) -> str:
        """Returns structured text for LLM consumption:
        - Current price + key levels
        - Active signals with confidence and reasons
        - Market context
        - Recent trade history summary
        """
```

Feed the engine's computed results to the LLM — **engine does the math, LLM does the reasoning**.

---

## Data Flow

```
DataSource (file / moomoo)
    │
    ▼
Runner (backtest / live)
    │
    ├── Strategy.run(bars)
    │       │
    │       ├── Walk rule tree → collect required Indicators
    │       ├── Compute Indicators on each timeframe DataFrame
    │       ├── Evaluate Signals → SignalResult (direction, confidence, reason)
    │       ├── Combine via And/Or/Not with weighted confidence
    │       └── Apply cooldown + market context filter
    │       │
    │       ▼
    │   StrategyResult
    │
    ├── PositionTracker.check()
    ├── Journal.log_signal()
    ├── Notifier.send()
    └── (future) LLM.summarize()
```

---

## What to implement now

1. `models/types.py` — all dataclasses and enums
2. `data/base.py` + `data/file_loader.py` — load JSON, resample timeframes
3. `indicators/base.py` + all 6 indicator files
4. `signals/base.py` + `signals/registry.py` + 3 signal files
5. `strategies/base.py` (including And/Or/Not) + `strategies/mean_reversion.py`
6. `runner/base.py` + `runner/backtest.py`
7. `journal/logger.py`
8. `notifiers/base.py` + `notifiers/console.py`
9. `config.py`
10. `main.py` — wire it all together

## What to defer

- `data/moomoo_loader.py` — live data
- `runner/live.py` — real-time execution
- `notifiers/webhook.py`, `notifiers/sms.py`
- `api/serializers.py` — dashboard
- LLM integration
- Position tracking (until live runner is built)
