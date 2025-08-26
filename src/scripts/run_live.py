# scripts/run_live.py
from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional, Tuple
from datetime import datetime, date, timedelta

import MetaTrader5 as mt5
import pandas as pd

from core.config import settings
from core.logging import setup_logging
from broker.mt5_client import MT5Client
from data.feeds import TIMEFRAME_MAP
from strategy.breakout_close import BreakoutClose  # AGGRESSIV variant
from risk.position_sizing import calc_volume_for_risk


# ---------------------- Utils ----------------------

def resolve_symbol(cli_symbol: Optional[str], strategy_obj) -> str:
    log = logging.getLogger("runner")
    if cli_symbol:
        log.info("Symbol valgt via CLI: %s", cli_symbol)
        return cli_symbol
    if getattr(strategy_obj, "default_symbol", None):
        ds = strategy_obj.default_symbol
        log.info("Symbol valgt via strategi-default: %s", ds)
        return ds
    fallback = getattr(settings, "symbol_fallback", "EURUSD")
    log.warning("Ingen symbol angitt – faller tilbake til %s", fallback)
    return fallback


def timeframe_to_mt5(tf_code: str) -> int:
    tf_code = tf_code.upper()
    if tf_code not in TIMEFRAME_MAP:
        raise ValueError(f"Ukjent timeframe: {tf_code}")
    return TIMEFRAME_MAP[tf_code]


def fetch_df(symbol: str, tf_const: int, bars: int) -> Optional[pd.DataFrame]:
    """
    Hent siste 'bars' candles som DataFrame. Returnerer None ved feil/ingen data.
    """
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bars)
    if rates is None:
        logging.getLogger("runner").warning("copy_rates_from_pos(%s) ga None. last_error=%s", symbol, mt5.last_error())
        return None
    if len(rates) == 0:
        logging.getLogger("runner").warning("Ingen rates for %s (len=0). last_error=%s", symbol, mt5.last_error())
        return None
    try:
        df = pd.DataFrame(rates)
        if df.empty:
            return None
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.rename(columns={"time": "datetime"}, inplace=True)
        return df
    except Exception as e:
        logging.getLogger("runner").exception("Klarte ikke konvertere rates til DataFrame: %s", e)
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kjør EA live/paper mot MT5")
    p.add_argument("--account", type=str, help="Kontoalias eller login (valgfri)")
    p.add_argument("--mode", type=str, choices=["live", "paper"], default="paper", help="Kjør live eller paper")
    p.add_argument("--symbol", type=str, help="Overstyr symbol")
    p.add_argument("--timeframe", type=str, help="Overstyr timeframe (M1,M5,M15,M30,H1,H4,D1...)")
    p.add_argument("--bars", type=int, default=500, help="Antall historiske barer som brukes")
    p.add_argument("--poll", type=float, help="Poll-interval i sekunder (overstyr)")
    return p.parse_args()


# ---------------------- MT5 Init/Login (robust) ----------------------

def _try_mt5client_initialize(mt: MT5Client, account_hint: Optional[str]) -> bool:
    """Prøv å initialisere MT5Client med account_id fra config."""
    log = logging.getLogger("runner")

    if hasattr(mt, "initialize") and callable(getattr(mt, "initialize")):
        log.info("Prøver MT5Client.initialize()")
        mt.initialize()
        return True

    if hasattr(mt, "connect") and callable(getattr(mt, "connect")):
        try:
            acc_id, _, _, _ = settings.get_login_params(account_hint)
            log.info("Prøver MT5Client.connect(account_id=%s)", acc_id)
            ok = mt.connect(account_id=acc_id)
            return bool(ok) if ok is not None else True
        except Exception as e:
            log.warning("MT5Client.connect feilet: %s", e)
            return False

    log.info("Ingen init-metode på MT5Client – antar allerede initialisert")
    return True


