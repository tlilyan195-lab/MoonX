"""ÉTAPE 5.1B — Trading Economics NEWS validation tests (no paid key)."""

from __future__ import annotations

from trading_signal_bot.data.news_provider_validation import (
    NEWS_CURRENCY_MAP,
    assess_fmp_economic_calendar,
    assess_news_providers_5_1b,
    assess_quantgist,
    assess_trading_economics,
)


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeSession:
    def get(self, url: str, params=None, timeout=30):  # noqa: ANN001
        if "guest:guest" in str(params) or (params or {}).get("c") == "guest:guest":
            return _Resp(410, "guest account has been discontinued")
        if "quantgist.com" in url and url.rstrip("/").endswith("/health"):
            return _Resp(200, '{"status":"ok"}')
        return _Resp(401, "authorization required")


def test_te_rejected_without_paid_access():
    te = assess_trading_economics(session=_FakeSession(), run_live_probe=True)
    assert te.verdict == "REJECT"
    assert te.usable_free_for_pilot is False
    assert te.usable_for_3y_without_paid is False
    assert te.probe["guest_status"] == 410
    assert "Date" in te.fields["publication_timestamp"]
    assert te.api_limits["calendar_rows_per_request"] == 1000


def test_quantgist_starter_covers_3y():
    qg = assess_quantgist()
    assert qg.plan_cost["starter_history_years"] == 3
    assert qg.plan_cost["free_history_years"] == 1
    assert qg.usable_for_3y_without_paid is False
    assert "currency" in qg.fields["currency_or_deducible"].lower()


def test_fmp_alternative_has_required_fields():
    fmp = assess_fmp_economic_calendar()
    assert "YES" in fmp.fields["publication_timestamp"]
    assert "YES" in fmp.fields["importance_impact"]
    assert fmp.usable_for_3y_without_paid is False


def test_full_5_1b_report_structure():
    report = assess_news_providers_5_1b(session=_FakeSession(), run_live_probe=True)
    assert report["trading_economics"]["verdict"] == "REJECT"
    assert len(report["alternatives"]) == 2
    assert report["recommended_v1"]["provider"] == "quantgist_economic_calendar_api"
    assert report["cdc_blackout_rules_modified"] is False
    assert report["ohlc_3y_downloaded"] is False
    assert report["news_currency_map"]["EURUSD"] == ["EUR", "USD"]
    assert report["news_currency_map"]["XAUUSD"] == ["USD"]
    assert "BTCUSDT" not in report["news_currency_map"]


def test_news_currency_map_cdc():
    assert NEWS_CURRENCY_MAP["GBPUSD"] == ("GBP", "USD")
    assert NEWS_CURRENCY_MAP["USDJPY"] == ("USD", "JPY")
