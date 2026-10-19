from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace

import pandas as pd
import pytest

from src.models import Breakdown, ETFData, Holding, PortfolioSnapshot, Position
from src.portfolio import (
    build_portfolio, exposure, normalize_sector, reported_coverage, source_coverage, validate_portfolio,
)
from src.plots import exposure_chart
from src.external_services.data_fetch import (
    _parse_justetf_allocation, _parse_justetf_holdings,
    _parse_yf_holdings, _parse_yf_sectors,
)


ISIN_A = "IE00B3YLTY66"
ISIN_B = "IE000KCKFHE8"


def sample_snapshot() -> PortfolioSnapshot:
    a = ETFData(
        isin=ISIN_A, ticker="A.MI", name="Fund A",
        quote_price=100.0, quote_currency="EUR", unit_price_eur=100,
        listing_verified=True,
        yfinance=Breakdown(
            sectors={"technology": 0.6, "financial_services": 0.4},
            holdings=(Holding("Apple Inc.", 0.1, symbol="AAPL"),),
        ),
        justetf=Breakdown(
            sectors={"Technology": 0.5, "Finance": 0.5},
            countries={"United States": 0.9, "Unreported": 0.1},
            holdings=(Holding("APPLE INC", 0.08, isin="US0378331005"),),
        ),
    )
    b = ETFData(
        isin=ISIN_B, ticker="B.MI", name="Fund B",
        quote_price=200.0, quote_currency="EUR", unit_price_eur=200,
        listing_verified=True,
        yfinance=Breakdown(
            sectors={"technology": 0.2, "healthcare": 0.8},
            holdings=(Holding("Apple Inc.", 0.05, symbol="AAPL"),),
        ),
        justetf=Breakdown(
            sectors={"Technology": 0.4, "Healthcare": 0.6},
            countries={"United States": 0.7, "Germany": 0.3},
            holdings=(Holding("Apple Inc", 0.06, isin="US0378331005"),),
        ),
    )
    return PortfolioSnapshot((Position(a, 2), Position(b, 4)), datetime.now(timezone.utc))


def test_csv_validation_and_duplicate_isins():
    data = pd.DataFrame({"isin": [ISIN_A.lower(), ISIN_A, ISIN_B], "position": [2, 3, 4]})
    result = validate_portfolio(data)
    assert result.to_dict("records") == [
        {"isin": ISIN_A, "position": 5}, {"isin": ISIN_B, "position": 4}
    ]


@pytest.mark.parametrize("position", [-1, 0, float("nan")])
def test_invalid_positions(position):
    with pytest.raises(ValueError):
        validate_portfolio(pd.DataFrame({"isin": [ISIN_A], "position": [position]}))


def test_build_loads_each_unique_etf_once():
    calls = []
    example = sample_snapshot().positions[0].etf
    data = pd.DataFrame({"isin": [ISIN_A, ISIN_A], "position": [2.0, 3.0]})

    def fake_load(isin):
        calls.append(isin)
        return example

    loaded = build_portfolio(data, loader=fake_load)
    assert calls == [ISIN_A]
    assert loaded.positions[0].units == 5
    assert loaded.total_eur == 500


def test_weighted_sector_aggregation_not_simple_average():
    snap = sample_snapshot()  # A is 20%, B is 80% of market value.
    assert snap.total_eur == 1000
    result = exposure(snap, "sectors", "yfinance").set_index("category")["weight"]
    assert result["Technology"] == pytest.approx(0.2 * 0.6 + 0.8 * 0.2)
    assert result["Healthcare"] == pytest.approx(0.8 * 0.8)
    assert sum(result) == pytest.approx(1.0)


def test_justetf_countries_and_coverage():
    snap = sample_snapshot()
    frame = exposure(snap, "countries", "justetf").set_index("category")
    assert frame.loc["United States", "weight"] == pytest.approx(0.2 * .9 + 0.8 * .7)
    assert source_coverage(snap, "countries", "justetf") == pytest.approx(1)
    with pytest.raises(ValueError):
        exposure(snap, "countries", "yfinance")


