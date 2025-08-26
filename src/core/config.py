# core/config.py
from __future__ import annotations
from typing import Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import os

def _find_env_file() -> Optional[str]:
    """
    Finn .env ved å gå oppover fra denne filen til prosjektroten.
    Stopper når vi ser mappen som inneholder 'scripts' og 'src' (vanlig layout),
    eller når vi har gått 5 nivåer.
    """
    # Tillat manuell overstyring via miljøvariabel
    manual = os.getenv("EA__ENV_FILE")
    if manual and Path(manual).is_file():
        return manual

    here = Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        # heuristikk: prosjektrot har ofte /scripts og /src
        if (parent / ".env").is_file():
            return str(parent / ".env")
        if (parent.parent / ".env").is_file():
            # i tilfelle core/config.py ligger i src/core/
            return str(parent.parent / ".env")
    return None


class Settings(BaseSettings):
    # --- Multi-konto (obligatorisk) ---
    accounts_raw: Optional[str] = None
    account_key: Optional[str] = None
    account_id: Optional[int] = None
    account_password: Optional[str] = None
    account_server: Optional[str] = None

    # --- Credentials per konto (valgfritt) ---
    passwords_raw: Optional[str] = None
    servers_raw: Optional[str] = None

    # --- Generelle fallbacks ---
    symbol_fallback: str = "EURUSD"
    timeframe_fallback: str = "M15"
    mode_fallback: str = "paper"
    mt5_path: Optional[str] = None
    polling_sec: float = 5.0
    default_timeframe: Optional[str] = None

    # --- Risiko / posisjonssizing ---
    risk_percent: float = 1.0
    use_risk_sizing: bool = True

    # --- Caps / limiter ---
    max_volume: float = 0.0
    max_risk_money: float = 0.0
    max_total_risk_percent: float = 0.0
    max_positions_per_symbol: int = 3

    # --- Daily loss guard ---
    max_daily_loss_percent: float = 0.0
    max_daily_loss_money: float = 0.0

    model_config = SettingsConfigDict(
        env_prefix="EA__",
        env_file=_find_env_file() or ".env",
        extra="ignore",
    )

    # caches
    _accounts: Dict[str, int] = {}
    _passwords: Dict[str, str] = {}
    _servers: Dict[str, str] = {}

    def __init__(self, **data):
        super().__init__(**data)
        self._accounts = self._parse_int_map(self.accounts_raw)
        self._passwords = self._parse_str_map(self.passwords_raw)
        self._servers = self._parse_str_map(self.servers_raw)

    @staticmethod
    def _parse_int_map(raw: Optional[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if not raw:
            return out
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if ":" not in part:
                continue
            k, v = (s.strip() for s in part.split(":", 1))
            if k and v.isdigit():
                out[k] = int(v)
        return out

    @staticmethod
    def _parse_str_map(raw: Optional[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not raw:
            return out
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if ":" not in part:
                continue
            k, v = (s.strip() for s in part.split(":", 1))
            if k and v:
                out[k] = v
        return out

    @property
    def accounts(self) -> Dict[str, int]:
        return dict(self._accounts)

    def _find_key_for_account_id(self, acc_id: int) -> Optional[str]:
        for k, v in self._accounts.items():
            if int(v) == int(acc_id):
                return k
        return None

    def get_account_id(self, key_or_id: Optional[str | int] = None) -> int:
        if isinstance(key_or_id, int):
            return key_or_id
        if isinstance(key_or_id, str) and key_or_id:
            if key_or_id.isdigit():
                return int(key_or_id)
            if key_or_id in self._accounts:
                return self._accounts[key_or_id]
        if self.account_id:
            return int(self.account_id)
        if self.account_key and self.account_key in self._accounts:
            return self._accounts[self.account_key]
        if self._accounts:
            return next(iter(self._accounts.values()))
        raise ValueError("Ingen MT5-konto konfigurert. Sett EA__ACCOUNTS eller oppgi --account.")

    def get_login_params(self, key_or_id: Optional[str | int] = None) -> tuple[int, Optional[str], Optional[str], Optional[str]]:
        acc_id = self.get_account_id(key_or_id)
        key_used: Optional[str] = None
        if isinstance(key_or_id, str) and key_or_id and not key_or_id.isdigit():
            key_used = key_or_id if key_or_id in self._accounts else None
        if not key_used:
            key_used = self._find_key_for_account_id(acc_id)
        password = self._passwords.get(key_used) if key_used else None
        server = self._servers.get(key_used) if key_used else None
        return acc_id, key_used, password, server


# Global settings
settings = Settings()
