from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mortgage_servicing_dashboard.calendar import (
    CalendarFreshnessState,
    EarningsCalendar,
    EarningsCalendarError,
    FilingEarningsEvent,
    build_earnings_calendar_from_official_config,
    load_earnings_calendar_config,
)
from mortgage_servicing_dashboard.cli import main

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "config" / "calendar" / "earnings_calendar.v1.yaml"


@pytest.mark.parametrize(
    ("company_id", "window_start", "window_end", "accession"),
    [
        ("tfc", date(2026, 10, 16), date(2026, 10, 20), "0000092230-26-000096"),
        ("pfsi", date(2026, 10, 20), date(2026, 10, 27), "0001104659-26-088174"),
    ],
)
def test_official_sec_history_keeps_actual_and_inferred_calendar_state_distinct(
    company_id: str,
    window_start: date,
    window_end: date,
    accession: str,
) -> None:
    result = build_earnings_calendar_from_official_config(
        config_path=_CONFIG,
        company_id=company_id,
        as_of=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )

    assert result.last_reported_period.period_end == date(2026, 6, 30)
    assert result.last_reported_period.accession == accession
    assert result.last_reported_period.is_inferred is False
    assert result.last_reported_period.filing_url.endswith(f"{accession}-index.html")
    assert result.last_reported_period.exhibit_url.startswith(
        "https://www.sec.gov/Archives/edgar/data/"
    )
    assert result.inferred_window.expected_period_end == date(2026, 9, 30)
    assert result.inferred_window.is_inferred is True
    assert result.window_start == window_start
    assert result.window_end == window_end
    assert result.freshness_state is CalendarFreshnessState.NOT_YET_EXPECTED
    assert result.next_announced_event is None
    assert all(event_id.startswith("edgar:") for event_id in result.inference_basis)


def test_calendar_freshness_includes_awaiting_expected_filing() -> None:
    within = build_earnings_calendar_from_official_config(
        config_path=_CONFIG,
        company_id="tfc",
        as_of=datetime(2026, 10, 18, 12, tzinfo=UTC),
    )
    awaiting = build_earnings_calendar_from_official_config(
        config_path=_CONFIG,
        company_id="tfc",
        as_of=datetime(2026, 10, 21, 0, 0, tzinfo=UTC),
    )

    assert within.freshness_state is CalendarFreshnessState.WITHIN_EXPECTED_WINDOW
    assert awaiting.freshness_state is CalendarFreshnessState.AWAITING_EXPECTED_FILING
    assert awaiting.last_reported_period.period_end == date(2026, 6, 30)


def test_calendar_cli_rejects_missing_database_without_creating_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'calendar-cli.db').as_posix()}"

    assert (
        main(
            [
                "calendar",
                "--database-url",
                database_url,
                "--as-of",
                "2026-08-12T12:00:00+00:00",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "database_not_found"
    assert not (tmp_path / "calendar-cli.db").exists()


def _event(year: int, *, accepted_day: int, suffix: int) -> FilingEarningsEvent:
    accession = f"0000092230-{year % 100:02d}-{suffix:06d}"
    return FilingEarningsEvent(
        event_id=f"edgar:{accession}",
        company_id="tfc",
        cik="0000092230",
        accession=accession,
        period_end=date(year, 9, 30),
        accepted_at=datetime(year, 10, accepted_day, tzinfo=UTC),
        form="8-K",
        exhibit_type="EX-99.1",
        filing_url=f"https://www.sec.gov/Archives/edgar/data/92230/{accession}-index.html",
        exhibit_url="https://www.sec.gov/Archives/edgar/data/92230/exhibit.htm",
    )


def test_calendar_inference_fails_closed_on_invalid_history() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    filings = (
        _event(2023, accepted_day=19, suffix=77),
        _event(2024, accepted_day=17, suffix=71),
        _event(2025, accepted_day=17, suffix=150),
        _event(2026, accepted_day=17, suffix=96),
    )
    calendar = EarningsCalendar(config)

    with pytest.raises(EarningsCalendarError, match="timezone-aware"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2026, 8, 12),  # noqa: DTZ001 - intentional rejection case
            filing_events=filings,
        )
    with pytest.raises(EarningsCalendarError, match="at least one known"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2022, 8, 12, tzinfo=UTC),
            filing_events=filings,
        )
    with pytest.raises(EarningsCalendarError, match="insufficient matching-quarter"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2026, 12, 1, tzinfo=UTC),
            filing_events=filings[-1:],
        )
    invalid_lag = (
        replace(filings[0], accepted_at=datetime(2022, 1, 1, tzinfo=UTC)),
        *filings[1:3],
        replace(
            filings[3],
            period_end=date(2026, 6, 30),
            accepted_at=datetime(2026, 7, 17, tzinfo=UTC),
        ),
    )
    with pytest.raises(EarningsCalendarError, match="outside the configured"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
            filing_events=invalid_lag,
        )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("minimum_matching_periods: 2", "minimum_matching_periods: 4", "cannot exceed"),
        ("lookback_matching_periods: 3", "lookback_matching_periods: 0", "positive"),
        ("padding_days: 1", "padding_days: -1", "nonnegative"),
        ('["03-31", "06-30", "09-30", "12-31"]', '["03-31"]', "four unique"),
        ('"03-31"', '"13-31"', "MM-DD"),
    ],
)
def test_calendar_configuration_validation(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(_CONFIG.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")

    with pytest.raises(EarningsCalendarError, match=message):
        load_earnings_calendar_config(path)


def test_official_history_rejects_duplicate_events_and_non_sec_urls(tmp_path: Path) -> None:
    original = _CONFIG.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        original.replace("0000092230-24-000071", "0000092230-23-000077"),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="duplicate event IDs"):
        build_earnings_calendar_from_official_config(
            config_path=duplicate,
            company_id="tfc",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
        )

    wrong_host = tmp_path / "wrong-host.yaml"
    wrong_host.write_text(
        original.replace("https://www.sec.gov", "https://example.test", 1),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="official SEC HTTPS host"):
        build_earnings_calendar_from_official_config(
            config_path=wrong_host,
            company_id="tfc",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
        )


def test_calendar_configuration_load_errors_are_safe(tmp_path: Path) -> None:
    with pytest.raises(EarningsCalendarError, match="could not be loaded"):
        load_earnings_calendar_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(EarningsCalendarError, match="must be a mapping"):
        load_earnings_calendar_config(invalid)

    duplicate_company = tmp_path / "duplicate-company.yaml"
    duplicate_company.write_text(
        _CONFIG.read_text(encoding="utf-8").replace("company_id: pfsi", "company_id: tfc"),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="duplicate company IDs"):
        load_earnings_calendar_config(duplicate_company)
