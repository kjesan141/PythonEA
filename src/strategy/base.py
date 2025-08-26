# src/strategy/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


@dataclass
class Signal:
    """
    Representerer et handelssignal ved bar-close.
    side:  "buy" | "sell" | None
    price: typisk close-prisen for signalbaren (valgfritt, men nyttig for logging)
    meta:  valgfri bærepose for ekstra data (SL/TP-beregning, debug, osv.)
    """
    side: Optional[str]  # "buy" | "sell" | None
    price: Optional[float] = None
    meta: dict[str, Any] | None = None


class StrategyBase(ABC):
    """
    Abstrakt base for alle strategier.

    Kontrakt:
      - on_bar(df) kalles ved bar-close med siste OHLCV DataFrame.
      - returner et Signal-objekt (side="buy"/"sell" eller None).
      - df skal minst ha kolonner: ["time","open","high","low","close","tick_volume"].

    Valgfri konvensjon:
      - default_symbol: hvis strategien 'hører til' et symbol, kan du sette denne.
    """

    # Valgfri: strategi-spesifikk default symbol (brukes hvis CLI og .env ikke gir noe)
    default_symbol: Optional[str] = None

    # Valgfri: strategi-navn for logging/identifikasjon (defaults til klassenavn)
    @property
    def name(self) -> str:
        return type(self).__name__

    # Valgfri: kall når strategien startes (før første bar)
    def on_start(self) -> None:
        """Hook: kjøres én gang før første on_bar. Bruk for init/state."""
        return None

    # Valgfri: kall når strategien stoppes (f.eks. ved CTRL+C)
    def on_stop(self) -> None:
        """Hook: kjøres ved stopp. Rydd opp state hvis nødvendig."""
        return None

    @abstractmethod
    def on_bar(self, df: pd.DataFrame) -> Signal:
        """
        Kalles ved hver ny bar-close.
        Returner Signal("buy"/"sell") for ordre, eller Signal(None) for ingen handling.
        """
        ...
