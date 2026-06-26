import pandas as pd

from indicators.stoch_rsi import StochRSI
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


@register
class StochCross(Signal):
    name = "stoch_cross"
    display_name = "Stochastic RSI Crossover"
    description = "K crosses D in oversold or overbought zones."
    category = "momentum"
    required_indicators = [StochRSI]
    default_params = {"threshold": 20, "cooldown_bars": 5}
    strategy_notes = [
        "Entry rules: buy when StochRSI K crosses above D in the oversold zone; sell when K crosses below D in the overbought zone.",
        "Exit rules: close or reverse when an opposite StochRSI zone crossover appears after cooldown.",
        "Filters: ignores crosses outside the oversold or overbought zones and uses cooldown.",
        "Best conditions: works best after sharp intraday extensions that start to mean revert.",
        "Weaknesses: can fire early in strong trends where overbought or oversold stays pinned.",
    ]

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        k = df["stoch_rsi_k"]
        d = df["stoch_rsi_d"]
        threshold = self.params["threshold"]
        results: list[SignalResult] = []
        for i in df.index:
            if i == df.index[0] or pd.isna(k.loc[i]) or pd.isna(d.loc[i]):
                results.append(self.neutral("Stoch RSI unavailable"))
                continue
            prev_pos = k.shift(1).loc[i] - d.shift(1).loc[i]
            curr_pos = k.loc[i] - d.loc[i]
            if prev_pos <= 0 < curr_pos and k.loc[i] <= threshold:
                conf = min(1.0, 0.55 + (threshold - min(k.loc[i], threshold)) / max(threshold, 1) * 0.35 + abs(curr_pos) / 100)
                results.append(SignalResult(SignalDirection.BUY, conf, self.weight, f"K crossed above D at {k.loc[i]:.2f}", self.name, self.timeframe))
            elif prev_pos >= 0 > curr_pos and k.loc[i] >= 100 - threshold:
                conf = min(1.0, 0.55 + (max(k.loc[i], 100 - threshold) - (100 - threshold)) / max(threshold, 1) * 0.35 + abs(curr_pos) / 100)
                results.append(SignalResult(SignalDirection.SELL, conf, self.weight, f"K crossed below D at {k.loc[i]:.2f}", self.name, self.timeframe))
            else:
                results.append(self.neutral("No Stoch RSI zone crossover"))
        return pd.Series(self._apply_cooldown(results), index=df.index)
