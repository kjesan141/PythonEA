# src/strategy/donchian_breakout.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
from .base import StrategyBase, Signal

@dataclass
class DonchianParams:
    lookback: int = 20
    atr_period: int = 14
    rr: float = 3.0
    ema_filter: Optional[int] = 200   # None/0 = av
    breakout_mode: str = "close"      # "close" eller "intra"
    atr_floor_mult: float = 0.0

class DonchianBreakout(StrategyBase):
    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        rr: float = 3.0,
        ema_filter: Optional[int] = 200,
        breakout_mode: str = "close",
        atr_floor_mult: float = 0.0,
        default_symbol: Optional[str] = None,
    ) -> None:
        # IKKE kall super().__init__ (basen har ingen __init__)
        self._default_symbol = default_symbol
        self.params = DonchianParams(
            lookback=int(lookback),
            atr_period=int(atr_period),
            rr=float(rr),
            ema_filter=(None if (ema_filter is None or int(ema_filter) == 0) else int(ema_filter)),
            breakout_mode=str(breakout_mode),
            atr_floor_mult=float(atr_floor_mult),
        )

    # --- Properties for kompatibilitet med StrategyBase (read-only) ---
    @property
    def name(self) -> str:
        return "DonchianBreakout"

    @property
    def default_symbol(self) -> Optional[str]:
        return self._default_symbol

    # --- Intern beregning ---
    def _ema(self, s: pd.Series, period: int) -> pd.Series:
        return s.ewm(span=period, adjust=False).mean()

    def _atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"].astype(float)
        low  = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr1 = (high - low).abs()
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0/float(period), adjust=False).mean()  # Wilder approx

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
        if price > ema_last:  return True, "long"
        if price < ema_last:  return True, "short"
        return False, None

    def _entry_price(self, df: pd.DataFrame) -> float:
        return float(df["close"].iloc[-1])  # runner legger market-ordre

    def _make_signal(self, side: str, entry: float, atr_val: float) -> Optional[Signal]:
        if self.params.atr_floor_mult and self.params.atr_floor_mult > 0.0:
            atr_val = max(atr_val, self.params.atr_floor_mult * entry / 10000.0)
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

    # --- Kalles fra runner ---
    def on_bar(self, df: pd.DataFrame) -> Optional[Signal]:
        need = max(self.params.lookback + 1,
                   self.params.atr_period + 2,
                   (self.params.ema_filter or 0) + 2)
        if len(df) < need:
            return None

        ok, bias = self._passes_trend_filter(df)
        if not ok:
            return None

        hi, lo = self._compute_bands(df, self.params.lookback)

        if self.params.breakout_mode == "intra":
            px_high = float(df["high"].iloc[-1])
            px_low  = float(df["low"].iloc[-1])
        else:
            px_high = float(df["close"].iloc[-1])
            px_low  = float(df["close"].iloc[-1])

        atr_val = float(self._atr(df, self.params.atr_period).iloc[-1])
        entry = self._entry_price(df)

        if (bias in (None, "long")) and (px_high > hi):
            return self._make_signal("buy", entry, atr_val)
        if (bias in (None, "short")) and (px_low < lo):
            return self._make_signal("sell", entry, atr_val)
        return None
