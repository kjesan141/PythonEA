# src/broker/mt5_client.py
from __future__ import annotations

import logging
from typing import Optional, Tuple

import MetaTrader5 as mt5
import pandas as pd

log = logging.getLogger(__name__)


class MT5Client:
    """
    Tynn adapter rundt MetaTrader5-pakken:
      - initialize/login (smartere connect)
      - sikre symbol
      - hente bars til DataFrame
      - sende market-ordre
      - lukke posisjoner
    """

    def __init__(self) -> None:
        self._connected: bool = False
        self._account_id: Optional[int] = None

    # -----------------------------
    # Tilkobling / init / login
    # -----------------------------
    def connect(
        self,
        account_id: int,
        mt5_path: Optional[str] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
    ) -> bool:
        """
        Smarter connect:
        - Initialiserer terminal (bruker konkret EXE hvis gitt).
        - Hvis terminal allerede er innlogget på ønsket konto -> bruk den uten nytt login-kall.
        - Hvis terminal er innlogget på annen konto:
            - Har vi password+server -> forsøk login-bytte.
            - Ellers: forklar hva som mangler.
        """
        try:
            mt5.shutdown()
        except Exception:
            pass

        # Normaliser path: hvis mappe er oppgitt, prøv å finne terminal64.exe
        path_used = mt5_path
        if path_used:
            import os
            if os.path.isdir(path_used):
                cand = os.path.join(path_used, "terminal64.exe")
                if os.path.isfile(cand):
                    path_used = cand

        if path_used:
            log.info("MT5 initialize med path: %s", path_used)
            ok = mt5.initialize(path=path_used)
            if not ok or mt5.terminal_info() is None:
                log.error("MT5 initialize feilet med path. Prøver uten path. Feil: %s", mt5.last_error())
                mt5.shutdown()
                ok = mt5.initialize()
        else:
            ok = mt5.initialize()

        if not ok or mt5.terminal_info() is None:
            log.error("MT5 initialize failed: %s", mt5.last_error())
            return False

        tinfo = mt5.terminal_info()
        log.info("Terminal info | name=%s | company=%s | path=%s",
                 getattr(tinfo, "name", None), getattr(tinfo, "company", None), getattr(tinfo, "path", None))

        # Dersom terminalen allerede er innlogget
        current = mt5.account_info()
        if current:
            log.info("Terminal er innlogget | login=%s | server=%s | name=%s",
                     current.login, current.server, current.name)
            if int(current.login) == int(account_id):
                self._connected = True
                self._account_id = account_id
                log.info("Bruker eksisterende innlogging (ingen login-kall nødvendig).")
                return True
            else:
                log.warning("Terminal er innlogget på annen konto (%s) enn ønsket (%s).",
                            current.login, account_id)
                if password and server:
                    ok = mt5.login(login=account_id, password=password, server=server)
                    if not ok:
                        log.error("MT5 login (bytte konto) feilet: %s", mt5.last_error())
                        return False
                else:
                    log.error(
                        "Kan ikke bytte til ønsket konto uten credentials. "
                        "Løsning: 1) logg inn manuelt i denne terminalen på konto %s (server=%s), "
                        "eller 2) legg inn EA__ACCOUNT_PASSWORD og EA__ACCOUNT_SERVER i .env.",
                        account_id, server or "ukjent"
                    )
                    return False
        else:
            log.info("Terminalen er ikke innlogget ennå.")

        # Ingen aktiv konto, eller vi må logge inn eksplisitt:
        if password and server:
            ok = mt5.login(login=account_id, password=password, server=server)
        else:
            ok = mt5.login(login=account_id)

        if not ok:
            log.error("MT5 login failed: %s", mt5.last_error())
            return False

        acc = mt5.account_info()
        if not acc or int(acc.login) != int(account_id):
            log.error("Innlogget på uventet konto. got=%s expected=%s", getattr(acc, "login", None), account_id)
            return False

        self._connected = True
        self._account_id = account_id
        log.info("MT5 connected | account=%s | name=%s | server=%s", acc.login, acc.name, acc.server)
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    def shutdown(self) -> None:
        try:
            mt5.shutdown()
        finally:
            self._connected = False
            self._account_id = None
            log.info("MT5 shutdown complete")

    # -----------------------------
    # Symbols / markedsdata
    # -----------------------------
    def ensure_symbol(self, symbol: str) -> bool:
        if not symbol:
            return False
        if mt5.symbol_info(symbol) is None:
            log.error("Symbol not found: %s", symbol)
            return False
        if not mt5.symbol_select(symbol, True):
            log.error("Failed to select symbol: %s | %s", symbol, mt5.last_error())
            return False
        return True

    def rates_df(self, symbol: str, timeframe: int, count: int = 300) -> pd.DataFrame:
        if not self.ensure_symbol(symbol):
            return pd.DataFrame()
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            log.error("copy_rates_from_pos returned None for %s | %s", symbol, mt5.last_error())
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close", "tick_volume"]]

    # -----------------------------
    # Ordre / posisjoner
    # -----------------------------
    def market_order(
        self,
        symbol: str,
        side: str,               # "buy" | "sell"
        volume: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        deviation: int = 10,
        comment: str = "",
    ) -> Tuple[bool, Optional[int], Optional[int]]:
        assert side in ("buy", "sell"), "side must be 'buy' or 'sell'"
        if not self.ensure_symbol(symbol):
            return False, None, None

        type_map = {"buy": mt5.ORDER_TYPE_BUY, "sell": mt5.ORDER_TYPE_SELL}
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "type": type_map[side],
            "volume": float(volume),
            "deviation": int(deviation),
            "type_filling": mt5.ORDER_FILLING_IOC,
            "comment": comment,
        }
        if sl is not None:
            req["sl"] = float(sl)
        if tp is not None:
            req["tp"] = float(tp)

        res = mt5.order_send(req)
        ok = bool(res) and res.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log.error("order_send failed | symbol=%s side=%s vol=%s | retcode=%s | %s",
                      symbol, side, volume, getattr(res, "retcode", None), mt5.last_error())
            return False, getattr(res, "order", None), getattr(res, "retcode", None)

        log.info("order_send OK | symbol=%s side=%s vol=%s | order_id=%s price=%.5f",
                 symbol, side, volume, res.order, getattr(res, "price", float("nan")))
        return True, res.order, res.retcode

    def close_position_by_ticket(
        self,
        ticket: int,
        deviation: int = 10,
        comment: str = "close_position",
    ) -> Tuple[bool, Optional[int], Optional[int]]:
        pos = next((p for p in mt5.positions_get() or [] if p.ticket == ticket), None)
        if not pos:
            log.error("Position not found for ticket=%s", ticket)
            return False, None, None
        side = "sell" if pos.type == mt5.ORDER_TYPE_BUY else "buy"
        return self.market_order(symbol=pos.symbol, side=side, volume=pos.volume, deviation=deviation, comment=comment)

    # -----------------------------
    # Nyttige helpers
    # -----------------------------
    def equity(self) -> Optional[float]:
        acc = mt5.account_info()
        return float(acc.equity) if acc else None
