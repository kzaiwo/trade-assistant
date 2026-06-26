import pandas as pd

from indicators.macd import MACD
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


@register
class MACDCross(Signal):
    name = "macd_cross"
    display_name = "MACD Crossover"
    description = "MACD line crosses the signal line with histogram confirmation."
    category = "momentum"
    required_indicators = [MACD]
    default_params = {"cooldown_bars": 5}

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        dif = df["macd_dif"]
        dea = df["macd_dea"]
        hist = df["macd_hist"]
        results: list[SignalResult] = []
        for i in df.index:
            if i == df.index[0] or pd.isna(dif.loc[i]) or pd.isna(dea.loc[i]):
                results.append(self.neutral("MACD unavailable"))
                continue
            prev = (dif - dea).shift(1).loc[i]
            curr = (dif - dea).loc[i]
            if prev <= 0 < curr and hist.loc[i] > 0:
                conf = min(1.0, 0.58 + min(abs(hist.loc[i]) / max(abs(df["close"].loc[i]), 1) * 100, 0.35))
                results.append(SignalResult(SignalDirection.BUY, conf, self.weight, "MACD crossed above signal with positive histogram", self.name, self.timeframe))
            elif prev >= 0 > curr and hist.loc[i] < 0:
                conf = min(1.0, 0.58 + min(abs(hist.loc[i]) / max(abs(df["close"].loc[i]), 1) * 100, 0.35))
                results.append(SignalResult(SignalDirection.SELL, conf, self.weight, "MACD crossed below signal with negative histogram", self.name, self.timeframe))
            else:
                results.append(self.neutral("No MACD histogram-confirmed crossover"))
        return pd.Series(self._apply_cooldown(results), index=df.index)