def test_partial_coverage_does_not_renormalize():
    snap = sample_snapshot()
    a, b = snap.positions
    absent = ETFData(
        isin=b.etf.isin, ticker=b.etf.ticker, name=b.etf.name,
        quote_price=b.etf.quote_price, quote_currency="EUR", unit_price_eur=b.etf.unit_price_eur,
        yfinance=None, justetf=None,
    )
    snap = PortfolioSnapshot((a, Position(absent, b.units)), snap.loaded_at)
    assert source_coverage(snap, "sectors", "yfinance") == pytest.approx(0.2)
    frame = exposure(snap, "sectors", "yfinance")
    assert frame["weight"].sum() == pytest.approx(0.2)


def test_holdings_aggregate_and_nonaggregate():
    snap = sample_snapshot()
    total = exposure(snap, "holdings", "yfinance")
    assert len(total) == 1
    assert total.iloc[0]["weight"] == pytest.approx(0.2 * 0.1 + 0.8 * 0.05)
    individual = exposure(snap, "holdings", "yfinance", aggregate=False)
    assert len(individual) == 2


def test_overlap_and_name_matching():
    snap = sample_snapshot()
    frames = {
        source: exposure(snap, "holdings", source)
        for source in ("yfinance", "justetf")
    }
    fig = exposure_chart(frames, kind="holdings", title="test")
    assert fig.layout.barmode == "overlay"
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == list(fig.data[1].y)
    assert len(fig.data[0].y) == 1  # "Apple Inc." matched "APPLE INC"
    assert fig.data[0].width < 1
    fig2 = exposure_chart(frames, kind="holdings", title="test", max_categories=1)
    assert len(fig2.data[0].x) == 1


def test_source_parser_fraction_contracts():
    assert _parse_yf_sectors({"technology": 0.3}) == {"technology": 0.3, "Unreported": pytest.approx(0.7)}
    holdings = _parse_yf_holdings(pd.DataFrame(
        {"Name": ["Apple Inc."], "Holding Percent": [0.06]}, index=["AAPL"]
    ))
    assert holdings[0].weight == 0.06
    assert holdings[0].symbol == "AAPL"
    alloc = _parse_justetf_allocation([{"name": "USA", "percentage": 80}])
    assert alloc == {"USA": .8, "Unreported": pytest.approx(.2)}
    just_h = _parse_justetf_holdings([
        {"name": "NVIDIA", "percentage": 5.1, "isin": "US67066G1040"}
    ])
    assert just_h[0].weight == pytest.approx(.051)


def test_sector_normalization_examples():
    assert normalize_sector("realestate") == normalize_sector("Real Estate")
    assert normalize_sector("financial_services") == normalize_sector("Finance")
    assert normalize_sector("Business Services") != normalize_sector("Consumer Services")


