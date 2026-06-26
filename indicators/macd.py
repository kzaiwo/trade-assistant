import pandas as pd

from indicators.base import Indicator


class MACD(Indicator):
    name = "macd"
    display_name = "MACD"
    description = "Moving average convergence divergence trend momentum."
    category = "momentum"
    default_params = {"fast": 12, "slow": 26, "signal": 9}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        fast = df["close"].ewm(span=self.params["fast"], adjust=False).mean()
        slow = df["close"].ewm(span=self.params["slow"], adjust=False).mean()
        df["macd_dif"] = fast - slow
        df["macd_dea"] = df["macd_dif"].ewm(span=self.params["signal"], adjust=False).mean()
        df["macd_hist"] = df["macd_dif"] - df["macd_dea"]
        return df
