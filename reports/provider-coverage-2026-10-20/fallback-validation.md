# justETF fallback fix — 20 October 2026

The recommended small fix is implemented in
`src/external_services/data_fetch.py`, in `_justetf_overview()`.

## Behavior

- Keep countries, sectors and holdings that the expanded request already returned.
- If countries or sectors are empty, request the normal profile page **once**,
  with expansion disabled, and fill only the missing lists.
- If that fallback fails, keep any successful data from the original response.
- If the original main page is blocked, report it unavailable without retrying it.
- Show **Using justETF page summaries** prominently in the main analysis and each
  comparison portfolio when a dataset was recovered this way. These lists may group
  smaller allocations under Other; full detail is not confirmed.

The installed library is unchanged. No country is guessed from an ETF name, no
allocation is rescaled to hide missing data, and invalid populated allocations
remain subject to the existing validation. Yahoo listing selection is unchanged.
The live-audit script now defaults to a slower five-second pause between funds;
this does not guarantee that justETF will allow a large batch.

## Verification

**40 offline tests pass, with no expected failures.** They include the captured
HTTP-200 access-denied response, empty and unrelated expansion responses, preservation
of successful expanded lists, recovery of both lists with one extra page request,
and a failed fallback that must not discard successful sectors or holdings.

An offline replay of the recorded country lists from all 52 accessible profiles
in the original audit retained geography for **51/52 instead of 27/52**, recovering
the 24 previously discarded lists. This is a replay, not a new live 100-ETF result.
See [replay rows](fallback_replay.csv) and [summary](fallback_replay_summary.json).

A new, slower live check also succeeded for all three selected funds:

| ISIN | Result |
| --- | --- |
| IE00B7K93397 — UBS S&P 500 | Page fallback retained United States 96.71%, Ireland 1.14%, Other 2.15% |
| IE00B4L5Y983 — iShares Core MSCI World | Expanded list preserved: 11 returned country categories; no fallback |
| IE00B5BMR087 — iShares Core S&P 500 | Page fallback retained the three returned country categories |

All three also had usable EUR prices. The full responses, timestamps and fetching
file hash are in [fallback_live_check.json](fallback_live_check.json).

Streamlit checks using these live responses confirmed restored geography charts,
visible page-summary notices in both views, preservation of the World ETF's expanded
list, and no new fetches when changing charts.

## Remaining limits

The fix recovers data available on a normal page. It cannot recover an HTTP-403
blocked profile or a dataset the provider does not disclose. The original audit's
48 blocked profiles remain an unknown outcome in that historical result. The
separate invalid sector total and seven Yahoo listing failures from that audit
were not silently accepted or changed by this fix.

Existing cached snapshots need reloading. Use **Refresh all external data** for the
main portfolio, or click **Compare portfolios** to rebuild that comparison.
