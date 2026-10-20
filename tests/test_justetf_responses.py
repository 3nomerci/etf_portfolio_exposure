"""Exercise saved HTTP responses and the application's small justETF fallback."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import justetf_scraping.etf_profile as profile

from scripts.check_provider_coverage import ajax_status, page_allocation
from src.external_services.data_fetch import _justetf_overview

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = [
    {"name": "United States", "percentage": 96.71},
    {"name": "Ireland", "percentage": 1.14},
    {"name": "Other", "percentage": 2.15},
]


def test_s_and_p_countries_exist_on_the_main_page():
    assert page_allocation((FIXTURES / "sp500_countries.html").read_text(), "countries") == EXPECTED


def test_http_200_access_denied_is_not_a_country_dataset():
    text = (FIXTURES / "justetf_access_denied.xml").read_text()
    response = SimpleNamespace(status_code=200, text=text, headers={"content-type": "text/xml"})
    assert ajax_status(response)["redirect"] == "/en/access-denied.html"
    assert profile._parse_allocation_from_ajax(
        text, "etf-holdings_countries_table", "tl_etf-holdings_countries_value_name",
        "tl_etf-holdings_countries_value_percentage",
    ) == []


@pytest.mark.parametrize("country_response", ["denied", "empty", "unrelated"])
def test_country_data_survives_unavailable_expansion(monkeypatch, country_response):
    page = (FIXTURES / "sp500_countries.html").read_text()
    denied = (FIXTURES / "justetf_access_denied.xml").read_text()
    responses = {"denied": denied, "empty": "<ajax-response/>",
                 "unrelated": '<ajax-response><component id="other">No country table</component></ajax-response>'}
    calls = []

    def get(session, url, **kwargs):
        calls.append(url)
        if "countries-loadMoreCountries" in url:
            text = responses[country_response]
        elif "_wicket" in url:
            text = "<ajax-response/>"
        else:
            text = page
        return SimpleNamespace(status_code=200, text=text)

    monkeypatch.setattr(requests.Session, "get", get)
    result, fallbacks, warnings = _justetf_overview("IE00B7K93397")
    assert result["countries"] == EXPECTED
    assert fallbacks == ("countries",)
    assert warnings == []
    assert sum("_wicket" not in url for url in calls) == 2  # Initial page and one fallback page.


def test_valid_expanded_lists_do_not_trigger_fallback(monkeypatch):
    import justetf_scraping
    expanded = {"countries": [{"name": f"Country {i}", "percentage": 10} for i in range(10)],
                "sectors": [{"name": "Technology", "percentage": 100}]}
    calls = []

    def overview(isin, **kwargs):
        calls.append(kwargs["expand_allocations"])
        return expanded

    monkeypatch.setattr(justetf_scraping, "get_etf_overview", overview)
    result, fallbacks, warnings = _justetf_overview("IE00B7K93397")
    assert result == expanded and len(result["countries"]) == 10
    assert not fallbacks and not warnings and calls == [True]


def test_both_lists_recover_with_one_fallback_and_keep_original_holdings(monkeypatch):
    import justetf_scraping
    expanded = {"countries": [], "sectors": [], "top_holdings": [{"name": "Original", "percentage": 10}]}
    page = {"countries": EXPECTED, "sectors": [{"name": "Technology", "percentage": 100}],
            "top_holdings": [{"name": "Different", "percentage": 20}]}
    calls = []

    def overview(isin, **kwargs):
        calls.append(kwargs["expand_allocations"])
        return expanded if kwargs["expand_allocations"] else page

    monkeypatch.setattr(justetf_scraping, "get_etf_overview", overview)
    result, fallbacks, warnings = _justetf_overview("IE00B7K93397")
    assert result["countries"] == EXPECTED and result["sectors"] == page["sectors"]
    assert result["top_holdings"][0]["name"] == "Original"
    assert fallbacks == ("countries", "sectors") and not warnings and calls == [True, False]


@pytest.mark.parametrize("failure", ["exception", "empty"])
def test_failed_fallback_preserves_successful_fields(monkeypatch, failure):
    import justetf_scraping
    expanded = {"countries": [], "sectors": [{"name": "Technology", "percentage": 100}],
                "top_holdings": [{"name": "Example", "percentage": 10}]}
    calls = []

    def overview(isin, **kwargs):
        calls.append(kwargs["expand_allocations"])
        if kwargs["expand_allocations"]:
            return expanded
        if failure == "exception":
            raise ConnectionError("HTTP 403")
        return None

    monkeypatch.setattr(justetf_scraping, "get_etf_overview", overview)
    result, fallbacks, warnings = _justetf_overview("IE00B7K93397")
    assert result == expanded and result["top_holdings"] and result["sectors"]
    assert not fallbacks and warnings and calls == [True, False]


def test_blocked_initial_page_is_not_retried(monkeypatch):
    import justetf_scraping
    calls = []

    def overview(isin, **kwargs):
        calls.append(kwargs["expand_allocations"])
        raise ConnectionError("HTTP 403")

    monkeypatch.setattr(justetf_scraping, "get_etf_overview", overview)
    with pytest.raises(ConnectionError, match="403"):
        _justetf_overview("IE00B7K93397")
    assert calls == [True]
