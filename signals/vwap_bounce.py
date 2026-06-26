import pandas as pd

from indicators.vwap import VWAP
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


@register
class VWAPBounce(Signal):
    name = "vwap_bounce"
    display_name = "VWAP Bounce"
    description = "Price reclaims or rejects VWAP with confirming volume."
    category = "trend"
    required_indicators = [VWAP]
    default_params = {"volume_window": 20, "volume_multiplier": 1.05, "cooldown_bars": 5}

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        avg_volume = df["volume"].rolling(self.params["volume_window"]).mean()
        volume_ok = df["volume"] >= avg_volume * self.params["volume_multiplier"]
        results: list[SignalResult] = []
        prev_close = df["close"].shift(1)
        prev_vwap = df["vwap"].shift(1)
        for i, row in df.iterrows():
            if pd.isna(row["vwap"]) or pd.isna(avg_volume.loc[i]):
                results.append(self.neutral("VWAP or average volume unavailable"))
            elif prev_close.loc[i] <= prev_vwap.loc[i] and row["close"] > row["vwap"] and volume_ok.loc[i]:
                conf = min(1.0, 0.6 + (row["volume"] / avg_volume.loc[i] - 1) * 0.2)
                results.append(SignalResult(SignalDirection.BUY, conf, self.weight, "Price reclaimed VWAP on confirming volume", self.name, self.timeframe))
            elif prev_close.loc[i] >= prev_vwap.loc[i] and row["close"] < row["vwap"] and volume_ok.loc[i]:
                conf = min(1.0, 0.6 + (row["volume"] / avg_volume.loc[i] - 1) * 0.2)
                results.append(SignalResult(SignalDirection.SELL, conf, self.weight, "Price rejected VWAP on confirming volume", self.name, self.timeframe))
            else:
                results.append(self.neutral("No VWAP reclaim/rejection with volume"))
        return pd.Series(self._apply_cooldown(results), index=df.index)
