"""All network access lives here. Fetch each ETF's information in one pass.

Uses yfinance for exchange-listed price, currency and fund details, and
justetf-scraping for countries, sectors and its reported top-ten holdings.
No explicit historical-price requests are made.
"""
from __future__ import annotations

from functools import lru_cache
import math
import re
from typing import Any

import pandas as pd

from src.models import Breakdown, ETFData, Holding


# ISIN search can return several listings. Prefer an EUR listing when possible.
_EUR_EXCHANGES = (".MI", ".DE", ".PA", ".AS", ".BR", ".MC", ".VI")
_ALL_EXCHANGES = _EUR_EXCHANGES + (".L", ".SW", ".S", ".ST")


def _positive_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _fraction(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1.0000001:
        raise ValueError(f"Invalid fractional allocation: {value!r}")
    return number


def _parse_yf_sectors(raw: Any) -> dict[str, float]:
    if not raw:
        return {}
    result: dict[str, float] = {}
    for key, value in dict(raw).items():
        label = str(key).strip()
        if label:
            result[label] = result.get(label, 0.0) + _fraction(value)
    total = sum(result.values())
    if total > 1.005:
        raise ValueError(f"Yahoo sector allocations sum to {total:.2%}, above 100%")
    if result and total < 0.995:
        result["Unreported"] = result.get("Unreported", 0.0) + (1 - total)
    return result


def _parse_yf_holdings(raw: pd.DataFrame | None) -> tuple[Holding, ...]:
    if raw is None or raw.empty:
        return ()
    if "Holding Percent" not in raw.columns:
        raise ValueError("Yahoo holdings have no 'Holding Percent' column")
    result = []
    for symbol, row in raw.head(10).iterrows():
        weight = _fraction(row["Holding Percent"])
        if weight <= 0:
            continue
        name = row.get("Name")
        label = str(name).strip() if pd.notna(name) and str(name).strip() else str(symbol)
        isin = row.get("ISIN", row.get("Isin", None))
        result.append(Holding(
            name=label,
            weight=weight,
            isin=str(isin).strip().upper() if pd.notna(isin) and str(isin).strip() else None,
            symbol=str(symbol).strip(),
        ))
    return tuple(result)


def _parse_justetf_allocation(items: Any) -> dict[str, float]:
    if not items:
        return {}
    result: dict[str, float] = {}
    for item in items:
        label = str(item["name"]).strip()
        if not label:
            raise ValueError("Blank allocation category from justETF")
        weight = _fraction(float(item["percentage"]) / 100)
        result[label] = result.get(label, 0.0) + weight
    total = sum(result.values())
    if total > 1.005:
        raise ValueError(f"justETF allocations sum to {total:.2%}, above 100%")
    if total < 0.995:
        result["Unreported"] = result.get("Unreported", 0.0) + (1 - total)
    return result


def _parse_justetf_holdings(items: Any) -> tuple[Holding, ...]:
    if not items:
        return ()
    holdings = []
    for item in items[:10]:
        label = str(item["name"]).strip()
        if not label:
            raise ValueError("Blank justETF holding name")
        weight = _fraction(float(item["percentage"]) / 100)
        isin = item.get("isin")
        holdings.append(Holding(
            name=label,
            weight=weight,
            isin=str(isin).strip().upper() if isin and str(isin).strip() else None,
        ))
    return tuple(holdings)


def _resolve_ticker(isin: str) -> str:
    """Best-effort search; always show resolved symbol in UI for verification."""
    import yfinance as yf

    quotes = yf.Search(isin, max_results=20).quotes or []
    candidates = [q for q in quotes if str(q.get("symbol") or "").strip()]
    if not candidates:
        raise LookupError(f"Yahoo Finance could not resolve an exchange ticker for {isin}")

    def priority(quote: dict[str, Any]) -> tuple[int, int, int, int]:
        symbol = str(quote["symbol"]).upper()
        explicit_isin = str(quote.get("isin") or "").upper()
        return (
            int(explicit_isin == isin),
            int(str(quote.get("quoteType") or "").upper() == "ETF"),
            int(str(quote.get("currency") or "").upper() == "EUR"),
            int(symbol.endswith(_EUR_EXCHANGES)),
        )

    # No blind guess when Yahoo explicitly identifies a *different* ISIN.
    candidates = [
        q for q in candidates
        if not q.get("isin") or str(q["isin"]).upper() == isin
    ]
    if not candidates:
        raise LookupError(f"Yahoo returned no listing matching ISIN {isin}")
    candidates.sort(key=priority, reverse=True)
    return str(candidates[0]["symbol"]).strip()


def _spot_price(ticker: Any) -> tuple[float, str, str]:
    """Fetch current/latest quote metadata; no explicit history call."""
    info: dict[str, Any] = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        pass

    price = next((v for v in (
        _positive_number(info.get("regularMarketPrice")),
        _positive_number(info.get("currentPrice")),
        _positive_number(info.get("previousClose")),
    ) if v is not None), None)
    currency = str(info.get("currency") or "").strip()
    name = str(info.get("longName") or info.get("shortName") or "").strip()

    if price is None or not currency:
        try:
            fast = ticker.fast_info
            if price is None:
                price = _positive_number(fast.get("last_price"))
            if not currency:
                currency = str(fast.get("currency") or "").strip()
        except Exception:
            pass

    if price is None or not currency:
        raise ValueError("Yahoo Finance did not provide a usable price and quote currency")
    return price, currency, name


@lru_cache(maxsize=32)
def eur_conversion(currency: str) -> float:
    """Multiply a quote by this factor to get EUR (GBp/GBX are pence)."""
    import yfinance as yf

    code = currency.strip()
    if code == "EUR":
        return 1.0
    multiplier = 0.01 if code in ("GBp", "GBX") else 1.0
    fx_currency = "GBP" if code in ("GBp", "GBX") else code.upper()
    ticker = yf.Ticker(f"{fx_currency}EUR=X")
    # FX metadata may omit its own currency; only the numeric rate is needed.
    info = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        pass
    price = _positive_number(info.get("regularMarketPrice")) or _positive_number(info.get("previousClose"))
    if price is None:
        try:
            price = _positive_number(ticker.fast_info.get("last_price"))
        except Exception:
            pass
    if price is None or not math.isfinite(price) or price <= 0:
        raise ValueError(f"Invalid conversion rate {fx_currency}/EUR")
    return multiplier * price


def fetch_etf(isin: str) -> ETFData:
    """Download the small supported data set exactly once per provider/ETF.

    Failure to obtain a quote is fatal: portfolio weights need valid EUR prices.
    Sector/holding provider errors are nonfatal and surfaced as warnings.
    """
    import yfinance as yf
    import justetf_scraping

    isin = isin.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        raise ValueError(f"Invalid ISIN format: {isin!r}")

    symbol = _resolve_ticker(isin)
    yf_ticker = yf.Ticker(symbol)
    quote_price, currency, yahoo_name = _spot_price(yf_ticker)
    price_eur = quote_price * eur_conversion(currency)
    warnings: list[str] = []

    yahoo: Breakdown | None = None
    try:
        # Call get_funds_data only once: reuse its sector and holdings fields.
        fund = yf_ticker.get_funds_data()
        if fund is None:
            raise ValueError("No fund details returned")
        sectors = _parse_yf_sectors(getattr(fund, "sector_weightings", None))
        holdings = _parse_yf_holdings(getattr(fund, "top_holdings", None))
        if sectors or holdings:
            yahoo = Breakdown(sectors=sectors, holdings=holdings)
        if not sectors:
            warnings.append(f"{isin}: yfinance sector allocation unavailable")
        if not holdings:
            warnings.append(f"{isin}: yfinance top holdings unavailable")
    except Exception as exc:
        warnings.append(f"{isin}: yfinance fund data unavailable ({exc})")

    justetf: Breakdown | None = None
    justetf_name = ""
    try:
        # No Gettex socket, historical price downloads, or full constituents.
        overview = justetf_scraping.get_etf_overview(
            isin, include_gettex=False, expand_allocations=True
        )
        if not isinstance(overview, dict) or not overview:
            raise ValueError("Empty justETF profile")
        justetf_name = str(overview.get("name") or "").strip()
        sectors = _parse_justetf_allocation(overview.get("sectors"))
        countries = _parse_justetf_allocation(overview.get("countries"))
        holdings = _parse_justetf_holdings(overview.get("top_holdings"))
        if sectors or countries or holdings:
            justetf = Breakdown(sectors=sectors, countries=countries, holdings=holdings)
        for key, allocation in (("sectors", sectors), ("countries", countries)):
            if not allocation:
                warnings.append(f"{isin}: justETF {key} unavailable")
        if not holdings:
            warnings.append(f"{isin}: justETF top holdings unavailable")
    except Exception as exc:
        warnings.append(f"{isin}: justETF profile unavailable ({exc})")

    return ETFData(
        isin=isin,
        ticker=symbol,
        name=justetf_name or yahoo_name or symbol,
        quote_price=quote_price,
        quote_currency=currency,
        unit_price_eur=price_eur,
        yfinance=yahoo,
        justetf=justetf,
        warnings=tuple(warnings),
    )
