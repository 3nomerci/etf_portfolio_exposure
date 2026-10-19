"""Portfolio comparison UI. Provider requests use the loader supplied by app.py."""
import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st

from src.plots import exposure_chart
from src.portfolio import build_portfolio, exposure, reported_coverage, source_coverage, validate_portfolio
from src.ui import SOURCE_LABELS, retrieval_range


def portfolio_comparison(current, loader, ticker_overrides):
    with st.expander("Compare two portfolios", expanded=False):
        if current is None:
            st.info("Load a portfolio with usable prices above before comparing portfolio weights.")
            return
        st.caption("The current loaded portfolio is Portfolio 1. Upload or edit Portfolio 2 below, "
                   "using ISINs and numbers of ETF units. Common ETFs use the same prices in both portfolios.")
        if "comparison" not in st.session_state:
            st.session_state.comparison = {
                "input": pd.DataFrame([{"isin": p.etf.isin, "position": p.units} for p in current.positions]),
                "snapshot": None, "base": None, "error": None, "upload": None, "version": 0,
            }
        state = st.session_state.comparison
        upload = st.file_uploader("Import Portfolio 2 CSV", type="csv", key="comparison_upload")
        if upload is not None:
            digest = hashlib.sha256(upload.getvalue()).hexdigest()
            if digest != state["upload"]:
                state["snapshot"] = None
                try:
                    state["input"] = validate_portfolio(pd.read_csv(BytesIO(upload.getvalue()), dtype={"isin": str}))
                except Exception as exc:
                    st.error(f"Cannot import Portfolio 2: {exc}")
                else:
                    state.update(upload=digest, version=state["version"] + 1, error=None)
        with st.form("compare_portfolios"):
            edited = st.data_editor(
                state["input"], key=f"comparison_editor_{state['version']}",
                num_rows="dynamic", hide_index=True, width="stretch",
                column_config={
                    "isin": st.column_config.TextColumn("ISIN", required=True),
                    "position": st.column_config.NumberColumn("Position (units)", required=True,
                                                             min_value=0.000000001, format="%.5f"),
                },
            )
            compare_clicked = st.form_submit_button("Compare portfolios")
        if compare_clicked:
            state.update(snapshot=None, error=None, base=current.loaded_at)
            shared = {p.etf.isin: p.etf for p in current.positions}

            def load(isin):
                if isin not in shared:
                    shared[isin] = loader(isin, ticker_overrides.get(isin, ""))
                return shared[isin]

            with st.spinner("Loading Portfolio 2…"):
                try:
                    state["input"] = validate_portfolio(edited)
                    state["snapshot"] = build_portfolio(state["input"], load)
                except Exception as exc:
                    state["error"] = str(exc)
            state["version"] += 1
            st.rerun()
        if state["base"] != current.loaded_at:
            st.caption("Click Compare portfolios to compare against the current loaded portfolio.")
            return
        if state["error"]:
            st.error(f"Cannot compare portfolios: {state['error']}")
            return
        other = state["snapshot"]
        if other is None:
            return

        portfolios = {"Portfolio 1 (current)": current, "Portfolio 2": other}
        for column, (label, snapshot) in zip(st.columns(2), portfolios.items()):
            column.metric(f"{label} value", f"€{snapshot.total_eur:,.2f}")
        chart = st.segmented_control("Compare exposures", ["Sectors", "Geography", "Top holdings"],
                                     default="Sectors", key="comparison_chart") or "Sectors"
        kind = {"Sectors": "sectors", "Geography": "countries", "Top holdings": "holdings"}[chart]
        source = "justetf"
        if kind != "countries":
            choice = st.radio("Comparison data source", list(SOURCE_LABELS.values()),
                              index=1, horizontal=True, key="comparison_source")
            source = next(key for key, label in SOURCE_LABELS.items() if label == choice)
        frames = {label: exposure(snapshot, kind, source, original_sectors=True)
                  for label, snapshot in portfolios.items()}
        if any(not frame.empty for frame in frames.values()):
            st.plotly_chart(exposure_chart(
                frames, kind=kind, title=f"Portfolio comparison · {chart.lower()}", side_by_side=True,
                max_categories=20 if kind == "holdings" else None, weight_label="Weight within each portfolio",
            ), width="stretch", key="portfolio_comparison_plot")
        else:
            st.warning("No data is available for this comparison and source.")
        coverage = []
        for label, snapshot in portfolios.items():
            covered = source_coverage(snapshot, kind, source)
            reported = reported_coverage(snapshot, kind, source)
            coverage.append({"Portfolio": label, "ETFs with data": covered, "Reported": reported,
                             "Unreported": max(0.0, covered - reported), "Source unavailable": max(0.0, 1 - covered)})
            etfs = [p.etf for p in snapshot.positions]
            st.caption(f"{label} · Source: {SOURCE_LABELS[source]}, retrieved {retrieval_range(etfs, source)}. "
                       f"Yahoo prices retrieved {retrieval_range(etfs, 'quote')}.")
            if any("fx" in etf.retrieved_at for etf in etfs):
                st.caption(f"{label} · Yahoo FX retrieved {retrieval_range(etfs, 'fx')}.")
        st.dataframe(pd.DataFrame(coverage), hide_index=True, width="stretch",
                     column_config={key: st.column_config.NumberColumn(format="percent")
                                    for key in coverage[0] if key != "Portfolio"})
        st.caption("Each portfolio uses its own total EUR value. Missing allocations are not redistributed. "
                   "Both portfolios use the same provider; sector names are the provider's original names.")
        if kind == "holdings":
            st.caption("Only the top 10 disclosed holdings per ETF are available; the chart shows up to 20 "
                       "reported holdings. Missing holdings are unknown, so this is not a complete overlap comparison.")
