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
is preloaded unless a local `data/portfolio.csv` already exists.

To run the offline tests:

```bash
uv run pytest
```

## Use

1. Add/remove ISINs in the table and specify **numbers of ETF units**, not EUR amounts.
2. Click **Load portfolio data** to apply edits and fetch quotes and allocations.
   **Download CSV** saves the last submitted portfolio on your device.
3. Open **ETF valuations and data quality** to review each Yahoo ticker, exchange,
   quote currency, source links, retrieval times and coverage. The app searches Yahoo
   using the ISIN, takes the first suitable ETF result and converts its quote to EUR.
   Yahoo often does not return an ISIN in its quote: this does not stop loading or
   mean your ISIN is wrong. If you want another listing, enter the same ISIN and a
   Yahoo ticker under **Change Yahoo ticker (optional)**, then load again. Leave that
   table empty for automatic selection. A reported different ISIN is rejected.
   Ticker choices last for the session; they are not included in the CSV.
4. Switch among **Sectors**, **Geography (justETF only)**, and **Top holdings**.
   Sectors and holdings can compare Yahoo Finance and justETF with side-by-side or
   overlaid bars. For sectors, **Use original sector names** disables approximate
   name mappings. Selecting a single provider always uses its original sector names.
5. ETF data is held in Streamlit session state and publicly available per-ISIN fetch
   results are cached for one hour, separately for each ISIN and chosen ticker.
   A loaded snapshot stays fixed until you load or refresh it; changing charts makes
   no network requests. **Refresh all external data** clears the cache and reloads
   the last submitted inputs. Quotes and any needed currency conversions are fetched
   together. If pricing fails, previous totals and portfolio charts are hidden;
   individual ETF charts from justETF remain available without market prices.

## Organization

```text
app.py                         Streamlit UI, editing and session state
src/models.py                  Provider-independent data structures
src/external_services/data_fetch.py  All network requests, listing checks, provider parsing
src/portfolio.py               CSV, valuation, exposure aggregation
src/plots.py                   Plotly horizontal bar charts
src/ui.py                      Data-quality panels and retrieval-time display
data/portfolio.example.csv      Illustrative input
```

## Sources and limitations

- **This app is for information only, not investment advice.** Check important figures
  against the fund issuer before making decisions. It is an MVP, not a trading tool.
- **Yahoo lookup is best effort.** An ISIN search usually finds the right ETF, but
  the first result is not guaranteed to be correct. The app checks an ISIN when
  Yahoo actually returns one; otherwise it shows “Yahoo search result; ISIN not
  returned” and continues. There is no required confirmation step or extra lookup
  through another website. You can inspect the linked listing and change the ticker.
- An ISIN identifies the fund share class, not a single exchange listing. The app
  uses Yahoo's result order; it does not compare exchanges or find the best venue.
  The selected exchange and actual quote currency are shown. A USD listing can
  still be valued in EUR using the retrieved USD/EUR rate.
- **Prices and currency conversions come from Yahoo Finance through yfinance.**
  Quotes may be delayed or old. The app can fall back to the previous closing price
  and flags that choice. Quote time is shown when available; there is no automatic
  age cutoff. Retrieval times are recorded when data arrive and remain unchanged
  when cached data are reused. They do not mean that the underlying data are fresh.
  The app makes no explicit price-history requests, although yfinance's fallback
  methods can request history internally.
- **Allocations and top holdings come from Yahoo Finance and justETF; countries come
  only from justETF.** Each chart shows its sources and retrieval times; the quality
  panel provides per-listing links and times. justETF's holdings date is shown when
  supplied. Funds and providers may report different dates; the app does not align them.
- Portfolio weights are based on `ETF units × quote price in EUR` divided by **total
  portfolio value**. The two providers share this same denominator. Missing provider
  allocations are **not** spread over other ETFs. If any position has no usable price
  or currency conversion, the whole portfolio valuation is withheld. The app still
  fetches justETF directly by ISIN and lets you view each ETF's own allocations.
  These are labelled as weights within that ETF, not portfolio weights. A known
  Yahoo ISIN mismatch also prevents use of that quote. If only allocations fail,
  available portfolio charts remain usable and show the missing coverage.
- **Coverage has two meanings.** “ETFs with data” measures how much portfolio value
  belongs to funds with that dataset. “Reported” measures the portion actually
  described by the returned categories or holdings. For example, every ETF might have
  a top-ten list while those lists describe only 20% of portfolio value. The remaining
  80% is unknown. The panel separates unreported portions from entirely unavailable
  provider datasets. “Other” remains the provider's category; it is not the app's
  calculated “Unreported” remainder. Small rounding differences may remain.
- `GBp` and `GBX` represent pence rather than pounds and are converted accordingly.
- **Matching sector names does not make their definitions equal.** For example,
  Communication Services can include businesses that another provider does not put
  in Telecommunication. Harmonized labels are only rough display matches. Use the
  original-name switch and side-by-side bars to inspect differences; even identical
  original names do not prove that providers classify companies in the same way.
- Only **10 holdings per ETF** are used. An aggregate top-N portfolio view is therefore
  a ranking of *disclosed holdings*, not guaranteed to be the true top-N constituent
  list. Missing holdings should not be interpreted as 0% exposure.
- Individual holdings with different identifiers or different names may remain on
  separate rows. Existing matching ignores punctuation and common company suffixes,
  but preserves share-class words. It does not use fuzzy matching or a verified
  cross-provider security directory. Similar names can still be misleading, and
  unreported holdings prevent a complete overlap comparison.
- **Fund facts are shown only when returned by the supported fields.** TER, fund size,
  domicile, replication, fund currency, index and distribution details come from the
  justETF profile. The scraper returns TER in percentage points and fund size in
  EUR millions; the panel labels these units explicitly. The current scraper can
  return these fields empty even when they appear on the website. Empty facts are
  omitted, and there is no empty “ETF information” panel. The app does not guess
  domicile or substitute quote currency for fund currency. Fund size is the provider's
  reported figure, not an independently checked or necessarily current total.
- **Bid–ask spread is indicative.** When Yahoo supplies positive bid and ask values
  in the right order, the app shows `(ask − bid) / midpoint` for the selected listing.
  Their times may differ or be unavailable. It is not a live executable spread or
  a full estimate of trading costs. Missing or invalid quotes are omitted.
- For local use, `data/portfolio.csv` is gitignored. For **public hosting**, do not
  store user portfolios on a shared server filesystem; prefer the CSV download
  or implement per-user persistent storage and authentication. Cloud app files can
  be ephemeral.
- justETF scraping and Yahoo responses depend on public services that can change,
  block requests or return incomplete data. Individual allocation failures are shown
  without discarding other usable datasets. There is no second pricing provider,
  automatic retry system or guarantee that plausible but wrong provider data can be
  detected. No extra sources are queried to fill missing facts.

Provider/library references: [yfinance](https://ranaroussi.github.io/yfinance/reference/index.html),
and [justetf-scraping](https://github.com/druzsan/justetf-scraping).

## Deployment

Push this repository (without `data/portfolio.csv`) to GitHub, then connect it to
[Streamlit Community Cloud](https://share.streamlit.io). Set the entry point to
`app.py`. Validate dependency installation and justETF connectivity in that
runtime before relying on a deployment.
