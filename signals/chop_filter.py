import pandas as pd

from indicators.bollinger_bands import BollingerBands
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


@register
class ChopFilter(Signal):
    name = "chop_filter"
    display_name = "Chop Filter"
    description = "Detects choppy price action around the Bollinger middle line."
    category = "filter"
    required_indicators = [BollingerBands]
    default_params = {"lookback": 8, "flip_threshold": 3, "narrow_bw_pct": 0.02}
    strategy_notes = [
        "Entry rules: returns an active BUY filter signal when the market is choppy around BB mid.",
        "Exit rules: this is a filter only and does not close positions by itself.",
        "Filters: detects repeated BB midline flips with mostly flat BB mid, or tight bandwidth that is not expanding.",
        "Best conditions: useful as an entry blocker for strategies that otherwise overtrade sideways candles.",
        "Weaknesses: can block early entries before a real breakout if bandwidth has not started expanding yet.",
    ]

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        lookback = int(self.params["lookback"])
        flip_threshold = int(self.params["flip_threshold"])
        narrow_bw_pct = float(self.params["narrow_bw_pct"])
        side = (df["close"] > df["bb_mid"]).astype(int)
        flips = side.ne(side.shift(1)).rolling(lookback).sum().fillna(0)
        flat_ratio = df["bb_mid_flat"].fillna(False).rolling(lookback).mean().fillna(0)
        bandwidth = df.get("bb_bandwidth", (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"])
        expanding = df.get("bb_bandwidth_expanding", bandwidth > bandwidth.shift(1)).fillna(False)
        results: list[SignalResult] = []
        for pos, idx in enumerate(df.index):
            if pd.isna(df["bb_mid"].iat[pos]) or pd.isna(bandwidth.iat[pos]):
                results.append(self.neutral("Bollinger midline unavailable"))
            elif flips.iat[pos] >= flip_threshold and flat_ratio.iat[pos] >= 0.5:
                results.append(SignalResult(SignalDirection.BUY, 0.9, self.weight, "Chop detected: repeated BB midline flips with flat midline", self.name, self.timeframe))
            elif bandwidth.iat[pos] < narrow_bw_pct and not bool(expanding.iat[pos]):
                results.append(SignalResult(SignalDirection.BUY, 0.8, self.weight, "Chop detected: narrow Bollinger bandwidth without expansion", self.name, self.timeframe))
            else:
                results.append(self.neutral("No chop filter block"))
        return pd.Series(results, index=df.index)