def _raw_mt5_initialize_and_login(account_hint: Optional[str]) -> None:
    """
    Fallback: initialiser MetaTrader5 direkte og logg inn hvis vi har creds.
    Respekterer EA__MT5_PATH dersom satt.
    """
    log = logging.getLogger("runner")

    path = getattr(settings, "mt5_path", None)
    if path:
        log.info("MetaTrader5.initialize(path=%s)", path)
        if not mt5.initialize(path=path):
            raise RuntimeError(f"MetaTrader5.initialize feilet: {mt5.last_error()}")
    else:
        log.info("MetaTrader5.initialize() uten path")
        if not mt5.initialize():
            raise RuntimeError(f"MetaTrader5.initialize feilet: {mt5.last_error()}")

    # Login hvis vi har credentials
    try:
        acc_id, key_used, password, server = settings.get_login_params(account_hint)
    except Exception:
        acc_id = None
        password = None
        server = None
        key_used = None

    if acc_id and password and server:
        log.info("Prøver mt5.login(login=%s, server=%s, key=%s)", acc_id, server, key_used or "")
        if not mt5.login(login=int(acc_id), password=password, server=server):
            raise RuntimeError(f"mt5.login feilet: {mt5.last_error()}")
    else:
        info = mt5.account_info()
        if info is None:
            log.warning("Ingen konto innlogget, og mangler creds i .env – kjører videre hvis terminal allerede er innlogget.")


def init_mt(account_hint: Optional[str]) -> MT5Client:
    """
    Robust init som støtter:
      - MT5Client.initialize() eller .connect()
      - Ellers raw MetaTrader5.initialize() + login via settings
    """
    log = logging.getLogger("runner")
    mt = MT5Client()

    try:
        ok = _try_mt5client_initialize(mt, account_hint)
        if not ok:
            log.warning("MT5Client init returnerte falsy – prøver rå MetaTrader5-init")
            _raw_mt5_initialize_and_login(account_hint)
    except Exception as e:
        log.warning("MT5Client-init feilet (%s) – prøver rå MetaTrader5-init", e)
        _raw_mt5_initialize_and_login(account_hint)

    # Logg terminalstatus
    ti = mt5.terminal_info()
    ai = mt5.account_info()
    if ti:
        log.info("Terminal info | name=%s | company=%s | path=%s", ti.name, ti.company, ti.path)
    if ai:
        log.info("Terminal er innlogget | login=%s | server=%s | name=%s", ai.login, ai.server, ai.name)
    else:
        log.warning("Ingen konto rapportert via mt5.account_info()")

    return mt


# ---------------------- Risiko / posisjoner ----------------------

def has_open_position_count(symbol: str) -> int:
    poss = mt5.positions_get(symbol=symbol)
    return len(poss) if poss else 0


def min_stop_distance_ok(symbol: str, entry: float, sl: float) -> bool:
    """
    Sjekk brokerens minimum stop-avstand (stops_level).
    stops_level er i points; min prisavstand = stops_level * point.
    """
    si = mt5.symbol_info(symbol)
    if si is None:
        return False
    point = float(si.point or 0.0)
    stops_points = int(getattr(si, "stops_level", 0) or 0)
    min_dist = stops_points * point
    if min_dist <= 0:
        return True  # ingen minimum oppgitt
    dist = abs(entry - sl)
    return dist >= min_dist


def apply_caps(volume: float, risk_fraction: float) -> float:
    """
    Cap på maks volum og ev. maks kr-risiko (skalerer volum ned proporsjonalt).
    """
    vol = float(volume)

    # Volum-cap
    max_vol = float(getattr(settings, "max_volume", 0.0) or 0.0)
    if max_vol > 0.0 and vol > max_vol:
        vol = max_vol

    # Risiko-cap i penger (valgfri)
    max_risk_money = float(getattr(settings, "max_risk_money", 0.0) or 0.0)
    if max_risk_money > 0.0 and risk_fraction > 0.0:
        ai = mt5.account_info()
        if ai and ai.equity and ai.equity > 0:
            intended_risk_money = float(ai.equity) * float(risk_fraction)
            if intended_risk_money > 0 and max_risk_money < intended_risk_money:
                scale = max_risk_money / intended_risk_money
                vol = vol * scale

    return max(0.0, vol)


def normalize_volume(symbol: str, volume: float) -> float:
    """
    Rund volum til nærmeste gyldige step og klem innen min/max iht. broker-regler.
    """
    si = mt5.symbol_info(symbol)
    if si is None:
        return max(0.0, volume)

    volume_min = float(si.volume_min or 0.01)
    volume_max = float(si.volume_max or 100.0)
    volume_step = float(si.volume_step or 0.01)

    if volume_step > 0:
        volume = round(round(volume / volume_step) * volume_step, 6)

    if volume < volume_min:
        volume = volume_min
    if volume > volume_max:
        volume = volume_max

    return max(0.0, volume)


