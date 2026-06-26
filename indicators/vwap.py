import pandas as pd

from indicators.base import Indicator


class VWAP(Indicator):
    name = "vwap"
    display_name = "VWAP"
    description = "Session volume-weighted average price."
    category = "trend"
    default_params = {}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        typical = (df["high"] + df["low"] + df["close"]) / 3
        pv = typical * df["volume"]
        grouped_date = pd.to_datetime(df["time_key"]).dt.date
        df["vwap"] = pv.groupby(grouped_date).cumsum() / df["volume"].groupby(grouped_date).cumsum()
        return df
