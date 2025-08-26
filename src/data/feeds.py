# src/data/feeds.py
from __future__ import annotations

import logging
from typing import Tuple

import MetaTrader5 as mt5
import pandas as pd

from broker.mt5_client import MT5Client

log = logging.getLogger(__name__)

# MT5-konstanter for timeframes
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def resolve_timeframe(tf_key: str) -> Tuple[str, int]:
    """
    Valider og oversett en nøkkel som 'M15' -> ('M15', mt5.TIMEFRAME_M15).
    Kaster ValueError hvis ugyldig.
    """
    key = (tf_key or "").upper()
    if key not in TIMEFRAME_MAP:
        raise ValueError(f"Ukjent timeframe '{tf_key}'. Gyldige: {', '.join(TIMEFRAME_MAP.keys())}")
    return key, TIMEFRAME_MAP[key]


def get_bars(mt: MT5Client, symbol: str, tf_key: str, count: int = 300) -> pd.DataFrame:
    """
    Hent siste 'count' bars for symbol/timeframe som DataFrame (OHLCV).
    Bruker MT5Client.rates_df under panseret.
    """
    _, tf_val = resolve_timeframe(tf_key)
    return mt.rates_df(symbol, tf_val, count=count)


def is_new_bar(prev_time, df: pd.DataFrame) -> tuple[bool, pd.Timestamp | None]:
    """
    Sjekk om siste rad i df representerer en ny bar (vs. prev_time).
    Returnerer (is_new, current_time).
    """
    if df is None or df.empty:
        return False, None
    cur = df["time"].iloc[-1]
    return (prev_time is None or cur != prev_time), cur
