# ETF Portfolio Analytics — Streamlit MVP

A lightweight demo that loads each ETF's **current price (Yahoo Finance)** and
**sector/country allocations and reported top 10 holdings (Yahoo Finance and justETF)**,
then makes portfolio-weighted charts without downloading again each time a chart changes.
**No historical price series** are downloaded explicitly.

## Run locally

Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/) are recommended.
Git must be installed because `justetf-scraping` is installed from its GitHub repository.

```bash
uv sync
uv run streamlit run app.py
```

The browser opens at http://localhost:8501. The example `data/portfolio.example.csv`
is preloaded until you save your own `data/portfolio.csv`.

To run the offline tests:

```bash
uv run pytest
```

## Use

1. Add/remove ISINs in the table and specify **numbers of ETF units**, not EUR amounts.
2. Choose **Save CSV locally** to save a private working copy as `data/portfolio.csv`,
   or **Download portfolio.csv** to save a file on your device.
3. Click **Load portfolio data**. This fetches **one justETF overview and one Yahoo
   fund-data response per ETF** (plus current quote and any necessary FX quote).
4. Switch among **Sectors**, **Geography (justETF only)**, and **Top holdings**.
   Sectors and holdings can show Yahoo Finance, justETF, or **overlaid bars**.
5. ETF data is held in Streamlit session state and publicly available per-ISIN fetch
   results are cached for one hour. **Refresh all external data** invalidates the cache.

## Organization

```text
app.py                         Streamlit UI, editing and session state
src/models.py                  Provider-independent data structures
src/external_services/data_fetch.py  Network requests + data normalization
src/portfolio.py               CSV, valuation, exposure aggregation
src/plots.py                   Plotly overlaid horizontal bar charts
data/portfolio.example.csv      Illustrative input
```

## Scope and caveats

- **Yahoo Finance ticker resolution is best effort**: an ISIN can have many exchange
  listings. The selected ticker, quote currency and price are shown in the ETF table;
  verify them, especially for foreign-currency or GBP-pence listings. Loading stops
  if a position cannot be priced instead of silently changing the denominator.
- Portfolio weights are based on `ETF units × quote price in EUR` divided by **total
  portfolio value**. The two providers share this same denominator. Missing provider
  allocations are **not** rescaled across other ETFs; the interface reports coverage.
- `GBp` and `GBX` represent pence rather than pounds and are converted accordingly.
- Sector mappings are **display-level approximate matches**, not authoritative
  mappings between classification taxonomies. Communication Services versus
  Telecommunication is not strictly identical.
- Only **10 holdings per ETF** are used. An aggregate top-N portfolio view is therefore
  a ranking of *disclosed holdings*, not guaranteed to be the true top-N constituent
  list. Missing holdings should not be interpreted as 0% exposure.
- Individual holdings with different identifiers or different names may remain on
  separate rows. Share classes are not deliberately collapsed.
- For local use, `data/portfolio.csv` is gitignored. For **public hosting**, do not
  store user portfolios on a shared server filesystem; prefer the CSV download
  or implement per-user persistent storage and authentication. Cloud app files can
  be ephemeral.
- JustETF scraping depends on the public site's availability and behavior, which may
  change. The app flags individual source failures but requires Yahoo market quotes
  for valid portfolio valuation.

## Deployment

Push this repository (without `data/portfolio.csv`) to GitHub, then connect it to
[Streamlit Community Cloud](https://share.streamlit.io). Set the entry point to
`app.py`. Validate dependency installation and justETF connectivity in that
runtime before relying on a deployment.
