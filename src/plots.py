"""Plotly visualization. No network calls or Streamlit dependencies."""
from __future__ import annotations

from collections import defaultdict
import re

import pandas as pd
import plotly.graph_objects as go

from src.portfolio import normalize_holding_name

_COLORS = {"yfinance": "#4477AA", "justetf": "#DB6B34"}
_LABELS = {"yfinance": "Yahoo Finance", "justetf": "justETF"}


def _label_key(name: str, kind: str) -> str:
    if kind == "holdings":
        return normalize_holding_name(name)
    if kind == "sectors":
        return name  # Preserve original provider labels when harmonization is off.
    return re.sub(r"\s+", " ", name.strip().casefold())


def exposure_chart(
    frames: dict[str, pd.DataFrame],
    *,
    kind: str,
    title: str,
    max_categories: int | None = None,
    side_by_side: bool = False,
    weight_label: str = "Weight in total portfolio",
) -> go.Figure:
    """One row per category, two narrow/wide overlaid marks when comparing.

    Values absent from a source remain None, not a reported 0%.
    """
    available = {source: df for source, df in frames.items() if not df.empty}
    if not available:
        raise ValueError("No reported data available for this plot")

    combined: dict[str, dict[str, float]] = defaultdict(dict)
    names: dict[str, str] = {}
    for source, df in available.items():
        for row in df.itertuples(index=False):
            label = str(row.category)
            key = _label_key(label, kind)
            if not key:
                continue
            if source in combined[key]:
                # Normalize label collisions within the same provider.
                combined[key][source] += float(row.weight)
            else:
                combined[key][source] = float(row.weight)
            if key not in names or source == "yfinance":
                names[key] = label

    ordered = sorted(combined, key=lambda key: max(combined[key].values()), reverse=True)
    if max_categories is not None:
        ordered = ordered[:max_categories]
    categories = [names[key] for key in ordered]

    fig = go.Figure()
    comparing = len(available) > 1 and not side_by_side
    for source in ("yfinance", "justetf"):
        if source not in available:
            continue
        vals = [combined[key].get(source) for key in ordered]
        fig.add_trace(go.Bar(
            x=vals,
            y=categories,
            name=_LABELS[source],
            orientation="h",
            width=None if side_by_side else ((0.72 if source == "yfinance" else 0.40) if comparing else 0.66),
            opacity=(0.53 if source == "yfinance" else 0.94) if comparing else 0.95,
            marker_color=_COLORS[source],
            marker_line_width=0,
            hovertemplate=f"<b>%{{y}}</b><br>{_LABELS[source]}: %{{x:.2%}}<extra></extra>",
        ))

    fig.update_layout(
        title=title,
        barmode="group" if side_by_side else "overlay",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Arial, sans-serif", size=13),
        legend=dict(orientation="h", x=0, y=1.08, yanchor="bottom"),
        margin=dict(l=20, r=25, t=105, b=50),
        height=max(440, min(1150, 110 + 32 * len(categories))),
        hovermode="closest",
    )
    fig.update_xaxes(
        title=weight_label,
        tickformat=".0%",
        rangemode="tozero",
        showgrid=True,
        gridcolor="#E6EAF0",
        zeroline=True,
        zerolinecolor="#A0A7B0",
    )
    fig.update_yaxes(
        categoryorder="array",
        categoryarray=list(reversed(categories)),
        showgrid=False,
        automargin=True,
    )
    return fig