def _loss_per_lot_if_sl(symbol: str, entry: float, sl: float) -> float:
    """
    Estimer PnL-tap pr 1 lot dersom SL treffes (bruker tick_size og tick_value).
    """
    si = mt5.symbol_info(symbol)
    if not si:
        return 0.0
    tick_size = float(si.trade_tick_size or si.point or 0.0)
    tick_value = float(si.trade_tick_value or 0.0)
    if tick_size <= 0 or tick_value <= 0:
        return 0.0
    ticks = abs(entry - sl) / tick_size
    return ticks * tick_value


def current_portfolio_risk_percent() -> float:
    """
    Estimer total %-risiko for alle åpne posisjoner gitt deres SL.
    """
    ai = mt5.account_info()
    if not ai or not ai.equity:
        return 0.0
    eq = float(ai.equity)
    total = 0.0
    for p in (mt5.positions_get() or []):
        if not getattr(p, "sl", 0.0):
            continue
        entry = float(p.price_open)
        sl = float(p.sl)
        loss_per_lot = _loss_per_lot_if_sl(p.symbol, entry, sl)
        if loss_per_lot <= 0:
            continue
        total += loss_per_lot * float(p.volume)
    return (total / eq) * 100.0


# ---------------------- Daily loss guard ----------------------

class DailyLossGuard:
    def __init__(self, max_loss_pct: float, max_loss_money: float):
        self.max_loss_pct = float(max_loss_pct or 0.0)   # 0 = av
        self.max_loss_money = float(max_loss_money or 0.0)
        self.day: date = date.today()
        ai = mt5.account_info()
        self.start_equity: float = float(ai.equity) if ai and ai.equity else 0.0
        self.locked_for_today: bool = False

    def _reset_if_new_day(self):
        today = date.today()
        if today != self.day:
            ai = mt5.account_info()
            self.day = today
            self.start_equity = float(ai.equity) if ai and ai.equity else 0.0
            self.locked_for_today = False
            logging.getLogger("runner").info("📆 Ny dag: baseline equity satt til %.2f", self.start_equity)

    def should_block_new_trades(self) -> bool:
        self._reset_if_new_day()
        if self.locked_for_today:
            return True
        ai = mt5.account_info()
        if not ai or not ai.equity:
            return False
        current_eq = float(ai.equity)
        dd_money = max(0.0, self.start_equity - current_eq)
        dd_pct = (dd_money / self.start_equity * 100.0) if self.start_equity > 0 else 0.0

        over_money = self.max_loss_money > 0.0 and dd_money >= self.max_loss_money
        over_pct = self.max_loss_pct > 0.0 and dd_pct >= self.max_loss_pct
        if over_money or over_pct:
            self.locked_for_today = True
            logging.getLogger("runner").warning(
                "⛔ Max daily loss truffet: drawdown=%.2f (%.2f%%) | grenser: money=%.2f pct=%.2f%%. "
                "Ingen nye trades i dag.",
                dd_money, dd_pct, self.max_loss_money, self.max_loss_pct
            )
            return True
        return False


# ---------------------- Historikk / PnL ----------------------

def realized_pnl_last_24h() -> float:
    """
    Summerer realisert PnL (profit + swap + commission) for deals siste 24t.
    Fungerer på MT5-builds uten history_select.
    """
    end = datetime.now()
    start = end - timedelta(hours=24)

    deals = None
    try:
        deals = mt5.history_deals_get(start, end)
    except Exception:
        deals = None

    if deals is None and hasattr(mt5, "history_select"):
        try:
            if mt5.history_select(start, end):
                deals = mt5.history_deals_get()
        except Exception:
            deals = None

    if deals is None:
        return 0.0

    total = 0.0
    for d in deals:
        total += float(getattr(d, "profit", 0.0))
        total += float(getattr(d, "swap", 0.0))
        total += float(getattr(d, "commission", 0.0))
    return total


# ---------------------- Order send (robust) ----------------------

