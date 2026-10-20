"""All network access lives here. Fetch each ETF's information in one pass.

Uses yfinance for exchange-listed price, currency and fund details, and
justetf-scraping for countries, sectors and its reported top-ten holdings.
No explicit historical-price requests are made.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import pandas as pd

from src.models import Breakdown, ETFData, Holding


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
    if sum(holding.weight for holding in result) > 1.005:
        raise ValueError("Yahoo top holdings exceed 100%")
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
    if sum(holding.weight for holding in holdings) > 1.005:
        raise ValueError("justETF top holdings exceed 100%")
    return tuple(holdings)


def _resolve_ticker(isin: str, ticker_override: str = "") -> dict[str, Any]:
    """Use Yahoo's first suitable ETF search result, or the user's chosen ticker."""
    import yfinance as yf

    if ticker_override:
        return {"symbol": ticker_override.strip().upper()}
    quotes = yf.Search(isin, max_results=20).quotes or []
    candidates = [q for q in quotes if str(q.get("symbol") or "").strip()]
    if not candidates:
        raise LookupError(f"Yahoo Finance could not resolve an exchange ticker for {isin}")

    # No blind guess when Yahoo explicitly identifies a *different* ISIN.
    candidates = [
        q for q in candidates
        if not q.get("isin") or str(q["isin"]).upper() == isin
    ]
    candidates = [q for q in candidates if str(q.get("quoteType") or "ETF").upper() == "ETF"]
    if not candidates:
        raise LookupError(f"Yahoo returned no listing matching ISIN {isin}")
    return candidates[0]


def _verify_listing(isin: str, candidate: dict, info: dict) -> tuple[str | None, str]:
    """Check identifiers when Yahoo supplies them; a missing ISIN is not a failure."""
    if str(info.get("quoteType") or "ETF").upper() != "ETF":
        raise ValueError(f"{isin}: selected Yahoo listing is not an ETF")
    identities = [(candidate.get("isin"), "Yahoo search"), (info.get("isin"), "Yahoo quote")]
    matched = None
    source = ""
    for value, origin in identities:
        value = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
            continue
        if value != isin:
            raise ValueError(f"{isin}: Yahoo listing reports a different ISIN ({value}); choose another ticker")
        matched, source = value, origin
    return matched, source


def _spot_price(ticker: Any) -> tuple[float, str, dict[str, Any], str]:
    """Fetch current/latest quote metadata; no explicit history call."""
    info: dict[str, Any] = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        pass

    price, basis = None, ""
    for key in ("regularMarketPrice", "currentPrice", "previousClose"):
        if (price := _positive_number(info.get(key))) is not None:
            basis = key
            break
    currency = str(info.get("currency") or "").strip()

    if price is None or not currency:
        try:
            fast = ticker.fast_info
            if price is None:
                price = _positive_number(fast.get("last_price"))
                basis = "fast_info last_price"
            if not currency:
                currency = str(fast.get("currency") or "").strip()
            if not info.get("exchange"):
                info["exchange"] = fast.get("exchange")
        except Exception:
            pass

    if price is None or not currency:
        raise ValueError("Yahoo Finance did not provide a usable price and quote currency")
    return price, currency, info, basis


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


def _fund_details(overview: dict[str, Any]) -> dict[str, str]:
    """Only documented justetf-scraping fields; fund_size_eur is in EUR millions."""
    details = {}
    for key, label in (
        ("fund_domicile", "Domicile"), ("replication", "Replication"),
        ("fund_currency", "Fund currency"), ("distribution_policy", "Distribution"),
        ("index", "Index"), ("fund_provider", "Fund provider"),
    ):
        value = overview.get(key)
        if isinstance(value, str) and value.strip():
            details[label] = value.strip()
    ter = overview.get("ter")  # Percentage points: 0.19 means 0.19% per year.
    if ter is not None:
        try:
            ter = float(ter)
            if math.isfinite(ter) and ter >= 0:
                details["TER (per year)"] = f"{ter:.2f}%"
        except (TypeError, ValueError):
            pass
    size = _positive_number(overview.get("fund_size_eur"))
    if size is not None:
        details["Fund size / AUM"] = f"EUR {size:,.2f} million"
    return details


def _justetf_overview(isin: str) -> tuple[dict, tuple[str, ...], list[str]]:
    """Keep expanded lists; retry the page once if the library returns empty lists."""
    import justetf_scraping

    overview = justetf_scraping.get_etf_overview(isin, include_gettex=False, expand_allocations=True)
    if not isinstance(overview, dict) or not overview:
        raise ValueError("Empty justETF profile")
    missing = [kind for kind in ("countries", "sectors") if not overview.get(kind)]
    if not missing:
        return overview, (), []
    try:
        page = justetf_scraping.get_etf_overview(isin, include_gettex=False, expand_allocations=False)
    except Exception as exc:
        return overview, (), [f"{isin}: justETF page fallback unavailable ({exc})"]
    if not isinstance(page, dict) or not page:
        return overview, (), [f"{isin}: justETF page fallback returned no profile"]
    recovered = []
    for kind in missing:
        if page.get(kind):
            overview[kind] = page[kind]
            recovered.append(kind)
    return overview, tuple(recovered), []


def fetch_etf(isin: str, ticker_override: str = "") -> ETFData:
    """Fetch providers independently; missing prices prevent portfolio valuation only."""
    isin = isin.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        raise ValueError(f"Invalid ISIN format: {isin!r}")
    warnings: list[str] = []
    retrieved_at = {}

    def dataset(label, getter, parser, empty):
        # A bad field must not discard other usable fields from the same provider.
        try:
            value = parser(getter())
            if value:
                return value
            warnings.append(f"{isin}: {label} unavailable")
        except Exception as exc:
            warnings.append(f"{isin}: {label} unavailable ({exc})")
        return empty

    justetf: Breakdown | None = None
    justetf_name = ""
    details = {}
    holdings_date = ""
    try:
        # No Gettex socket, historical price downloads, or full constituents.
        overview, page_fallbacks, fallback_warnings = _justetf_overview(isin)
        warnings.extend(fallback_warnings)
        details = _fund_details(overview)
        holdings_date = str(overview.get("holdings_date") or "")
        justetf_name = str(overview.get("name") or "").strip()
        sectors = dataset("justETF sectors", lambda: overview.get("sectors"), _parse_justetf_allocation, {})
        countries = dataset("justETF countries", lambda: overview.get("countries"), _parse_justetf_allocation, {})
        holdings = dataset("justETF top holdings", lambda: overview.get("top_holdings"), _parse_justetf_holdings, ())
        valid_fallbacks = tuple(kind for kind, values in (("countries", countries), ("sectors", sectors))
                                if values and kind in page_fallbacks)
        justetf = Breakdown(sectors=sectors, countries=countries, holdings=holdings, page_fallbacks=valid_fallbacks)
        retrieved_at["justetf"] = datetime.now(timezone.utc)
    except Exception as exc:
        warnings.append(f"{isin}: justETF profile unavailable ({exc})")

    # Yahoo search/price failures must not discard the ISIN-based justETF response.
    symbol = ticker_override.strip().upper()
    candidate, info = {}, {}
    quote_price, price_eur, currency, quote_basis = None, None, "", ""
    listing_isin, verification_source = None, ""
    valuation_error = ""
    yahoo: Breakdown | None = None
    try:
        import yfinance as yf
        candidate = _resolve_ticker(isin, ticker_override)
        symbol = str(candidate["symbol"]).strip().upper()
        yf_ticker = yf.Ticker(symbol)
        quote_price, currency, info, quote_basis = _spot_price(yf_ticker)
        retrieved_at["quote"] = datetime.now(timezone.utc)
        listing_isin, verification_source = _verify_listing(isin, candidate, info)
        price_eur = quote_price * eur_conversion(currency)
        if currency != "EUR":
            retrieved_at["fx"] = datetime.now(timezone.utc)
        if quote_basis == "previousClose":
            warnings.append(f"{isin}: valuation uses Yahoo's previous close, not a current quote")
        try:
            fund = yf_ticker.get_funds_data()
            if fund is None:
                raise ValueError("No fund details returned")
            sectors = dataset("Yahoo sectors", lambda: fund.sector_weightings, _parse_yf_sectors, {})
            holdings = dataset("Yahoo top holdings", lambda: fund.top_holdings, _parse_yf_holdings, ())
            yahoo = Breakdown(sectors=sectors, holdings=holdings)
            retrieved_at["yfinance"] = datetime.now(timezone.utc)
        except Exception as exc:
            warnings.append(f"{isin}: Yahoo allocations unavailable ({exc})")
    except Exception as exc:
        price_eur = None
        valuation_error = f"{isin}: Yahoo price or EUR conversion unavailable ({exc})"
        warnings.append(valuation_error)

    bid, ask = _positive_number(info.get("bid")), _positive_number(info.get("ask"))
    spread = (ask - bid) / ((ask + bid) / 2) if bid and ask and ask >= bid else None
    market_time = _positive_number(info.get("regularMarketTime"))
    quote_as_of = (
        datetime.fromtimestamp(market_time, timezone.utc)
        if market_time and quote_basis in ("regularMarketPrice", "currentPrice") else None
    )
    return ETFData(
        isin=isin,
        ticker=symbol,
        name=justetf_name or str(info.get("longName") or info.get("shortName") or symbol),
        quote_price=quote_price,
        quote_currency=currency,
        unit_price_eur=price_eur,
        yfinance=yahoo,
        justetf=justetf,
        warnings=tuple(warnings),
        exchange=str(info.get("fullExchangeName") or info.get("exchange") or candidate.get("exchange") or ""),
        listing_isin=listing_isin,
        listing_verified=listing_isin == isin,
        verification_source=verification_source,
        retrieved_at=retrieved_at,
        quote_as_of=quote_as_of,
        quote_basis=quote_basis,
        fund_details=details,
        spread_fraction=spread,
        holdings_date=holdings_date,
        valuation_error=valuation_error,
    )
