from abc import ABC, abstractmethod

import pandas as pd


class Indicator(ABC):
    name: str
    display_name: str
    description: str
    category: str = "technical"
    default_params: dict = {}

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to df and return it. Pure computation."""
