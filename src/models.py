"""Typed, provider-independent representations of downloaded ETF data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Holding:
    name: str
    weight: float  # Fraction of this ETF's NAV, not a percentage.
    isin: str | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class Breakdown:
    sectors: dict[str, float] = field(default_factory=dict)
    countries: dict[str, float] = field(default_factory=dict)
    holdings: tuple[Holding, ...] = ()  # Top 10 *reported* holdings.


@dataclass(frozen=True)
class ETFData:
    isin: str
    ticker: str
    name: str
    quote_price: float
    quote_currency: str
    unit_price_eur: float
    yfinance: Breakdown | None
    justetf: Breakdown | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Position:
    etf: ETFData
    units: float

    @property
    def value_eur(self) -> float:
        return self.units * self.etf.unit_price_eur


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[Position, ...]
    loaded_at: datetime

    @property
    def total_eur(self) -> float:
        return sum(position.value_eur for position in self.positions)
