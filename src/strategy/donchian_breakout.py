# src/strategy/donchian_breakout.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

import numpy as np
import pandas as pd

from .base import StrategyBase, Signal


@dataclass
class DonchianParams:
    lookback: int = 20          # Donchian-vindu
    atr_period: int = 14        # ATR for SL/TP
    rr: float = 3.0             # Risk:Reward (TP = rr * SL)
    ema_filter: Optional[int] = 200  # Trendfilter (EMA). None for av
    breakout_mode: str = "close"     # "close" eller "intra"
    atr_floor_mult: float = 0.0      # Valgfri gulv-multiplier på ATR (0 = av)


class DonchianBreakout(StrategyBase):
    """
    Enkel trendfølgende Donchian-breakout:
      - Valgfritt trendfilter med EMA(ema_filter)
      - Kjøp når pris bryter over høyeste high i 'lookback'
      - Selg når pris bryter under laveste low i 'lookback'
      - SL = 1 * ATR(atr_period) fra entry
      - TP = rr * SL
    """

    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        rr: float = 3.0,
        ema_filter: Optional[int] = 200,
        breakout_mode: str = "close",  # "close" eller "intra"
        atr_floor_mult: float = 0.0,
        default_symbol: Optional[str] = None,
        name: str = "DonchianBreakout",
    ) -> None:
        super().__init__(default_symbol=default_symbol, name=name)
        self.params = DonchianParams(
            lookback=lookback,
            atr_period=atr_period,
            rr=rr,
            ema_filter=ema_filter,
            breakout_mode=breakout_mode,
            atr_floor_mult=atr_floor_mult,
        )

    # --- Hooks (valgfritt) ---
    def on_start(self) -> None:
        return None

    def on_stop(self) -> None:
        return None

    # --- Kjerne ---
    def _ema(self, s: pd.Series, period: int) -> pd.Series:
        return s.ewm(span=period, adjust=False).mean()

    def _atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        prev_close = close.shift(1)
        tr1 = (high - low).abs()
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Wilder's smoothing ≈ EMA med alpha=1/period
        atr = tr.ewm(alpha=1.0 / float(period), adjust=False).mean()
        return atr

    def _compute_bands(self, df: pd.DataFrame, lookback: int) -> tuple[float, float]:
        window = int(lookback)
        hi = float(df["high"].rolling(window=window, min_periods=window).max().iloc[-2])
        lo = float(df["low"].rolling(window=window, min_periods=window).min().iloc[-2])
        return hi, lo

    def _passes_trend_filter(self, df: pd.DataFrame) -> tuple[bool, Optional[str]]:
        if not self.params.ema_filter:
            return True, None
        ema = self._ema(df["close"].astype(float), int(self.params.ema_filter))
        price = float(df["close"].iloc[-1])
        ema_last = float(ema.iloc[-1])
        if price > ema_last:
            return True, "long"
        if price < ema_last:
            return True, "short"
        return False, None

    def _entry_price(self, df: pd.DataFrame) -> float:
        if self.params.breakout_mode == "intra":
            return float(df["close"].iloc[-1])
        # "close": bruk siste close (strategien kjøres ved bar-close)
        return float(df["close"].iloc[-1])

    def _make_signal(self, side: str, entry: float, atr_val: float) -> Optional[Signal]:
        # Valgfri gulv på ATR
        if self.params.atr_floor_mult and self.params.atr_floor_mult > 0.0:
            atr_val = max(atr_val, self.params.atr_floor_mult * entry / 10000.0)  # enkel defensiv gulv

        if atr_val <= 0.0 or not np.isfinite(atr_val):
            return None

        sl_dist = float(atr_val)
        rr = float(self.params.rr)

        if side == "buy":
            sl = entry - sl_dist
            tp = entry + rr * sl_dist
        else:
            sl = entry + sl_dist
            tp = entry - rr * sl_dist

        return Signal(side=side, price=float(entry), meta={"sl": float(sl), "tp": float(tp)})

    def on_bar(self, df: pd.DataFrame) -> Optional[Signal]:
        # Minimum datakrav
        need = max(self.params.lookback + 1, self.params.atr_period + 2, (self.params.ema_filter or 0) + 2)
        if len(df) < need:
            return None

        # Trendfilter
        ok, bias = self._passes_trend_filter(df)
        if not ok:
            return None

        # Donchian-bånd basert på _ferdige_ bars (bruk -2)
        hi, lo = self._compute_bands(df, self.params.lookback)

        # Hvilket prisnivå sjekker vi brudd mot?
        if self.params.breakout_mode == "intra":
            px_high = float(df["high"].iloc[-1])
            px_low = float(df["low"].iloc[-1])
        else:
            # close-basert brudd
            px_high = float(df["close"].iloc[-1])
            px_low = float(df["close"].iloc[-1])

        # ATR for SL/TP
        atr = self._atr(df, self.params.atr_period)
        atr_val = float(atr.iloc[-1])

        # Long-bias kun hvis over EMA (hvis bias=="long"), short-bias kun hvis under (bias=="short")
        # Hvis ema_filter er av, er bias None, og begge brudd er lov
        entry = self._entry_price(df)

        if (bias in (None, "long")) and (px_high > hi):
            return self._make_signal("buy", entry, atr_val)

        if (bias in (None, "short")) and (px_low < lo):
            return self._make_signal("sell", entry, atr_val)

        return None