def send_market_order(
    mt: MT5Client,
    *,
    symbol: str,
    side: str,
    volume: float,
    sl: Optional[float],
    tp: Optional[float],
    comment: str,
) -> Tuple[bool, Optional[int], Optional[int]]:
    """
    Abstraksjon:
      - Bruk MT5Client.market_order hvis finnes
      - Ellers raw mt5.order_send
    Returnerer (ok, order_id, deal_id).
    """
    log = logging.getLogger("runner")
    if hasattr(mt, "market_order") and callable(getattr(mt, "market_order")):
        return mt.market_order(symbol=symbol, side=side, volume=volume, sl=sl, tp=tp, comment=comment)

    side = side.lower()
    type_map = {"buy": mt5.ORDER_TYPE_BUY, "sell": mt5.ORDER_TYPE_SELL}
    if side not in type_map:
        log.error("Ugyldig side=%s", side)
        return False, None, None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.error("symbol_info_tick(%s) er None", symbol)
        return False, None, None

    price = tick.ask if side == "buy" else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "type": type_map[side],
        "volume": float(volume),
        "price": float(price),
        "deviation": 20,
        "magic": 0,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if sl is not None:
        request["sl"] = float(sl)
    if tp is not None:
        request["tp"] = float(tp)

    res = mt5.order_send(request)
    if res is None:
        log.error("order_send returnerte None: %s", mt5.last_error())
        return False, None, None
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("order_send feilkode=%s | %s", res.retcode, res._asdict())
        return False, getattr(res, "order", None), getattr(res, "deal", None)

    return True, getattr(res, "order", None), getattr(res, "deal", None)


def adjust_tp_to_exact_2r(symbol: str, side: str, sl: float) -> None:
    """
    Etter vellykket ordre: sett TP = fill ± 2R (R = |fill - SL|), basert på faktisk fill/åpen posisjon.
    Tar hensyn til brokerens min. avstand for TP.
    """
    log = logging.getLogger("runner")
    poss = mt5.positions_get(symbol=symbol) or []
    if not poss:
        log.warning("Fant ingen åpen posisjon for %s ved TP-justering.", symbol)
        return
    pos = sorted(poss, key=lambda p: getattr(p, "time", 0))[-1]
    fill = float(pos.price_open)

    r = abs(fill - sl)
    if r <= 0:
        log.warning("R ble 0 ved TP-justering. fill=%.5f sl=%.5f", fill, sl)
        return

    new_tp = fill + (2.0 * r if side.lower() == "buy" else -2.0 * r)

    si = mt5.symbol_info(symbol)
    if si:
        point = float(si.point or 0.0)
        stops_points = int(getattr(si, "stops_level", 0) or 0)
        min_dist = stops_points * point
        if min_dist > 0:
            if side.lower() == "buy" and (new_tp - fill) < min_dist:
                new_tp = fill + min_dist
            if side.lower() == "sell" and (fill - new_tp) < min_dist:
                new_tp = fill - min_dist

    current_tp = float(pos.tp) if getattr(pos, "tp", 0.0) else 0.0
    if abs(current_tp - new_tp) <= 1e-6:
        log.info("TP var allerede ~2R (%.5f). Hopper justering.", current_tp)
        return

    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": int(pos.ticket),
        "sl": float(sl),
        "tp": float(new_tp),
        "symbol": symbol,
    }
    mod = mt5.order_send(req)
    if mod and mod.retcode == mt5.TRADE_RETCODE_DONE:
        log.info("🔧 TP justert til eksakt 2R fra fill: %.5f (fill=%.5f, SL=%.5f, R=%.5f)",
                 new_tp, fill, sl, r)
    else:
        log.warning("Kunne ikke oppdatere TP til 2R. Retcode=%s", getattr(mod, "retcode", None))


# ---------------------- Hovedprogram ----------------------

