"""ETF portfolio dashboard MVP. Run: uv run streamlit run app.py"""
from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st

from src.external_services.data_fetch import eur_conversion, fetch_etf
from src.plots import exposure_chart
from src.portfolio import (
    build_portfolio,
    exposure,
    load_portfolio_csv,
    save_portfolio_csv,
    source_coverage,
    validate_portfolio,
)

BASE = Path(__file__).resolve().parent
LOCAL_CSV = BASE / "data" / "portfolio.csv"
EXAMPLE_CSV = BASE / "data" / "portfolio.example.csv"

st.set_page_config(page_title="ETF Portfolio Analytics", page_icon="📊", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False, max_entries=256)
def cached_etf(isin: str):
    """Shared cache for public ETF data, never for users' positions."""
    return fetch_etf(isin)


def initial_portfolio() -> pd.DataFrame:
    return load_portfolio_csv(LOCAL_CSV if LOCAL_CSV.exists() else EXAMPLE_CSV)


if "portfolio_df" not in st.session_state:
    st.session_state.portfolio_df = initial_portfolio()
if "snapshot" not in st.session_state:
    st.session_state.snapshot = None
if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0

st.title("ETF Portfolio Analytics")
st.caption("Edit ETF units, download once per ETF, then explore exposures without further network calls.")

with st.expander("Portfolio · ISIN and number of units", expanded=True):
    upload = st.file_uploader("Import an existing portfolio CSV", type="csv", key="portfolio_upload")
    if upload is not None:
        digest = hashlib.sha256(upload.getvalue()).hexdigest()
        if st.session_state.get("last_upload_digest") != digest:
            try:
                new_data = validate_portfolio(pd.read_csv(BytesIO(upload.getvalue()), dtype={"isin": str}))
            except Exception as exc:
                st.error(f"Cannot import CSV: {exc}")
            else:
                st.session_state.portfolio_df = new_data
                st.session_state.snapshot = None
                st.session_state.editor_version += 1
                st.session_state.last_upload_digest = digest
                st.rerun()

    with st.form("edit_portfolio"):
        edited = st.data_editor(
            st.session_state.portfolio_df,
            key=f"portfolio_editor_{st.session_state.editor_version}",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_order=["isin", "position"],
            column_config={
                "isin": st.column_config.TextColumn("ISIN", required=True, help="12-character ISIN"),
                "position": st.column_config.NumberColumn(
                    "Position (units)", required=True, min_value=0.000000001,
                    format="%.5f", help="Number of ETF units, not a monetary amount"
                ),
            },
        )
        left, right = st.columns([1, 1])
        with left:
            save_clicked = st.form_submit_button("Save CSV locally", width="stretch")
        with right:
            load_clicked = st.form_submit_button("Load portfolio data", type="primary", width="stretch")

    if save_clicked or load_clicked:
        try:
            cleaned = validate_portfolio(edited)
        except Exception as exc:
            st.error(f"Invalid portfolio: {exc}")
        else:
            st.session_state.portfolio_df = cleaned
            st.session_state.snapshot = None  # Never display stale weights.
            st.session_state.editor_version += 1
            if save_clicked:
                try:
                    save_portfolio_csv(cleaned, LOCAL_CSV)
                    st.success(f"Saved to {LOCAL_CSV.relative_to(BASE)}")
                except OSError as exc:
                    st.error(f"Could not save CSV on this server: {exc}")
            if load_clicked:
                with st.spinner("Loading quotes and available allocations for all ETFs…"):
                    try:
                        st.session_state.snapshot = build_portfolio(cleaned, loader=cached_etf)
                    except Exception as exc:
                        st.error(f"Portfolio loading failed: {exc}")
                        st.info("Yahoo Finance must resolve a valid quoted listing for every ISIN to value the portfolio.")

    st.download_button(
        "Download portfolio.csv",
        data=st.session_state.portfolio_df.to_csv(index=False).encode("utf-8"),
        file_name="portfolio.csv",
        mime="text/csv",
        width="stretch",
        help="Useful for backups and when the app is hosted: downloaded CSV stays on your device.",
    )
    st.caption("Local save writes to the server's data/portfolio.csv. On shared/cloud hosting, use Download CSV instead: server files may be shared or temporary.")

