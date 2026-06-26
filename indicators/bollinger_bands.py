import pandas as pd

from indicators.base import Indicator

BB_MID_FLAT_TOLERANCE = 0.001


class BollingerBands(Indicator):
    name = "bollinger_bands"
    display_name = "Bollinger Bands"
    description = "Volatility bands around a moving average."
    category = "volatility"
    default_params = {"period": 15, "std_dev": 2}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        p = self.params["period"]
        s = self.params["std_dev"]
        rolling = df["close"].rolling(p)
        df["bb_mid"] = rolling.mean()
        std = rolling.std()
        df["bb_upper"] = df["bb_mid"] + s * std
        df["bb_lower"] = df["bb_mid"] - s * std
        df["bb_mid_slope"] = df["bb_mid"] - df["bb_mid"].shift(1)
        df["bb_mid_flat"] = df["bb_mid_slope"].abs() <= df["close"] * BB_MID_FLAT_TOLERANCE
        df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["bb_bandwidth_expanding"] = df["bb_bandwidth"] > df["bb_bandwidth"].shift(1)
        return df
