import numpy as np
import pandas as pd

from indicators.base import Indicator
from indicators.rsi import RSI


class StochRSI(Indicator):
    name = "stoch_rsi"
    display_name = "Stochastic RSI"
    description = "Stochastic oscillator applied to RSI for momentum crosses."
    category = "momentum"
    default_params = {"rsi_period": 5, "stoch_period": 5, "k_period": 3, "d_period": 3}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = RSI(periods=[self.params["rsi_period"]]).compute(df)
        rsi_col = f"rsi_{self.params['rsi_period']}"
        low = df[rsi_col].rolling(self.params["stoch_period"]).min()
        high = df[rsi_col].rolling(self.params["stoch_period"]).max()
        raw = 100 * (df[rsi_col] - low) / (high - low).replace(0, np.nan)
        raw = pd.to_numeric(raw, errors="coerce")
        df["stoch_rsi_k"] = raw.rolling(self.params["k_period"]).mean().fillna(50)
        df["stoch_rsi_d"] = (
            df["stoch_rsi_k"].rolling(self.params["d_period"]).mean().fillna(50)
        )
        return df