@pytest.mark.parametrize("failure", [None, "yahoo_sectors", "yahoo_holdings", "justetf", "justetf_sectors"])
def test_download_etf_fetches_each_provider_once(monkeypatch, failure):
    """Provider calls are coalesced: don't re-request allocations for each plot."""
    import sys
    from types import SimpleNamespace
    from src.external_services import data_fetch

    counts = {"fund": 0, "justetf": 0, "quote": 0}

    class FakeTicker:
        def get_info(self):
            counts["quote"] += 1
            return {"regularMarketPrice": 200, "currency": "EUR", "longName": "Test ETF",
                    "exchange": "MIL", "bid": 199, "ask": 201}

        def get_funds_data(self):
            counts["fund"] += 1
            return SimpleNamespace(
                sector_weightings={"technology": 2} if failure == "yahoo_sectors" else {"technology": 0.6, "healthcare": 0.4},
                top_holdings=pd.DataFrame(
                    {"Name": ["Apple Inc"], "Holding Percent": [2 if failure == "yahoo_holdings" else 0.05]},
                    index=["AAPL"],
                ),
            )

    def fake_justetf(isin, *, include_gettex, expand_allocations):
        counts["justetf"] += 1
        assert include_gettex is False
        assert expand_allocations is True
        if failure == "justetf":
            raise ConnectionError("Provider unavailable")
        return {
            "name": "Test ETF",
            "sectors": [{"name": "Technology", "percentage": 200}] if failure == "justetf_sectors" else [{"name": "Technology", "percentage": 60}, {"name": "Finance", "percentage": 40}],
            "countries": [{"name": "USA", "percentage": 100}],
            "top_holdings": [{"name": "Apple Inc", "percentage": 5, "isin": "US0378331005"}],
            "ter": 0.19, "fund_size_eur": 18588, "fund_domicile": "Ireland",
            "replication": "Physical", "holdings_date": "2026-09-30",
        }

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Search=lambda isin, max_results: SimpleNamespace(quotes=[
            {"symbol": "TEST.MI", "isin": isin, "quoteType": "ETF", "currency": "EUR"}
        ]),
        Ticker=lambda symbol: FakeTicker(),
    ))
    monkeypatch.setitem(sys.modules, "justetf_scraping", SimpleNamespace(
        get_etf_overview=fake_justetf,
    ))
    etf = data_fetch.fetch_etf(ISIN_A)
    assert etf.unit_price_eur == 200
    assert etf.yfinance is not None
    assert bool(etf.yfinance.sectors) == (failure != "yahoo_sectors")
    assert bool(etf.yfinance.holdings) == (failure != "yahoo_holdings")
    if failure == "justetf":
        assert etf.justetf is None
    else:
        assert etf.justetf.countries == {"USA": 1.0}
        assert bool(etf.justetf.sectors) == (failure != "justetf_sectors")
        assert etf.justetf.holdings
        assert etf.fund_details["TER (per year)"] == "0.19%"
        assert etf.fund_details["Fund size / AUM"] == "EUR 18,588.00 million"
        assert etf.holdings_date == "2026-09-30"
        assert etf.retrieved_at["justetf"].tzinfo is not None
    assert bool(etf.warnings) == bool(failure)
    assert etf.listing_verified and etf.listing_isin == ISIN_A
    assert etf.exchange == "MIL"
    assert etf.spread_fraction == pytest.approx(.01)
    assert counts == {"fund": 1, "justetf": 1, "quote": 1}


def test_yahoo_unreported_sector_fraction():
    sectors = _parse_yf_sectors({"technology": 0.7})
    assert sectors["Unreported"] == pytest.approx(.3)
    with pytest.raises(ValueError):
        _parse_yf_sectors({"technology": .8, "financials": .8})


def test_listing_verification_rejects_conflicting_isin_and_non_etf():
    from src.external_services.data_fetch import _verify_listing

    assert _verify_listing(ISIN_A, {"isin": ISIN_A}, {})[0] == ISIN_A
    assert _verify_listing(ISIN_A, {}, {}) == (None, "")
    with pytest.raises(ValueError, match="different ISIN"):
        _verify_listing(ISIN_A, {"isin": ISIN_A}, {"isin": ISIN_B})
    with pytest.raises(ValueError, match="not an ETF"):
        _verify_listing(ISIN_A, {}, {"quoteType": "EQUITY"})


def test_missing_yahoo_isin_does_not_block_valuation():
    etf = replace(sample_snapshot().positions[0].etf, listing_verified=False)
    data = pd.DataFrame({"isin": [ISIN_A], "position": [2]})
    assert build_portfolio(data, lambda _: etf).total_eur == 200
    with pytest.raises(ValueError, match="does not match"):
        build_portfolio(data, lambda _: replace(etf, listing_isin=ISIN_B))


@pytest.mark.parametrize("bad_price", [None, 0, -1, float("nan"), float("inf")])
def test_one_invalid_valuation_blocks_the_whole_portfolio(bad_price):
    a, b = sample_snapshot().positions
    bad = replace(b.etf, unit_price_eur=bad_price)
    data = pd.DataFrame({"isin": [ISIN_A, ISIN_B], "position": [2, 4]})
    with pytest.raises(ValueError, match="invalid valuation"):
        build_portfolio(data, lambda isin: a.etf if isin == ISIN_A else bad)


