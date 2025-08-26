from __future__ import annotations

from typing import Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Multi-konto (obligatorisk) ---
    accounts_raw: Optional[str] = None         # EA__ACCOUNTS: "MAIN:123,TEST:456"
    account_key: Optional[str] = None          # EA__ACCOUNT_KEY: "MAIN"
    account_id: Optional[int] = None           # EA__ACCOUNT_ID: 123
    account_password: Optional[str] = None     # EA__ACCOUNT_PASSWORD
    account_server: Optional[str] = None       # EA__ACCOUNT_SERVER

    # --- Credentials per konto (valgfritt) ---
    passwords_raw: Optional[str] = None        # EA__PASSWORDS: "MAIN:pass,TEST:pass2"
    servers_raw: Optional[str] = None          # EA__SERVERS: "MAIN:ICMarketsSC-Demo,TEST:MetaQuotes-Demo"

    # --- Risiko / posisjonssizing ---
    risk_percent: float = 1.0        # EA__RISK_PERCENT (prosent, f.eks 1.0 for 1%)
    use_risk_sizing: bool = True     # EA__USE_RISK_SIZING (true/false)
    max_volume: float = 0.0        # 0.0 = ikke brukt
    max_risk_money: float = 0.0    # 0.0 = ikke brukt


# --- Tidsstyring ---
    default_timeframe: str = "M15"   # EA__DEFAULT_TIMEFRAME (brukes i run_live.py)
    polling_sec: float = 5.0         # EA__POLLING_SEC (sekunder mellom polling)

    # --- Fallbacks ---
    symbol_fallback: str = "EURUSD"            # EA__SYMBOL_FALLBACK
    timeframe_fallback: str = "M15"            # EA__TIMEFRAME_FALLBACK
    mode_fallback: str = "paper"               # EA__MODE_FALLBACK

    # --- MT5 terminal (kan være None) ---
    mt5_path: Optional[str] = None             # EA__MT5_PATH

    model_config = SettingsConfigDict(
        env_prefix="EA__",
        env_file=".env",
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
        # 1) direkte input
        if isinstance(key_or_id, int):
            return key_or_id
        if isinstance(key_or_id, str) and key_or_id:
            if key_or_id.isdigit():
                return int(key_or_id)
            if key_or_id in self._accounts:
                return self._accounts[key_or_id]
        # 2) eksplisitt ID i .env
        if self.account_id:
            return int(self.account_id)
        # 3) nøkkel i .env
        if self.account_key and self.account_key in self._accounts:
            return self._accounts[self.account_key]
        # 4) fallback: første i lista
        if self._accounts:
            return next(iter(self._accounts.values()))
        raise ValueError("Ingen MT5-konto konfigurert. Sett EA__ACCOUNTS eller oppgi --account.")

    def get_login_params(self, key_or_id: Optional[str | int] = None) -> tuple[int, Optional[str], Optional[str], Optional[str]]:
        """
        Returnerer (account_id, key_used, password, server).
        key_used kan være None hvis oppslaget ble gjort kun på ID.
        """
        acc_id = self.get_account_id(key_or_id)
        # Finn nøkkelnavn for å slå opp passord/server
        key_used: Optional[str] = None
        if isinstance(key_or_id, str) and key_or_id and not key_or_id.isdigit():
            # brukte en navngitt konto
            key_used = key_or_id if key_or_id in self._accounts else None
        if not key_used:
            key_used = self._find_key_for_account_id(acc_id)

        password = self._passwords.get(key_used) if key_used else None
        server = self._servers.get(key_used) if key_used else None
        return acc_id, key_used, password, server
        

# Global settings
settings = Settings()
