"""CSV portfolio validation and pure, market-value-weighted analytics."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import math
import re
from typing import Callable, Literal

import pandas as pd

from src.models import Breakdown, ETFData, PortfolioSnapshot, Position

Source = Literal["yfinance", "justetf"]
Kind = Literal["sectors", "countries", "holdings"]


def validate_portfolio(data: pd.DataFrame) -> pd.DataFrame:
    """ISIN + number of ETF units. Duplicated ISINs are consolidated."""
    if not {"isin", "position"}.issubset(data.columns):
        raise ValueError("CSV must have columns: isin,position")
    rows = data[["isin", "position"]].copy().dropna(how="all")
    if rows.empty:
        raise ValueError("Enter at least one ETF before loading data")
    if rows.isna().any().any():
        raise ValueError("Each portfolio row must have an ISIN and a position")

    rows["isin"] = rows["isin"].astype(str).str.strip().str.upper()
    invalid = ~rows["isin"].str.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
    if invalid.any():
        raise ValueError(f"Invalid ISIN format: {rows.loc[invalid, 'isin'].tolist()}")
    rows["position"] = pd.to_numeric(rows["position"], errors="raise")
    if not rows["position"].map(lambda v: math.isfinite(v) and v > 0).all():
        raise ValueError("Positions must be positive finite numbers of ETF units")
    return rows.groupby("isin", as_index=False, sort=False)["position"].sum()


def load_portfolio_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame({"isin": pd.Series(dtype="str"), "position": pd.Series(dtype="float")})
    return validate_portfolio(pd.read_csv(path, dtype={"isin": str}))

def build_portfolio(
    data: pd.DataFrame,
    loader: Callable[[str], ETFData],
) -> PortfolioSnapshot:
    """Download each unique ISIN once; save fetched datasets in a snapshot."""
    clean = validate_portfolio(data)
    positions = tuple(
        Position(etf=loader(str(row.isin)), units=float(row.position))
        for row in clean.itertuples(index=False)
    )
    for requested_isin, position in zip(clean["isin"], positions):
        etf = position.etf
        if etf.isin != requested_isin or (etf.listing_isin and etf.listing_isin != requested_isin):
            raise ValueError("Loaded ETF does not match the requested portfolio")
        if etf.valuation_error:
            raise ValueError(etf.valuation_error)
        if not all(value is not None and math.isfinite(value) and value > 0 for value in (
            etf.quote_price, etf.unit_price_eur,
        )) or not etf.quote_currency:
            raise ValueError(f"{etf.isin}: missing or invalid valuation; portfolio totals are unavailable")
    snapshot = PortfolioSnapshot(positions=positions, loaded_at=datetime.now(timezone.utc))
    if not math.isfinite(snapshot.total_eur) or snapshot.total_eur <= 0:
        raise ValueError("Portfolio must have a positive finite EUR market value")
    return snapshot


def _breakdown(position: Position, source: Source) -> Breakdown | None:
    return getattr(position.etf, source)


def source_coverage(snapshot: PortfolioSnapshot, kind: Kind, source: Source) -> float:
    """Fraction of market value belonging to ETFs with this dataset available."""
    total = snapshot.total_eur
    covered = sum(
        position.value_eur
        for position in snapshot.positions
        if (breakdown := _breakdown(position, source)) is not None
        and bool(getattr(breakdown, kind))
    )
    return covered / total


def reported_coverage(snapshot: PortfolioSnapshot, kind: Kind, source: Source) -> float:
    """Reported allocations as a fraction of the full portfolio, excluding unknowns."""
    reported = 0.0
    for position in snapshot.positions:
        breakdown = _breakdown(position, source)
        if breakdown is None:
            continue
        if kind == "holdings":
            fraction = sum(holding.weight for holding in breakdown.holdings)
        else:
            fraction = sum(value for label, value in getattr(breakdown, kind).items()
                           if label.casefold() != "unreported")
        reported += position.value_eur / snapshot.total_eur * min(fraction, 1.0)
    return reported


# Canonical display labels. Some near-equivalences are only approximate.
# Provider-specific labels (Business/Consumer Services, Other) remain separate.
_SECTOR_ALIASES = {
    "technology": "Technology",
    "information technology": "Technology",
    "financial services": "Financials",
    "finance": "Financials",
    "financials": "Financials",
    "industrials": "Industrials",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "consumer cyclical": "Consumer Cyclicals",
    "consumer cyclicals": "Consumer Cyclicals",
    "consumer discretionary": "Consumer Cyclicals",
    "consumer defensive": "Consumer Non-Cyclicals",
    "consumer non cyclicals": "Consumer Non-Cyclicals",
    "consumer staples": "Consumer Non-Cyclicals",
    "basic materials": "Materials",
    "non energy materials": "Materials",
    "materials": "Materials",
    "energy": "Energy",
    "utilities": "Utilities",
    "communication services": "Communication Services",
    "telecommunication": "Communication Services",
    "telecommunications": "Communication Services",
    "real estate": "Real Estate",
    "realestate": "Real Estate",
}


def normalize_sector(value: str) -> str:
    key = re.sub(r"\s+", " ", str(value).replace("_", " ").replace("-", " ").strip()).casefold()
    return _SECTOR_ALIASES.get(key, key.title())


def normalize_holding_name(name: str) -> str:
    """Conservative name matching: preserve class A/B and other distinctions."""
    key = re.sub(r"[^a-z0-9]+", " ", str(name).casefold()).strip()
    # Strip legal suffixes only at end, not share class identifiers.
    return re.sub(r"(?:\s+(?:inc|corp|corporation|ltd|limited|plc))+$", "", key).strip()


def etf_exposure(etf: ETFData, kind: Kind, source: Source) -> pd.DataFrame:
    """One ETF's original reported weights, usable without a market price."""
    breakdown = getattr(etf, source)
    if breakdown is None:
        return pd.DataFrame(columns=["category", "weight"])
    values = (
        [(holding.name, holding.weight) for holding in breakdown.holdings]
        if kind == "holdings" else list(getattr(breakdown, kind).items())
    )
    return pd.DataFrame(values, columns=["category", "weight"])


