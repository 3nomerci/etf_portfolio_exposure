"""Small Streamlit data-quality panels. No data retrieval lives here."""
from urllib.parse import quote

import pandas as pd
import streamlit as st

from src.portfolio import reported_coverage, source_coverage

SOURCE_LABELS = {"yfinance": "Yahoo Finance", "justetf": "justETF"}


def retrieval_range(etfs, source):
    times = [etf.retrieved_at[source] for etf in etfs if source in etf.retrieved_at]
    if not times:
        return "unavailable"
    first, last = min(times), max(times)
    text = first.strftime("%Y-%m-%d %H:%M:%S")
    if first != last:
        text += " to " + last.strftime("%Y-%m-%d %H:%M:%S")
    return text + " UTC"


def etf_quality_panel(etfs, snapshot):
    """Also usable after a failed load, without displaying partial portfolio totals."""
    with st.expander("ETF valuations and data quality", expanded=False):
        if not etfs:
            st.caption("Load the portfolio to inspect listings, sources and available fund details.")
            return
        positions = {p.etf.isin: p for p in snapshot.positions} if snapshot else {}
        rows = []
        for etf in etfs:
            row = {
                "ISIN": etf.isin, "Ticker (Yahoo)": etf.ticker, "ETF": etf.name,
                "Exchange": etf.exchange or "Unavailable", "Quote currency": etf.quote_currency,
                "Listing ISIN": etf.listing_isin or "Unavailable",
                "Listing check": "ISIN matched" if etf.listing_verified else "Yahoo search result; ISIN not returned",
                "Price (quote currency)": etf.quote_price,
                "Unit price (EUR)": etf.unit_price_eur,
            }
            if snapshot:
                position = positions[etf.isin]
                row.update({"Units": position.units, "Value (EUR)": position.value_eur,
                            "Portfolio weight": position.value_eur / snapshot.total_eur})
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Portfolio weight": st.column_config.NumberColumn(format="percent")})
        st.caption("Prices and currency conversion: Yahoo Finance. GBp/GBX quotes are pence. "
                   "Yahoo often omits the ISIN from a quote. This does not mean your ISIN is wrong: "
                   "the app uses its search result and loads normally. "
                   "Use Change Yahoo ticker in Portfolio only if you want a different listing. "
                   "justETF allocations are fetched directly by ISIN.")
        for etf in etfs:
            for warning in etf.warnings:
                st.warning(warning)

        if snapshot:
            coverage = []
            for source, kinds in (("yfinance", ("sectors", "holdings")),
                                  ("justetf", ("sectors", "countries", "holdings"))):
                for kind in kinds:
                    covered = source_coverage(snapshot, kind, source)
                    reported = reported_coverage(snapshot, kind, source)
                    coverage.append({
                        "Source": SOURCE_LABELS[source], "Dataset": kind,
                        "ETFs with data": covered, "Reported": reported,
                        "Unreported within covered ETFs": max(0.0, covered - reported),
                        "Provider data unavailable": max(0.0, 1 - covered),
                    })
            st.dataframe(pd.DataFrame(coverage), hide_index=True, width="stretch",
                         column_config={key: st.column_config.NumberColumn(format="percent")
                                        for key in coverage[0] if key not in ("Source", "Dataset")})
            st.caption("Coverage figures are shares of total portfolio value. ETFs with data may disclose "
                       "only part of their holdings. Unreported and unavailable portions are unknown, not zero.")

        facts = []
        for etf in etfs:
            links = f"[justETF](https://www.justetf.com/en/etf-profile.html?isin={etf.isin})"
            if etf.ticker:
                links += f" · [Yahoo {etf.ticker}](https://finance.yahoo.com/quote/{quote(etf.ticker, safe='')}/)"
            st.markdown(f"**{etf.isin}** · {links}")
            for source, label in {"quote": "Yahoo price", "fx": "Yahoo FX", **SOURCE_LABELS}.items():
                if source in etf.retrieved_at:
                    st.caption(f"{label} retrieved: {retrieval_range([etf], source)}")
            if etf.holdings_date:
                st.caption(f"justETF holdings date: {etf.holdings_date}")
            if etf.quote_as_of:
                st.caption(f"Yahoo quote time: {etf.quote_as_of.isoformat()}")
            facts.extend({"ISIN": etf.isin, "Detail": key, "Value": value, "Source": "justETF"}
                         for key, value in etf.fund_details.items())
            if etf.spread_fraction is not None:
                facts.append({"ISIN": etf.isin, "Detail": "Indicative bid–ask spread",
                              "Value": f"{etf.spread_fraction:.2%}", "Source": "Yahoo Finance"})
        if facts:
            st.dataframe(pd.DataFrame(facts), hide_index=True, width="stretch")
            st.caption("Only returned facts are shown. Fund currency can differ from quote currency. "
                       "Bid–ask spread uses Yahoo's bid and ask; it may be delayed and is not a guaranteed trading cost.")
        st.caption("Retrieval time is when the app received data, not when the provider measured it.")
