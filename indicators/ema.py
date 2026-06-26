import pandas as pd

from indicators.base import Indicator


class EMA(Indicator):
    name = "ema"
    display_name = "Exponential Moving Average"
    description = "Exponentially weighted moving averages."
    category = "trend"
    default_params = {"periods": [8, 13, 21, 50]}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for period in self.params["periods"]:
            df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
        return df