snapshot = st.session_state.snapshot
if snapshot is None:
    st.info("Click **Load portfolio data** to retrieve prices and allocations. Then choose a chart below.")
    st.stop()

head_l, head_m, head_r = st.columns(3)
head_l.metric("Portfolio value", f"€{snapshot.total_eur:,.2f}")
head_m.metric("ETF positions", f"{len(snapshot.positions)}")
head_r.metric("Data loaded (UTC)", snapshot.loaded_at.strftime("%d %b %Y · %H:%M"))

positions_table = pd.DataFrame([
    {
        "ISIN": p.etf.isin,
        "Ticker (Yahoo)": p.etf.ticker,
        "ETF": p.etf.name,
        "Units": p.units,
        "Quote currency": p.etf.quote_currency,
        "Price (quote currency)": p.etf.quote_price,
        "Unit price (EUR)": p.etf.unit_price_eur,
        "Value (EUR)": p.value_eur,
        "Portfolio weight": p.value_eur / snapshot.total_eur,
    }
    for p in snapshot.positions
])
with st.expander("ETF valuations and data quality", expanded=False):
    st.dataframe(
        positions_table,
        hide_index=True,
        width="stretch",
        column_config={"Portfolio weight": st.column_config.NumberColumn(format="percent")},
    )
    st.caption("Verify the selected Yahoo exchange listing and quote currency. UK quotes in GBp/GBX are converted from pence before EUR valuation.")
    warnings = [warning for p in snapshot.positions for warning in p.etf.warnings]
    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("Both data sources returned their requested datasets for all ETFs.")

st.divider()
st.subheader("Exposure analysis")
chart_kind = st.segmented_control(
    "Chart",
    options=["Sectors", "Geography", "Top holdings"],
    default="Sectors",
)
if chart_kind is None:
    chart_kind = "Sectors"
kind = {"Sectors": "sectors", "Geography": "countries", "Top holdings": "holdings"}[chart_kind]

if kind == "countries":
    st.caption("Geographic exposure is supplied by justETF only.")
    sources = ["justetf"]
else:
    selection = st.radio(
        "Source",
        options=["Overlay both", "justETF", "Yahoo Finance"],
        horizontal=True,
    )
    sources = {
        "Overlay both": ["yfinance", "justetf"],
        "justETF": ["justetf"],
        "Yahoo Finance": ["yfinance"],
    }[selection]

aggregate_holdings = True
max_categories = None
if kind == "holdings":
    a, b = st.columns(2)
    with a:
        aggregate_holdings = st.checkbox("Aggregate common holdings across ETFs", value=True)
    with b:
        max_categories = st.slider("Maximum holdings shown", 5, 50, 20, 5)

frames = {
    source: exposure(snapshot, kind, source, aggregate=aggregate_holdings)
    for source in sources
}
for source in sources:
    cov = source_coverage(snapshot, kind, source)
    label = "Yahoo Finance" if source == "yfinance" else "justETF"
    if cov < 0.999:
        st.warning(
            f"{label}: {cov:.1%} of portfolio value has reported {chart_kind.lower()} data. "
            "Missing provider data is not redistributed across the covered ETFs."
        )

if any(not frame.empty for frame in frames.values()):
    fig = exposure_chart(
        frames, kind=kind, title=f"Portfolio {chart_kind.lower()}",
        max_categories=max_categories,
    )
    st.plotly_chart(fig, width="stretch")
    if kind == "holdings":
        st.caption(
            "Only the top 10 disclosed holdings per ETF are fetched. "
            "Unreported securities are unknown, not zero; the ranking is approximate. "
            "Each bar is a fraction of the entire portfolio value."
        )
    elif kind == "sectors" and len(sources) == 2:
        st.caption(
            "Some sector labels are harmonized for display (including Communication Services / "
            "Telecommunication); their underlying classification definitions may differ."
        )
else:
    st.warning("No exposure data is available for the selected chart and source.")

if st.button("Refresh all external data", help="Clears the one-hour ETF cache and reloads the saved input portfolio"):
    cached_etf.clear()
    eur_conversion.cache_clear()
    with st.spinner("Refreshing market data…"):
        try:
            st.session_state.snapshot = build_portfolio(st.session_state.portfolio_df, loader=cached_etf)
        except Exception as exc:
            st.error(f"Refresh failed: {exc}")
        else:
            st.rerun()
