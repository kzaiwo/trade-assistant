from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    @abstractmethod
    def get_bars(self, symbol: str) -> dict[str, pd.DataFrame]:
        """Return bars keyed by timeframe: {"1m": df, "5m": df, "15m": df}"""
