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
    quote_price: float | None
    quote_currency: str
    unit_price_eur: float | None
    yfinance: Breakdown | None
    justetf: Breakdown | None
    warnings: tuple[str, ...] = ()
    exchange: str = ""
    listing_isin: str | None = None
    listing_verified: bool = False
    verification_source: str = ""
    retrieved_at: dict[str, datetime] = field(default_factory=dict)
    quote_as_of: datetime | None = None
    quote_basis: str = ""
    fund_details: dict[str, str] = field(default_factory=dict)  # justETF, with units.
    spread_fraction: float | None = None  # Yahoo (ask - bid) / midpoint.
    holdings_date: str = ""  # justETF's reported date, when supplied.
    valuation_error: str = ""


@dataclass(frozen=True)
class Position:
    etf: ETFData
    units: float

    @property
    def value_eur(self) -> float:
        if self.etf.unit_price_eur is None:
            raise ValueError(f"{self.etf.isin}: EUR price is unavailable")
        return self.units * self.etf.unit_price_eur


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[Position, ...]
    loaded_at: datetime

    @property
    def total_eur(self) -> float:
        return sum(position.value_eur for position in self.positions)
