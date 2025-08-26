# src/risk/position_sizing.py
from __future__ import annotations

import logging
from typing import Optional

import MetaTrader5 as mt5

log = logging.getLogger(__name__)


def _round_to_step(value: float, step: float) -> float:
    """
    Avrund volum til nærmeste gyldige 'step' (ikke gulv/ceil, men round-to-nearest).
    """
    if step <= 0:
        return value
    return round(round(value / step) * step, 6)


def calc_volume_for_risk(
    *,
    symbol: str,
    entry_price: float,
    stop_loss_price: float,
    risk_fraction: float,  # 0.01 = 1%
) -> Optional[float]:
    """
    Beregn volum (lots) slik at forventet tap ved SL ≈ equity * risk_fraction.

    Formel:
        ticks = abs(entry - SL) / tick_size
        loss_per_lot = ticks * tick_value
        lots = (equity * risk_fraction) / loss_per_lot

    Vi leser broker-regler fra MT5:
      - volume_min / volume_max / volume_step
      - tick_size (trade_tick_size/point)
      - tick_value (pnl per tick for 1 lot)
    """
    if risk_fraction <= 0:
        log.warning("risk_fraction <= 0, hopper over posisjonssizing")
        return None

    si = mt5.symbol_info(symbol)
    if si is None:
        log.error("symbol_info(%s) er None", symbol)
        return None

    tick_size = float(si.trade_tick_size or si.point or 0.0)
    tick_value = float(si.trade_tick_value or 0.0)
    volume_min = float(si.volume_min or 0.01)
    volume_max = float(si.volume_max or 100.0)
    volume_step = float(si.volume_step or 0.01)

    if tick_size <= 0 or tick_value <= 0:
        log.error(
            "Ugyldig tick_size/tick_value for %s (tick_size=%s, tick_value=%s)",
            symbol, tick_size, tick_value
        )
        return None

    distance = abs(entry_price - stop_loss_price)
    if distance <= 0:
        log.error("Stop loss avstand er 0. entry=%s sl=%s", entry_price, stop_loss_price)
        return None

    ai = mt5.account_info()
    if ai is None:
        log.error("account_info() er None")
        return None
    equity = float(ai.equity)

    risk_money = equity * risk_fraction
    ticks = distance / tick_size
    loss_per_lot = ticks * tick_value
    if loss_per_lot <= 0:
        log.error("loss_per_lot <= 0 (ticks=%s, tick_value=%s)", ticks, tick_value)
        return None

    raw_volume = risk_money / loss_per_lot

    # Avrund til nærmeste step og klem innen min/max
    vol = _round_to_step(raw_volume, volume_step)
    if vol < volume_min:
        vol = volume_min
    if vol > volume_max:
        vol = volume_max

    if vol <= 0:
        log.warning("Beregnet volum ble 0 etter regler. raw=%.6f", raw_volume)
        return None

    return vol
