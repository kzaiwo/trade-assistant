import numpy as np
import pandas as pd

from indicators.base import Indicator


class RSI(Indicator):
    name = "rsi"
    display_name = "Relative Strength Index"
    description = "Momentum oscillator measuring average gains versus losses."
    category = "momentum"
    default_params = {"periods": [14]}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        for period in self.params["periods"]:
            avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df[f"rsi_{period}"] = (100 - (100 / (1 + rs))).fillna(50)
        return df
