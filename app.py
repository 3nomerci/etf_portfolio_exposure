"""ETF portfolio dashboard MVP. Run: uv run streamlit run app.py"""
from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st

from src.external_services.data_fetch import fetch_etf
from src.plots import exposure_chart
from src.portfolio import (
    build_portfolio,
    exposure,
    etf_exposure,
    load_portfolio_csv,
    reported_coverage,
    source_coverage,
    validate_portfolio,
)
from src.ui import SOURCE_LABELS, etf_quality_panel, retrieval_range

BASE = Path(__file__).resolve().parent
LOCAL_CSV = BASE / "data" / "portfolio.csv"
EXAMPLE_CSV = BASE / "data" / "portfolio.example.csv"

st.set_page_config(page_title="ETF Portfolio Exposure", page_icon="📊", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False, max_entries=256) # ttl: max time to keep entry in cache
def cached_etf(isin: str, ticker_override: str = ""):
    """Cache public provider responses; portfolio positions stay in session state."""
    return fetch_etf(isin, ticker_override)


def initial_portfolio() -> pd.DataFrame:
    return load_portfolio_csv(LOCAL_CSV if LOCAL_CSV.exists() else EXAMPLE_CSV)


if "portfolio_df" not in st.session_state:
    st.session_state.portfolio_df = initial_portfolio()
if "snapshot" not in st.session_state:
    st.session_state.snapshot = None
if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0
if "etf_data" not in st.session_state:
    st.session_state.etf_data = {}
if "listing_choices" not in st.session_state:
    st.session_state.listing_choices = pd.DataFrame({
        "isin": pd.Series(dtype=str), "ticker": pd.Series(dtype=str),
    })

st.title("ETF Portfolio Exposure")
st.caption("Edit ETF units, download data once, then explore exposures without further network calls.")
st.caption("For information only, not investment advice. Provider data may be incomplete, delayed or outdated.")

with st.expander("Portfolio · ISIN and number of units", expanded=True):
    upload = st.file_uploader("Import an existing portfolio CSV, or modify the table below.", type="csv", key="portfolio_upload")
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
                st.session_state.etf_data = {}
                st.session_state.load_error = None
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
                    format="%.5f", help="Number of ETF units, not a monetary amount",

                ),
            },
        )
        with st.expander("Change Yahoo ticker (optional)"):
            st.caption("The app uses the first ETF result from a Yahoo search for your ISIN, then converts "
                       "its price to EUR. Leave this table empty to use that result. "
                       "If it is the wrong listing, enter its ISIN and the Yahoo ticker to use instead "
                       "(including the exchange suffix, for example IMID.L). Your portfolio ISIN stays the same.")
            listing_choices = st.data_editor(
                st.session_state.listing_choices[["isin", "ticker"]], num_rows="dynamic", hide_index=True,
                width="stretch", key=f"listing_editor_{st.session_state.editor_version}",
                column_config={
                    "isin": st.column_config.TextColumn("ISIN"),
                    "ticker": st.column_config.TextColumn("Yahoo ticker"),
                },
            )
        st.caption("Click **Load portfolio data** to retrieve prices and allocations. Then choose a chart below.")
        load_clicked = st.form_submit_button("Load portfolio data", type="primary", width="stretch", icon="🚀")

    download = st.download_button(
                        "Download CSV",
                        data=st.session_state.portfolio_df.to_csv(index=False).encode("utf-8"),
                        file_name="portfolio.csv",
                        mime="text/csv",
                        width="stretch",
                        help="Useful for backups and when the app is hosted: downloaded CSV stays on your device.",
                        icon="💾",
                        )
    refresh_clicked = st.button("Refresh all external data", help="Reloads saved inputs and clears the one-hour public-data cache")
    if load_clicked:
        st.session_state.snapshot = None
        st.session_state.etf_data = {}
        st.session_state.load_error = None
        try:
            cleaned = validate_portfolio(edited)
            choices = listing_choices.fillna({"isin": "", "ticker": ""}).copy()
            choices["isin"] = choices["isin"].str.strip().str.upper()
            choices["ticker"] = choices["ticker"].str.strip().str.upper()
            choices = choices[(choices["isin"] != "") | (choices["ticker"] != "")].reset_index(drop=True)
            if ((choices["isin"] == "") | (choices["ticker"] == "")).any() or choices["isin"].duplicated().any():
                raise ValueError("Listing corrections need one ISIN and one ticker per row, without duplicate ISINs")
        except Exception as exc:
            st.error(f"Invalid portfolio: {exc}")
        else:
            st.session_state.portfolio_df = cleaned
            st.session_state.listing_choices = choices
            st.session_state.editor_version += 1
            refresh_clicked = True

    if refresh_clicked:
        if not load_clicked:
            cached_etf.clear()
        st.session_state.snapshot = None  # Failed loads/refreshes never retain stale weights.
        st.session_state.etf_data = {}
        choices = st.session_state.listing_choices
        overrides = dict(zip(choices["isin"], choices["ticker"]))

        def load_etf(isin):
            etf = cached_etf(isin, overrides.get(isin, ""))
            st.session_state.etf_data[isin] = etf
            return etf

        with st.spinner("Loading quotes and available allocations for all ETFs…"):
            try:
                st.session_state.snapshot = build_portfolio(st.session_state.portfolio_df, loader=load_etf)
                st.session_state.load_error = None
            except Exception as exc:
                st.session_state.load_error = str(exc)
        st.rerun()

