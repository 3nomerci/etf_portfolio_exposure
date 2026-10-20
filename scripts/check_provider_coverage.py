"""Live coverage audit; keep network diagnostics outside the app.

Run from the repo root:
  uv run python scripts/check_provider_coverage.py --sample reports/.../sample.csv --output reports/...
Existing results are reused so an interrupted audit can resume. Use a fresh output
directory for a new run. No application or installed-library code is changed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distribution, version
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from xml.etree import ElementTree

import pandas as pd
import requests
from bs4 import BeautifulSoup
import justetf_scraping.etf_profile as profile
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.external_services.data_fetch import fetch_etf, _parse_justetf_allocation


def page_allocation(html, kind):
    """Compare the discarded main-page data, using the library's own parser."""
    return profile._parse_allocation_from_soup(
        BeautifulSoup(html, "html.parser"), f"etf-holdings_{kind}_row",
        f"tl_etf-holdings_{kind}_value_name", f"tl_etf-holdings_{kind}_value_percentage",
    )


def ajax_status(response):
    result = {"http_status": response.status_code, "content_type": response.headers.get("content-type", "")}
    try:
        root = ElementTree.fromstring(response.text)
        result["redirect"] = root.findtext("redirect") or ""
        result["components"] = len(root.findall(".//component"))
    except ElementTree.ParseError:
        result["redirect"] = "not an XML response"
        result["components"] = 0
    return result


def named_count(allocation):
    return sum(value > 0 for name, value in allocation.items() if name.casefold() != "unreported")


def check_etf(item, output):
    responses = []
    request = requests.Session.request

    def observed(session, method, url, **kwargs):
        kwargs.setdefault("timeout", 25)
        response = request(session, method, url, **kwargs)
        if "justetf.com/en/etf-profile.html" in url:
            responses.append(response)
        return response

    start = time.monotonic()
    log = StringIO()
    with redirect_stdout(log), redirect_stderr(log), patch.object(requests.Session, "request", observed):
        etf = fetch_etf(item["isin"])
    metrics = {
        **item, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - start, 2), "ticker": etf.ticker,
        "exchange": etf.exchange, "quote_currency": etf.quote_currency,
        "eur_valuation": etf.unit_price_eur is not None and not etf.valuation_error,
        "isin_returned_and_matched": etf.listing_verified,
        "yahoo_sectors": bool(etf.yfinance and named_count(etf.yfinance.sectors)),
        "yahoo_holdings": bool(etf.yfinance and etf.yfinance.holdings),
        "justetf_profile": "justetf" in etf.retrieved_at,
        "justetf_sectors": bool(etf.justetf and named_count(etf.justetf.sectors)),
        "justetf_geography": bool(etf.justetf and named_count(etf.justetf.countries)),
        "justetf_holdings": bool(etf.justetf and etf.justetf.holdings),
        "justetf_page_fallbacks": ",".join(etf.justetf.page_fallbacks) if etf.justetf else "",
        "fund_facts_count": len(etf.fund_details), "warnings": " | ".join(etf.warnings),
    }
    diagnostics = {"main_page": {}, "ajax": {}}
    for response in responses:
        if "_wicket" not in response.url:
            diagnostics["main_page_http_status"] = response.status_code
            for kind in ("countries", "sectors"):
                raw = page_allocation(response.text, kind)
                diagnostics["main_page"][kind] = raw
                try:
                    valid = _parse_justetf_allocation(raw)
                    metrics[f"main_page_{kind}"] = bool(named_count(valid))
                    metrics[f"main_page_{kind}_sum"] = round(sum(valid.values()), 6)
                except (ValueError, TypeError, KeyError) as exc:
                    metrics[f"main_page_{kind}"] = False
                    diagnostics[f"{kind}_validation_error"] = str(exc)
        else:
            for kind in ("countries", "sectors"):
                if f"{kind}-loadMore" in response.url:
                    diagnostics["ajax"][kind] = ajax_status(response)
                    metrics[f"ajax_{kind}_redirect"] = diagnostics["ajax"][kind]["redirect"]
                    if item["isin"] == "IE00B7K93397":
                        (output / f"sp500_ajax_{kind}.xml").write_text(response.text, encoding="utf-8")
    metrics["geography_with_main_page_fallback"] = metrics["justetf_geography"] or metrics.get("main_page_countries", False)
    metrics["sectors_with_main_page_fallback"] = metrics["justetf_sectors"] or metrics.get("main_page_sectors", False)
    return {"metrics": metrics, "etf": asdict(etf), "diagnostics": diagnostics, "provider_log": log.getvalue()}


def save_summary(results, output):
    metrics = pd.DataFrame([
        {**result["metrics"], "justetf_http_status": result["diagnostics"].get("main_page_http_status")}
        for result in results
    ])
    metrics.to_csv(output / "coverage.csv", index=False)
    sample = metrics[metrics["group"] == "top100"]
    fields = ("eur_valuation", "isin_returned_and_matched", "yahoo_sectors", "yahoo_holdings",
              "justetf_profile", "justetf_sectors", "justetf_geography", "justetf_holdings",
              "geography_with_main_page_fallback", "sectors_with_main_page_fallback")
    summary = {"tested": len(sample), "availability": {field: int(sample[field].sum()) for field in fields}}
    summary["geography_recoverable_from_main_page"] = int((~sample["justetf_geography"] & sample["main_page_countries"].fillna(False)).sum())
    summary["justetf_http_403"] = int((sample["justetf_http_status"] == 403).sum())
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=5.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    items = pd.read_csv(args.sample).to_dict("records")
    for item in items:
        item["group"] = "top100"
    if "IE00B7K93397" not in {item["isin"] for item in items}:
        items.append({"isin": "IE00B7K93397", "name": "UBS Core S&P 500 UCITS ETF USD dis", "group": "case-study"})
    result_path = args.output / "results.jsonl"
    results = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines()] if result_path.exists() else []
    completed = {result["metrics"]["isin"] for result in results}
    metadata = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample": str(args.sample), "selection": "100 largest long-only ETFs by reported fund size in justETF's EUR screener",
        "selection_source": "https://www.justetf.com/en/search.html?search=ETFS",
        "yfinance_version": version("yfinance"), "justetf_scraping_version": version("justetf-scraping"),
        "justetf_scraping_source": json.loads(distribution("justetf-scraping").read_text("direct_url.json") or "{}"),
        "fetch_code_sha256": hashlib.sha256(Path("src/external_services/data_fetch.py").read_bytes()).hexdigest(),
        "note": "One sequential pass, no audit retries. Uses the current fetcher, including its one-page fallback. Missing is not zero.",
    }
    (args.output / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="etf-coverage-") as cache:
        yf.set_tz_cache_location(cache)
        try:
            for item in items:
                if item["isin"] in completed:
                    continue
                result = check_etf(item, args.output)
                with result_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, default=lambda value: value.isoformat()) + "\n")
                results.append(result)
                summary = save_summary(results, args.output)
                m = result["metrics"]
                print(f"{len(results)}/{len(items)} {item['isin']} {m['ticker']} EUR={m['eur_valuation']} "
                      f"geography={m['justetf_geography']} page={m.get('main_page_countries', False)} "
                      f"({m['duration_seconds']}s)", flush=True)
                time.sleep(args.delay)
        finally:
            for manager in (yf.cache._TzDBManager, yf.cache._CookieDBManager, yf.cache._ISINDBManager):
                manager.close_db()
    metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (args.output / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(save_summary(results, args.output), indent=2), flush=True)


if __name__ == "__main__":
    main()
