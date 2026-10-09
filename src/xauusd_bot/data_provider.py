"""Historical bid+ask OHLC data acquisition for XAUUSD.

Only the base 5-minute series is ever fetched; Daily/4H are derived from
it via timeframes.build_timeframes so every timeframe's candle
boundaries stay mutually consistent (see that module's docstring).

Both bid and ask sides are fetched/loaded (not just bid) because the H4
pullback strategy's fills are modeled with the real cost of a spread on
every entry and exit, not a single frictionless price.

NOTE on this sandbox vs. your machine: fetching from Dukascopy was
validated to work end-to-end (package installs, request shape is
correct), but the sandbox this was developed in blocks outbound requests
to arbitrary hosts under its own egress policy -- freeserv.dukascopy.com
is not on that allowlist, so the live fetch could not be executed here.
Run `xauusd-bot fetch-data ...` (or call `DukascopyDataProvider.fetch_5m_bidask`
directly) on your own machine, where no such restriction applies, and
inspect the cached parquet file it writes before trusting a backtest run
against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


@dataclass
class DukascopyDataProvider:
    symbol: str = "XAUUSD"
    cache_dir: Path = Path(".cache/xauusd_bot")

    def fetch_5m_bidask(
        self, start: datetime, end: datetime, use_cache: bool = True
    ) -> pd.DataFrame:
        """Returns a DataFrame with open/high/low/close (bid) plus
        ao/ah/al/ac (ask), at 5-minute resolution.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = (
            self.cache_dir
            / f"{self.symbol}_5min_bidask_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
        )
        if use_cache and cache_file.exists():
            return pd.read_parquet(cache_file)

        import dukascopy_python

        bid = self._normalize(
            dukascopy_python.fetch(
                self.symbol, dukascopy_python.INTERVAL_MIN_5, dukascopy_python.OFFER_SIDE_BID, start, end
            )
        )
        ask = self._normalize(
            dukascopy_python.fetch(
                self.symbol, dukascopy_python.INTERVAL_MIN_5, dukascopy_python.OFFER_SIDE_ASK, start, end
            )
        ).rename(columns={"open": "ao", "high": "ah", "low": "al", "close": "ac"})
        combined = bid.join(ask, how="inner")
        combined.to_parquet(cache_file)
        return combined

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        if not isinstance(df.index, pd.DatetimeIndex):
            time_col = next(c for c in df.columns if "time" in c.lower())
            df = df.set_index(pd.to_datetime(df[time_col], utc=True)).drop(columns=[time_col])
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        keep = [c for c in ["open", "high", "low", "close"] if c in df.columns]
        return df[keep].sort_index()


def load_csv_5m_bidask(path: str | Path, tz: str = "UTC") -> pd.DataFrame:
    """Load base 5-minute bid+ask OHLC from a local CSV with columns
    [timestamp, open, high, low, close, ao, ah, al, ac] (timestamp
    parseable by pandas, assumed UTC unless `tz` says otherwise).
    """
    df = pd.read_csv(path)
    ts_col = next(c for c in df.columns if c.lower() in ("timestamp", "time", "datetime", "date"))
    df[ts_col] = pd.to_datetime(df[ts_col], utc=(tz == "UTC"))
    if tz != "UTC":
        df[ts_col] = df[ts_col].dt.tz_localize(tz).dt.tz_convert("UTC")
    df = df.set_index(ts_col).sort_index()
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close", "ao", "ah", "al", "ac"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required bid/ask columns: {missing}")
    return df[["open", "high", "low", "close", "ao", "ah", "al", "ac"]]
