import pandas as pd

from indicators.bollinger_bands import BollingerBands
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


@register
class BBSqueeze(Signal):
    name = "bb_squeeze"
    display_name = "Bollinger Band Squeeze"
    description = "Price touches an outer band while Bollinger bandwidth is narrowing."
    category = "mean_reversion"
    required_indicators = [BollingerBands]
    default_params = {"band_tolerance": 0.0015, "narrow_lookback": 5, "cooldown_bars": 5}

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        bandwidth = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        prior_width = bandwidth.shift(self.params["narrow_lookback"])
        narrowing = bandwidth < prior_width
        tol = self.params["band_tolerance"]
        results: list[SignalResult] = []
        for i, row in df.iterrows():
            if pd.isna(row["bb_lower"]) or pd.isna(row["bb_upper"]) or pd.isna(bandwidth.loc[i]):
                results.append(self.neutral("Bollinger Bands unavailable"))
            elif row["close"] <= row["bb_lower"] * (1 + tol) and narrowing.loc[i]:
                conf = min(1.0, 0.55 + abs((row["close"] - row["bb_lower"]) / row["close"]) * 50)
                results.append(SignalResult(SignalDirection.BUY, conf, self.weight, "Price touched lower band while bandwidth narrowed", self.name, self.timeframe))
            elif row["close"] >= row["bb_upper"] * (1 - tol) and narrowing.loc[i]:
                conf = min(1.0, 0.55 + abs((row["close"] - row["bb_upper"]) / row["close"]) * 50)
                results.append(SignalResult(SignalDirection.SELL, conf, self.weight, "Price touched upper band while bandwidth narrowed", self.name, self.timeframe))
            else:
                results.append(self.neutral("No band touch with narrowing bandwidth"))
        return pd.Series(self._apply_cooldown(results), index=df.index)
