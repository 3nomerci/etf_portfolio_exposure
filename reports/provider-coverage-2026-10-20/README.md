# Live ETF data coverage audit — 20 October 2026

**Update:** the application fallback has since been implemented and verified.
See [follow-up validation](fallback-validation.md). The figures and known failure
below are preserved as the original baseline, not presented as current behavior.

The application was tested with **100 large ETFs**, plus the reported UBS S&P 500
case separately. **No application fetching or visualization code was changed.**

## Sample and method

The sample is the 100 largest long-only ETFs by fund size in the
[justETF EUR screener](https://www.justetf.com/en/search.html?search=ETFS), selected
from 3,479 returned records on 20 October 2026. Fund size is a practical proxy for
popularity, not a count of investors. These are 100 ISINs/share classes, not 100
different indexes, and this is not a sample of US-listed ETFs.

The smallest selected fund had reported size EUR 5,217 million. The complete
[sample](sample.csv) includes the selection rank, name and reported size. UBS
`IE00B7K93397` is outside that top 100, so it is an extra case and is excluded from
all `/100` results below.

The existing `fetch_etf()` function was run sequentially, with a one-second pause
between ETFs and a 25-second timeout on justETF requests. There were no retries in
the main pass. The pass ran from **05:55 to 05:59 America/New_York**, or
09:55–09:59 UTC. Versions, source commit and the tested fetching file's hash are in
[run.json](run.json). Raw per-ETF results, diagnostics and provider messages are in
[results.jsonl](results.jsonl).

## Results through the current application

| Dataset | Available out of 100 |
| --- | ---: |
| Usable Yahoo price and EUR conversion | 93 |
| Yahoo sectors | 85 |
| Yahoo reported top holdings | 72 |
| justETF profile received | 52 |
| justETF sectors | 49 |
| justETF geography | 27 |
| justETF reported top holdings | 51 |

**The 48 failed justETF profile requests returned HTTP 403. Their data availability
is unknown.** They must not be interpreted as 48 funds without geography or holdings.
Failures started at the 53rd profile. Later checks of an earlier successful fund,
the first blocked fund and the UBS case still returned 403, even with pauses and
without asking for expanded allocations. This pattern is consistent with temporary
request blocking, but the response does not establish its precise cause or duration.
Follow-up evidence is in [followup_checks.json](followup_checks.json).

Yahoo allocation data are only requested after successful pricing in the current
function. Its sector/holdings counts therefore include seven listing failures
where those allocation requests were never made. Availability here means a usable
dataset was returned; it does not mean a full list of constituents, current data or
an independently verified ticker. Yahoo returned no matching explicit ISIN for any
of the 100 searches/quotes, so the existing best-effort search limitation remains.

The filterable [coverage.csv](coverage.csv) contains every ETF, each dataset flag,
HTTP status, warnings and the main-page comparison.

## Why the S&P ETF lost geography

For **UBS Core S&P 500 UCITS ETF USD dis — IE00B7K93397**, the initial check, before
the later blocking, found these country rows on the normal page:

| justETF category | Weight |
| --- | ---: |
| United States | 96.71% |
| Ireland | 1.14% |
| Other | 2.15% |

The optional country expansion request returned **HTTP 200**, but its body was:

```xml
<ajax-response><redirect><![CDATA[/en/access-denied.html]]></redirect></ajax-response>
```

The installed scraper checks whether that response contains text. Because it does,
it tries to extract country rows and obtains an empty list. It only falls back to
the original page if no response text was received. **The valid original country
rows are therefore discarded.** The application then correctly warns that its
returned dataset is missing, but the upstream retrieval has already lost the data.

This is not evidence that justETF lacks geography for this fund. Nor should the app
guess 100% United States from the fund's name: the actual provider figures above
are different. [Initial evidence](sp500_diagnosis.json), [country table](sp500_country_table.html).

## How widespread the same defect is

Among the **52 profiles that were accessible**:

- Current geography retrieval succeeded for **27/52**.
- Another **24/52** had valid country rows on the normal page but an empty parsed
  expansion response. Every one of these responses redirected to access-denied.
- Using the original page when the parsed expansion is empty would retain geography
  for **51/52** accessible profiles. This was measured from the same responses,
  without extra requests or changing the application.
- The remaining accessible profile, `LU0290358497` (Xtrackers II EUR Overnight Rate
  Swap UCITS ETF 1C), supplied no country dataset in either response inspected.

This recovery figure measures dataset presence, not complete country detail.
The normal page may contain a short list and an “Other” category. It must not be
presented as a full country breakdown when that detail was not supplied.

There was also a separate sector-data issue: `IE00B3ZW0K18` returned expanded sector
weights totalling **100.63%**, exceeding the application's 100.5% tolerance. The app
rejected that dataset. Two accessible funds supplied no sectors, and one supplied
no top holdings. These are different situations from the discarded-country bug.
The supported fund-fact fields were empty on all 52 accessible profiles; the
profile scraper's empty values do not prove those facts are absent on the website.

## Seven Yahoo valuation failures

The failures were checked again after the batch:

| ISIN | Yahoo search result |
| --- | --- |
| DE0005933931 | DAXXF, labelled MUTUALFUND |
| LU2572257124 | LU2572257124.SG, labelled MUTUALFUND |
| LU2263803533 | LU2263803533.SG, labelled MUTUALFUND |
| LU2573966905 | LU2573966905.SG, labelled MUTUALFUND |
| IE00B53L3W79 | No results |
| IE00B53QG562 | No results |
| DE000A0F5UF5 | No results |

The current app accepts search results labelled ETF and rejects MUTUALFUND.
These four rejections do not mean the requested funds do not exist, or that Yahoo
has no listing for them. They expose a limitation of this search/filter method.
This audit does not automatically accept those results or substitute another fund.

## Tests added and the required fetching change

The old tests mocked the already-parsed provider dictionary. They tested portfolio
calculations and parsing of that dictionary, but could not detect a defect inside
the HTML/expansion scraper.

New offline tests replay the real country-table structure and the HTTP-200
access-denied response. They check the page data, distinguish the denied response
from a dataset, and verify that disabling expansion retains the original countries.
A regression requiring countries to survive a failed expansion is marked as an
**expected failure**: it exposes the installed upstream defect rather than falsely
claiming it has been fixed. Current result: **33 passed, 1 expected failure**.

The small required change is to fall back when the **parsed country/sector list is
empty**, not merely when the response body is empty. This should stay in data
retrieval code and preserve a warning when only the shorter page list is available.
Simply disabling expansion for every ETF would lose detail for funds where expanded
lists do work. The application and installed dependency were left unchanged in this
testing task.

## Repeat the audit

```powershell
uv run python scripts/check_provider_coverage.py --sample reports/provider-coverage-2026-10-20/sample.csv --output reports/new-coverage-run --delay 5
uv run pytest
```

Use a fresh output directory for a new pass. Interrupted runs can resume from their
existing results. A slower pace may help, but does not guarantee access; blocked
responses must remain a separate outcome. These results describe this date and
environment, not a permanent provider success rate or an investment recommendation.
