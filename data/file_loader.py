from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import INCLUDE_EXTENDED_HOURS, MARKET_CLOSE_TIME, MARKET_OPEN_TIME
from data.base import DataSource


class FileLoader(DataSource):
    def __init__(
        self,
        base_path: str = "../_trade_data",
        start_date: str | None = None,
        end_date: str | None = None,
        include_extended_hours: bool = INCLUDE_EXTENDED_HOURS,
    ):
        self.base_path = Path(base_path)
        self.start_date = start_date
        self.end_date = end_date
        self.include_extended_hours = include_extended_hours
        self._cache: dict[str, pd.DataFrame] = {}

    def get_bars(self, symbol: str) -> dict[str, pd.DataFrame]:
        if symbol not in self._cache:
            self._cache[symbol] = self._load_raw(symbol)
        df_1m = self._cache[symbol].copy()
        return {
            "1m": df_1m,
            "5m": self._resample(df_1m, "5min"),
            "15m": self._resample(df_1m, "15min"),
            "1h": self._resample(df_1m, "1h"),
        }

    def _load_raw(self, symbol: str) -> pd.DataFrame:
        pattern = f"{symbol}_*.json"
        files = sorted((self.base_path / symbol).glob(pattern))
        if not files:
            raise FileNotFoundError(f"No data files found for {symbol} at {self.base_path}")

        bars: list[dict] = []
        for file_path in files:
            with file_path.open() as fh:
                payload = json.load(fh)
            bars.extend(payload.get("bars", payload if isinstance(payload, list) else []))

        df = pd.DataFrame(bars)
        if df.empty:
            return df

        keep = ["time_key", "date", "open", "high", "low", "close", "volume"]
        df = df[[c for c in keep if c in df.columns]].copy()
        df["time_key"] = pd.to_datetime(df["time_key"])
        df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time_key", "open", "high", "low", "close", "volume"])
        df = df.sort_values("time_key").drop_duplicates("time_key")

        if self.start_date:
            df = df[df["time_key"] >= pd.Timestamp(self.start_date)]
        if self.end_date:
            df = df[df["time_key"] < pd.Timestamp(self.end_date) + pd.Timedelta(days=1)]
        if not self.include_extended_hours:
            open_hour, open_minute = [int(part) for part in MARKET_OPEN_TIME.split(":")]
            close_hour, close_minute = [int(part) for part in MARKET_CLOSE_TIME.split(":")]
            market_open = open_hour * 60 + open_minute
            market_close = close_hour * 60 + close_minute
            minute_of_day = df["time_key"].dt.hour * 60 + df["time_key"].dt.minute
            df = df[
                (minute_of_day >= market_open)
                & (minute_of_day <= market_close)
            ]

        return df.reset_index(drop=True)

    def _resample(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        resampled = (
            df.set_index("time_key")
            .resample(freq)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )
        resampled["date"] = resampled["time_key"].dt.date.astype(str)
        return resampled[["time_key", "date", "open", "high", "low", "close", "volume"]]
