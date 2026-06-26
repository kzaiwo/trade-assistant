import pandas as pd

from indicators.base import Indicator


class BollingerBands(Indicator):
    name = "bollinger_bands"
    display_name = "Bollinger Bands"
    description = "Volatility bands around a moving average."
    category = "volatility"
    default_params = {"period": 20, "std_dev": 2}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        p = self.params["period"]
        s = self.params["std_dev"]
        rolling = df["close"].rolling(p)
        df["bb_mid"] = rolling.mean()
        std = rolling.std()
        df["bb_upper"] = df["bb_mid"] + s * std
        df["bb_lower"] = df["bb_mid"] - s * std
        return df