snapshot = st.session_state.snapshot
if st.session_state.get("load_error"):
    st.warning(f"Portfolio total unavailable: {st.session_state.load_error}")
etfs = list(st.session_state.etf_data.values())
etf_quality_panel(etfs, snapshot)
if not etfs:
    st.info("Click Load portfolio data to fetch Yahoo prices and justETF allocations.")
    st.stop()

selected_etf = None
if snapshot:
    head_l, head_m, head_r = st.columns(3)
    head_l.metric("Portfolio value", f"€{snapshot.total_eur:,.2f}")
    head_m.metric("ETF positions", f"{len(snapshot.positions)}")
    head_r.metric("Data loaded (UTC)", snapshot.loaded_at.strftime("%d %b %Y · %H:%M"))
else:
    st.info("Portfolio weights need a usable price and EUR conversion for every ETF. "
            "You can still view each ETF's justETF allocations below, fetched directly by ISIN. "
            "These percentages describe the selected ETF, not the whole portfolio.")
    selected_isin = st.selectbox("ETF to view", [etf.isin for etf in etfs])
    selected_etf = next(etf for etf in etfs if etf.isin == selected_isin)

st.divider()
st.subheader("ETF allocations" if selected_etf else "Exposure analysis")
chart_kind = st.segmented_control(
    "Chart",
    options=["Sectors", "Geography", "Top holdings"],
    default="Sectors",
)
if chart_kind is None:
    chart_kind = "Sectors"
kind = {"Sectors": "sectors", "Geography": "countries", "Top holdings": "holdings"}[chart_kind]

if kind == "countries" or selected_etf:
    st.caption("Source: justETF, looked up directly by ISIN.")
    sources = ["justetf"]
else:
    selection = st.radio(
        "Source",
        options=["Compare both", "justETF", "Yahoo Finance"],
        horizontal=True,
    )
    sources = {
        "Compare both": ["yfinance", "justetf"],
        "justETF": ["justetf"],
        "Yahoo Finance": ["yfinance"],
    }[selection]

side_by_side = len(sources) == 2 and st.toggle("Show bars side by side", value=True)
original_sectors = True
if kind == "sectors" and len(sources) == 2:
    original_sectors = st.toggle("Use original sector names", value=False)
    st.warning(
        "Provider sector definitions can differ even when names look alike. "
        "Harmonized names are approximate display matches, including Communication Services / "
        "Telecommunication. They do not make the classifications equivalent. "
        "Use original names and side-by-side bars to inspect the differences."
    )
elif kind == "sectors":
    st.caption("Showing the selected provider's original sector names and classifications.")

aggregate_holdings = True
max_categories = None
if kind == "holdings":
    a, b = st.columns(2)
    with a:
        if snapshot:
            aggregate_holdings = st.checkbox("Aggregate common holdings across ETFs", value=True)
    with b:
        max_categories = st.slider("Maximum holdings shown", 5, 50, 20, 5)

frames = {
    source: (
        etf_exposure(selected_etf, kind, source) if selected_etf else
        exposure(snapshot, kind, source, aggregate=aggregate_holdings, original_sectors=original_sectors)
    )
    for source in sources
}
for source in sources:
    if selected_etf:
        continue
    cov = source_coverage(snapshot, kind, source)
    label = SOURCE_LABELS[source]
    reported = reported_coverage(snapshot, kind, source)
    st.caption(f"{label}: ETFs with data {cov:.1%} of portfolio value; reported allocations {reported:.1%}; "
               f"unreported within covered ETFs {max(0, cov - reported):.1%}; source unavailable {max(0, 1 - cov):.1%}.")
    if cov < 0.999:
        st.warning(
            f"{label}: {cov:.1%} of portfolio value has reported {chart_kind.lower()} data. "
            "Missing provider data is not redistributed across the covered ETFs."
        )

if any(not frame.empty for frame in frames.values()):
    fig = exposure_chart(
        frames, kind=kind, title=f"{selected_etf.name if selected_etf else 'Portfolio'} {chart_kind.lower()}",
        max_categories=max_categories,
        side_by_side=side_by_side,
        weight_label="Weight in selected ETF" if selected_etf else "Weight in total portfolio",
    )
    st.plotly_chart(fig, width="stretch")
    for source in sources:
        st.caption(f"Source: {SOURCE_LABELS[source]}{' (via yfinance)' if source == 'yfinance' else ''}. "
                   f"Retrieved: {retrieval_range([selected_etf] if selected_etf else etfs, source)}.")
    if snapshot:
        st.caption(f"Portfolio prices: Yahoo Finance, retrieved {retrieval_range(etfs, 'quote')}. "
                   "Listing links and FX retrieval times are in ETF valuations and data quality.")
    if kind == "holdings":
        st.caption(
            "Only the top 10 disclosed holdings per ETF are fetched. "
            "Unreported securities are unknown, not zero; the ranking is approximate. "
            + ("Each bar is a fraction of the selected ETF." if selected_etf else
               "Each bar is a fraction of the entire portfolio value.")
        )
else:
    st.warning("No exposure data is available for the selected chart and source.")