def exposure(
    snapshot: PortfolioSnapshot,
    kind: Kind,
    source: Source = "justetf",
    *,
    aggregate: bool = True,
    top_n: int | None = None,
    original_sectors: bool = False,
) -> pd.DataFrame:
    """Return 'category,weight' with weights relative to the *full* portfolio.

    For holdings, only each ETF's reported top ten are used; missing names
    are not treated as known zero exposure. aggregate=False separates ETFs.
    """
    if kind == "countries" and source != "justetf":
        raise ValueError("Geographic exposure is available from justETF only")
    if top_n is not None and top_n < 1:
        raise ValueError("top_n must be positive")

    weights: dict[str, float] = defaultdict(float)
    display: dict[str, str] = {}
    # Retain securities with distinct ISINs, even when names coincide.
    for position in snapshot.positions:
        breakdown = _breakdown(position, source)
        if breakdown is None:
            continue
        factor = position.value_eur / snapshot.total_eur
        if kind == "holdings":
            for holding in breakdown.holdings:
                # Within a provider, ISIN is a stronger identifier than name.
                base_key = (
                    f"isin:{holding.isin.upper()}" if holding.isin
                    else f"name:{normalize_holding_name(holding.name)}"
                )
                key = base_key if aggregate else f"{position.etf.isin}:{base_key}"
                weights[key] += factor * holding.weight
                display.setdefault(
                    key,
                    holding.name if aggregate else f"{holding.name} · {position.etf.ticker}",
                )
        else:
            for category, fraction in getattr(breakdown, kind).items():
                label = normalize_sector(category) if kind == "sectors" and not original_sectors else category
                key = label if aggregate else f"{position.etf.isin}:{label}"
                weights[key] += factor * fraction
                display.setdefault(
                    key,
                    label if aggregate else f"{label} · {position.etf.ticker}",
                )

    if not weights:
        return pd.DataFrame(columns=["category", "weight"])

    # Do not merge distinct ISINs simply because their visible names match.
    if kind == "holdings":
        duplicate_labels = defaultdict(list)
        for key, label in display.items():
            duplicate_labels[normalize_holding_name(label)].append(key)
        for identifiers in duplicate_labels.values():
            if len(identifiers) > 1:
                for key in identifiers:
                    suffix = key.split(":")[-1][-6:]
                    display[key] = f"{display[key]} [{suffix}]"

    result = pd.DataFrame([
        {"category": display[key], "weight": weight}
        for key, weight in weights.items()
    ]).sort_values("weight", ascending=False, ignore_index=True)
    return result.head(top_n).reset_index(drop=True) if top_n else result