def main() -> int:
    setup_logging()
    log = logging.getLogger("runner")
    args = parse_args()

    # Aggressiv strategi
    strat = BreakoutClose(
        lookback=5,
        swing_lookback=3,
        atr_period=14,
        atr_floor_mult=0.25,
        breakout_mode="intra",
        retest_entries=True,
        retest_window=5,
        max_adds=1,
    )

    # Konfig / CLI
    symbol = resolve_symbol(args.symbol, strat)
    timeframe_code = (args.timeframe or getattr(settings, "default_timeframe",
                                                getattr(settings, "timeframe_fallback", "M15"))).upper()
    tf_const = timeframe_to_mt5(timeframe_code)
    bars = args.bars
    poll_sec = args.poll or float(getattr(settings, "polling_sec", 5.0))
    mode = args.mode
    base_risk_fraction = float(getattr(settings, "risk_percent", 1.0)) / 100.0

    # Limits/styring
    max_positions_per_symbol = int(getattr(settings, "max_positions_per_symbol", 3))
    max_total_risk_percent = float(getattr(settings, "max_total_risk_percent", 0.0) or 0.0)
    max_daily_loss_pct = float(getattr(settings, "max_daily_loss_percent", 0.0) or 0.0)
    max_daily_loss_money = float(getattr(settings, "max_daily_loss_money", 0.0) or 0.0)

    log.info(
        "Starter EA | mode=%s | symbol=%s | timeframe=%s | bars=%d | poll=%.2fs",
        mode, symbol, timeframe_code, bars, poll_sec
    )
    log.info(
        "Risikostyring: %s | risk_percent=%.2f%% | max_pos/symbol=%d | max_total_risk=%.2f%% | max_daily_loss=%.2f%% / %.2f",
        "ON" if getattr(settings, "use_risk_sizing", True) else "OFF",
        float(getattr(settings, "risk_percent", 1.0)),
        max_positions_per_symbol,
        max_total_risk_percent,
        max_daily_loss_pct,
        max_daily_loss_money,
    )

    # Init MT + ev. login
    try:
        mt = init_mt(args.account)
    except Exception as e:
        log.exception("Klarte ikke initialisere MT5/innlogging: %s", e)
        return 1

    # Sørg for at symbolet er synlig
    si = mt5.symbol_info(symbol)
    if si is None:
        log.error("symbol_info(%s) er None. Er symbolet riktig og tilgjengelig hos megler?", symbol)
        return 1
    if not si.visible:
        if not mt5.symbol_select(symbol, True):
            log.error("Klarte ikke gjøre symbolet synlig: %s", symbol)
            return 1

    # Daily loss guard
    dguard = DailyLossGuard(max_daily_loss_pct, max_daily_loss_money)

    # Hovedløkke
    try:
        last_bar_time = None
        while True:
            df = fetch_df(symbol, tf_const, bars)
            if df is None or df.empty:
                log.warning("Mangler kursdata for %s (%s). Prøver igjen...", symbol, timeframe_code)
                time.sleep(poll_sec)
                continue

            current_bar_time = df["datetime"].iloc[-1]
            if last_bar_time is not None and current_bar_time == last_bar_time:
                time.sleep(poll_sec)
                continue
            last_bar_time = current_bar_time

            # Heartbeat: equity + realisert PnL siste 24t + åpen risiko
            last_close = float(df["close"].iloc[-1])
            ai = mt5.account_info()
            equity = float(ai.equity) if ai and ai.equity else 0.0
            pnl_24h = realized_pnl_last_24h()
            used_risk = current_portfolio_risk_percent()
            log.info("🕒 Ny bar: %s | close=%.5f | Equity=%.2f | Realisert siste 24t=%.2f | Brukt risiko=%.2f%%",
                     current_bar_time, last_close, equity, pnl_24h, used_risk)

            # Stopp nye handler hvis max daily loss er truffet
            if dguard.should_block_new_trades():
                time.sleep(poll_sec)
                continue

            # Posisjonskvote per symbol
            open_count = has_open_position_count(symbol)
            if open_count >= max_positions_per_symbol:
                log.info("📌 %s har allerede %d posisjoner (tak=%d) – hopper nytt entry.",
                         symbol, open_count, max_positions_per_symbol)
                time.sleep(poll_sec)
                continue

            # Globalt risikotak – justér effektiv risiko hvis nødvendig
            risk_fraction = base_risk_fraction
            if max_total_risk_percent > 0.0:
                used = current_portfolio_risk_percent()
                remaining = max(0.0, max_total_risk_percent - used)
                if remaining <= 0.0:
                    log.info("⛔ Porteføljerisiko nå %.2f%% ≥ tak %.2f%% – hopper ordre.", used, max_total_risk_percent)
                    time.sleep(poll_sec)
                    continue
                desired = risk_fraction * 100.0
                effective = min(desired, remaining) / 100.0
                if effective <= 0.0:
                    log.info("⛔ Ingen rest-risiko igjen – hopper ordre.")
                    time.sleep(poll_sec)
                    continue
                if effective < risk_fraction:
                    log.info("⚖️ Skalerer ned risiko fra %.2f%% → %.2f%% pga. globalt tak (brukt=%.2f%%, maks=%.2f%%)",
                             risk_fraction * 100, effective * 100, used, max_total_risk_percent)
                risk_fraction = effective

            # Strategi-signal
            signal = strat.on_bar(df)

            # Ekstra innsikt når det ikke blir signal
            if not signal or signal.side not in ("buy", "sell"):
                try:
                    hi, lo = strat._donchian(df)  # samme logikk som strategien
                    close = float(df["close"].iloc[-1])
                    high = float(df["high"].iloc[-1])
                    low = float(df["low"].iloc[-1])
                    logging.getLogger("runner").debug(
                        "No signal | mode=%s | close=%.5f high=%.5f low=%.5f | donchian_hi=%.5f donchian_lo=%.5f | lb=%d",
                        getattr(strat, "breakout_mode", "?"), close, high, low, hi, lo, getattr(strat, "lookback", 0)
                    )
                except Exception:
                    pass


            if signal and signal.side in ("buy", "sell"):
                try:
                    entry = float(signal.price or df["close"].iloc[-1])
                except Exception:
                    entry = float(df["close"].iloc[-1])

                sl = float(signal.meta["sl"]) if signal.meta and "sl" in signal.meta else None
                tp = float(signal.meta["tp"]) if signal.meta and "tp" in signal.meta else None

                if sl is None:
                    log.warning("Signal uten SL – hopper ordre. side=%s entry=%.5f", signal.side, entry)
                    time.sleep(poll_sec)
                    continue

                if not min_stop_distance_ok(symbol, entry, sl):
                    log.warning("⛔ SL for nærme (stops_level). entry=%.5f sl=%.5f symbol=%s", entry, sl, symbol)
                    time.sleep(poll_sec)
                    continue

                # Volum ut fra risiko
                if getattr(settings, "use_risk_sizing", True):
                    vol = calc_volume_for_risk(
                        symbol=symbol,
                        entry_price=entry,
                        stop_loss_price=sl,
                        risk_fraction=risk_fraction,
                    )
                    if vol is None:
                        log.warning("Kunne ikke beregne volum – hopper ordre.")
                        time.sleep(poll_sec)
                        continue
                else:
                    vol = 0.10

                # Caps og normalisering
                vol_before = vol
                vol = apply_caps(vol, risk_fraction)
                vol = normalize_volume(symbol, vol)
                log.debug("Volum | beregnet=%.6f | etter_caps=%.6f | normalisert=%.6f", vol_before, apply_caps(vol_before, risk_fraction), vol)

                if vol <= 0:
                    log.warning("⛔ Volum ble 0 etter caps/normalisering – hopper ordre.")
                    time.sleep(poll_sec)
                    continue

                si = mt5.symbol_info(symbol)
                if si:
                    log.info("Vol-regler %s | min=%.6f step=%.6f max=%.6f",
                             symbol,
                             float(si.volume_min or 0),
                             float(si.volume_step or 0),
                             float(si.volume_max or 0))

                if mode == "paper":
                    log.info(
                        "📄 PAPER: %s %.2f lots @%.5f | SL=%.5f TP=%s",
                        signal.side, vol, entry, sl, f"{tp:.5f}" if tp else "n/a"
                    )
                else:
                    ok, order_id, deal_id = send_market_order(
                        mt,
                        symbol=symbol,
                        side=signal.side,
                        volume=vol,
                        sl=sl,
                        tp=tp,
                        comment=f"{strat.name}: risk={float(getattr(settings, 'risk_percent', 1.0)):.2f}%",
                    )
                    if ok:
                        log.info(
                            "✅ LIVE: %s %.2f lots @%.5f | SL=%.5f TP=%s | order=%s deal=%s",
                            signal.side, vol, entry, sl, f"{tp:.5f}" if tp else "n/a", order_id, deal_id
                        )
                        adjust_tp_to_exact_2r(symbol, signal.side, sl)
                    else:
                        log.error("❌ LIVE: Ordre feilet.")

            time.sleep(poll_sec)

    except KeyboardInterrupt:
        log.info("Avslutter på brukerforespørsel (Ctrl+C).")
    except Exception as e:
        log.exception("Uventet feil i hovedløkke: %s", e)
        return 1
    finally:
        try:
            if hasattr(mt, "shutdown") and callable(getattr(mt, "shutdown")):
                mt.shutdown()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