def test_reported_coverage_and_original_sector_names():
    snapshot = sample_snapshot()
    assert source_coverage(snapshot, "holdings", "yfinance") == 1
    assert reported_coverage(snapshot, "holdings", "yfinance") == pytest.approx(.06)
    assert reported_coverage(snapshot, "countries", "justetf") == pytest.approx(.98)
    frames = {source: exposure(snapshot, "sectors", source, original_sectors=True)
              for source in ("yfinance", "justetf")}
    assert "financial_services" in set(frames["yfinance"]["category"])
    assert "Finance" in set(frames["justetf"]["category"])
    fig = exposure_chart(frames, kind="sectors", title="Original names", side_by_side=True)
    assert fig.layout.barmode == "group"
    assert "Technology" in fig.data[0].y and "technology" in fig.data[0].y


def test_portfolio_comparison_chart_labels_and_missing_categories():
    frames = {
        "Portfolio 1 (current)": pd.DataFrame({"category": ["Technology", "Finance"], "weight": [.6, .4]}),
        "Portfolio 2": pd.DataFrame({"category": ["Technology", "Energy"], "weight": [.3, .7]}),
    }
    fig = exposure_chart(frames, kind="sectors", title="Comparison", side_by_side=True)
    assert fig.layout.barmode == "group"
    assert [trace.name for trace in fig.data] == list(frames)
    first, second = [dict(zip(trace.y, trace.x)) for trace in fig.data]
    assert first == {"Technology": .6, "Finance": .4, "Energy": None}
    assert second == {"Technology": .3, "Finance": None, "Energy": .7}


def test_gbp_pence_conversion_and_missing_fx(monkeypatch):
    import sys
    from types import SimpleNamespace
    from src.external_services.data_fetch import eur_conversion

    info = {"regularMarketPrice": 1.2}
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Ticker=lambda _: SimpleNamespace(get_info=lambda: info, fast_info={}),
    ))
    assert eur_conversion("GBp") == pytest.approx(.012)
    assert eur_conversion("GBX") == pytest.approx(.012)
    assert eur_conversion("GBP") == pytest.approx(1.2)
    info.clear()
    with pytest.raises(ValueError, match="Invalid conversion"):
        eur_conversion("GBP")


@pytest.mark.parametrize("yahoo_failure", [False, True])
def test_isin_search_without_yahoo_identifier_and_independent_justetf(monkeypatch, yahoo_failure):
    import sys
    from types import SimpleNamespace
    from src.external_services import data_fetch
    from src.portfolio import etf_exposure

    class FakeTicker:
        def get_info(self):
            if yahoo_failure:
                raise ConnectionError("Yahoo is unavailable")
            return {"regularMarketPrice": 13.12, "currency": "USD", "exchange": "LSE"}

        def get_funds_data(self):
            return SimpleNamespace(sector_weightings={"technology": 1}, top_holdings=None)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        Search=lambda *a, **kw: SimpleNamespace(quotes=[
            {"symbol": "IMID.L", "quoteType": "ETF"},
            {"symbol": "OTHER.DE", "quoteType": "ETF", "currency": "EUR"},
        ]), Ticker=lambda _: FakeTicker(),
    ))
    monkeypatch.setitem(sys.modules, "justetf_scraping", SimpleNamespace(
        get_etf_overview=lambda *a, **kw: {
            "name": "Test ETF", "sectors": [{"name": "Technology", "percentage": 100}],
        },
    ))
    monkeypatch.setattr(data_fetch, "eur_conversion", lambda currency: .9)
    etf = data_fetch.fetch_etf(ISIN_A)
    assert etf.ticker == "IMID.L"  # Preserve Yahoo's result order.
    assert not etf.listing_verified
    assert etf_exposure(etf, "sectors", "justetf").to_dict("records") == [
        {"category": "Technology", "weight": 1},
    ]
    portfolio = pd.DataFrame({"isin": [ISIN_A], "position": [2]})
    if yahoo_failure:
        assert etf.unit_price_eur is None and etf.valuation_error
        with pytest.raises(ValueError, match="EUR conversion unavailable"):
            build_portfolio(portfolio, lambda _: etf)
    else:
        assert etf.unit_price_eur == pytest.approx(13.12 * .9)
        assert build_portfolio(portfolio, lambda _: etf).total_eur == pytest.approx(2 * 13.12 * .9)
