# src/strategy/breakout_close.py
from __future__ import annotations

import numpy as np
import pandas as pd
from .base import StrategyBase, Signal


class BreakoutClose(StrategyBase):
    """
    Breakout-strategi med intrabar-trigg, re-entry og pyramidering.

    Parametre:
      - breakout_mode: "close" (bar-close) eller "intra" (bruk high/low i pågående bar)
      - lookback: Donchian-vindu for brudd
      - swing_lookback: for SL (siste swing)
      - atr_period: for ATR-gulvberegning
      - atr_floor_mult: gulv for SL-avstand (multipler av ATR)
      - retest_entries: tillat re-entry ved retest av bruddnivå
      - retest_window: hvor lenge (bar-er) en retest er gyldig
      - max_adds: maks antall ekstra entries etter første (pyramidering)
    """
    name = "BreakoutClose"
    default_symbol = "DE40"

    def __init__(
        self,
        lookback: int = 10,
        swing_lookback: int = 3,
        atr_period: int = 14,
        atr_floor_mult: float = 0.25,
        breakout_mode: str = "intra",   # "close" eller "intra"
        retest_entries: bool = True,
        retest_window: int = 5,
        max_adds: int = 1,
    ):
        super().__init__()
        self.lookback = lookback
        self.swing_lookback = swing_lookback
        self.atr_period = atr_period
        self.atr_floor_mult = atr_floor_mult
        self.breakout_mode = breakout_mode
        self.retest_entries = retest_entries
        self.retest_window = retest_window
        self.max_adds = max_adds

        # intern state for retest/pyramidering
        self._last_break_price: float | None = None
        self._last_break_side: str | None = None
        self._last_break_bar_index: int | None = None
        self._adds_taken: int = 0

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        h, l, c = df["high"], df["low"], df["close"]
        tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
        return tr.rolling(period, min_periods=period).mean()

    @staticmethod
    def _last_swing_low(df: pd.DataFrame, lb: int) -> float | None:
        if len(df) < lb + 2:
            return None
        window = df["low"].iloc[-(lb + 1):-1]
        return float(window.min()) if not window.empty else None

    @staticmethod
    def _last_swing_high(df: pd.DataFrame, lb: int) -> float | None:
        if len(df) < lb + 2:
            return None
        window = df["high"].iloc[-(lb + 1):-1]
        return float(window.max()) if not window.empty else None

    def _donchian(self, df: pd.DataFrame) -> tuple[float, float]:
        """
        Donchian-kanaler basert på *forrige* N barer (ekskluderer gjeldende bar).
        Dette gjelder både for "close" og "intra" – slik at trigg-testen gir mening.
        """
        idx = -2  # <- VIKTIG: ekskluder nåværende bar i begge modus
        highest_n = float(df["high"].rolling(self.lookback).max().iloc[idx])
        lowest_n = float(df["low"].rolling(self.lookback).min().iloc[idx])
        return highest_n, lowest_n

    def _compute_sl_tp(self, side: str, entry: float, df: pd.DataFrame, atr_val: float) -> tuple[float, float]:
        floor = self.atr_floor_mult * atr_val
        if side == "buy":
            swing = self._last_swing_low(df, self.swing_lookback)
            if swing is None:
                return None, None  # type: ignore
            sl_candidate = max(swing, entry - floor)
            sl = min(entry - 1e-6, sl_candidate)
            if sl >= entry:
                return None, None  # type: ignore
            r = entry - sl
            tp = entry + 2.0 * r
            return float(sl), float(tp)
        else:
            swing = self._last_swing_high(df, self.swing_lookback)
            if swing is None:
                return None, None  # type: ignore
            sl_candidate = min(swing, entry + floor)
            sl = max(entry + 1e-6, sl_candidate)
            if sl <= entry:
                return None, None  # type: ignore
            r = sl - entry
            tp = entry - 2.0 * r
            return float(sl), float(tp)

    def _maybe_retest(self, df: pd.DataFrame) -> Signal | None:
        """
        Re-entry på retest av bruddnivå (innen retest_window barer).
        """
        if not self.retest_entries:
            return None
        if self._last_break_price is None or self._last_break_side is None or self._last_break_bar_index is None:
            return None
        if self._adds_taken >= self.max_adds:
            return None

        # hvor mange barer siden bruddet
        current_index = len(df) - 1
        if (current_index - self._last_break_bar_index) > self.retest_window:
            return None

        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])

        if self._last_break_side == "buy":
            if low <= self._last_break_price <= high:
                return Signal("buy", price=close, meta={})
        else:
            if low <= self._last_break_price <= high:
                return Signal("sell", price=close, meta={})

        return None

    def on_bar(self, df: pd.DataFrame) -> Signal | None:
        need = max(self.lookback, self.swing_lookback, self.atr_period) + 2
        if len(df) < need:
            return None

        atr_series = self._atr(df, self.atr_period)
        atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else None
        if atr is None or atr <= 0:
            return None

        highest_n, lowest_n = self._donchian(df)
        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])

        # 1) Primær breakout-trigg
        if self.breakout_mode == "close":
            buy_trig = close > highest_n
            sell_trig = close < lowest_n
        else:  # "intra": sammenlign barens high/low med forrige N-bars nivåer
            buy_trig = high > highest_n
            sell_trig = low < lowest_n

        if buy_trig:
            entry = close if self.breakout_mode == "close" else max(close, highest_n)
            sl, tp = self._compute_sl_tp("buy", entry, df, atr)
            if sl is None or tp is None:
                return None
            # logg brudd for ev. retest/pyramidering
            self._last_break_price = highest_n
            self._last_break_side = "buy"
            self._last_break_bar_index = len(df) - 1
            self._adds_taken = 0
            return Signal("buy", price=float(entry), meta={"sl": float(sl), "tp": float(tp)})

        if sell_trig:
            entry = close if self.breakout_mode == "close" else min(close, lowest_n)
            sl, tp = self._compute_sl_tp("sell", entry, df, atr)
            if sl is None or tp is None:
                return None
            self._last_break_price = lowest_n
            self._last_break_side = "sell"
            self._last_break_bar_index = len(df) - 1
            self._adds_taken = 0
            return Signal("sell", price=float(entry), meta={"sl": float(sl), "tp": float(tp)})

        # 2) Re-entry/pyramidering (frivillig)
        re = self._maybe_retest(df)
        if re:
            entry = float(df["close"].iloc[-1])
            sl, tp = self._compute_sl_tp(re.side, entry, df, atr)
            if sl is None or tp is None:
                return None
            self._adds_taken += 1
            re.meta = {"sl": float(sl), "tp": float(tp)}
            return re

        return None
