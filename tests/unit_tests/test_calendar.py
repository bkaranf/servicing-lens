from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mortgage_servicing_dashboard.calendar import (
    CalendarFreshnessState,
    EarningsCalendar,
    EarningsCalendarError,
    EdgarEarningsEventAdapter,
    IssuerIrEventAdapter,
    build_earnings_calendar_from_files,
    build_earnings_calendar_from_official_config,
    load_earnings_calendar_config,
)
from mortgage_servicing_dashboard.cli import main

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "config" / "calendar" / "earnings_calendar.v1.yaml"
_FIXTURES = _ROOT / "tests" / "fixtures" / "phase2" / "calendar"


def _payload(name: str) -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        json.loads((_FIXTURES / name).read_text(encoding="utf-8")),
    )


def test_calendar_keeps_actual_report_and_inferred_window_distinct() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    company = config.company("tfc")
    filings = EdgarEarningsEventAdapter().parse(
        (_FIXTURES / "tfc_edgar_earnings.json").read_bytes(),
        company=company,
    )
    issuer_events = IssuerIrEventAdapter().parse(
        (_FIXTURES / "tfc_ir_events.json").read_bytes(),
        company=company,
    )
    assert config.version == "earnings-calendar-1.0.0"
    assert company.cik == "0000092230"
    assert len(filings) == 6
    assert len(issuer_events) == 2
    assert all(not event.is_inferred for event in (*filings, *issuer_events))
    assert all(event.form == "8-K" and event.exhibit_type.startswith("EX-99") for event in filings)

    as_of = datetime(2026, 8, 12, 12, tzinfo=UTC)
    result = EarningsCalendar(config).build(
        company_id="tfc",
        as_of=as_of,
        filing_events=filings,
        issuer_events=issuer_events,
    )
    assert result.as_of == as_of
    assert result.last_reported_period.period_end == date(2026, 6, 30)
    assert result.last_reported_period.accession == "0000092230-26-000096"
    assert result.last_reported_period.is_inferred is False
    assert result.inferred_window.expected_period_end == date(2026, 9, 30)
    assert result.window_start == date(2026, 10, 16)
    assert result.window_end == date(2026, 10, 20)
    assert result.is_inferred is True
    assert result.inferred_window.is_inferred is True
    assert result.inference_basis == (
        "edgar:0000092230-23-000101",
        "edgar:0000092230-24-000102",
        "edgar:0000092230-25-000103",
    )
    assert result.freshness_state is CalendarFreshnessState.NOT_YET_EXPECTED
    assert result.next_announced_event is not None
    assert result.next_announced_event.event_id == "ir:tfc:2026q3-earnings-call"

    without_ir = EarningsCalendar(config).build(
        company_id="tfc",
        as_of=as_of,
        filing_events=filings,
    )
    assert without_ir.inferred_window == result.inferred_window
    assert without_ir.next_announced_event is None


def test_calendar_freshness_includes_awaiting_expected_filing() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    company = config.company("tfc")
    filings = EdgarEarningsEventAdapter().parse(
        (_FIXTURES / "tfc_edgar_earnings.json").read_bytes(),
        company=company,
    )
    calendar = EarningsCalendar(config)
    within = calendar.build(
        company_id="tfc",
        as_of=datetime(2026, 10, 18, 12, tzinfo=UTC),
        filing_events=filings,
    )
    assert within.freshness_state is CalendarFreshnessState.WITHIN_EXPECTED_WINDOW
    awaiting = calendar.build(
        company_id="tfc",
        as_of=datetime(2026, 10, 21, 0, 0, tzinfo=UTC),
        filing_events=filings,
    )
    assert awaiting.freshness_state is CalendarFreshnessState.AWAITING_EXPECTED_FILING
    assert awaiting.last_reported_period.period_end == date(2026, 6, 30)
    assert awaiting.window_end == date(2026, 10, 20)


@pytest.mark.parametrize(
    ("company_id", "window_start", "window_end", "announced_event_id"),
    [
        (
            "tfc",
            date(2026, 10, 16),
            date(2026, 10, 20),
            "ir:tfc:2026q3-earnings-call",
        ),
        (
            "pfsi",
            date(2026, 10, 21),
            date(2026, 10, 26),
            "ir:pfsi:2026q3-earnings-call",
        ),
    ],
)
def test_file_backed_service_populates_both_stage_a_issuers(
    company_id: str,
    window_start: date,
    window_end: date,
    announced_event_id: str,
) -> None:
    result = build_earnings_calendar_from_files(
        config_path=_CONFIG,
        company_id=company_id,
        edgar_input_path=_FIXTURES / f"{company_id}_edgar_earnings.json",
        issuer_ir_input_path=_FIXTURES / f"{company_id}_ir_events.json",
        as_of=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    assert result.last_reported_period.period_end == date(2026, 6, 30)
    assert result.last_reported_period.is_inferred is False
    assert result.inferred_window.expected_period_end == date(2026, 9, 30)
    assert result.inferred_window.is_inferred is True
    assert result.window_start == window_start
    assert result.window_end == window_end
    assert result.freshness_state is CalendarFreshnessState.NOT_YET_EXPECTED
    assert result.next_announced_event is not None
    assert result.next_announced_event.event_id == announced_event_id
    assert all(event_id.startswith("edgar:") for event_id in result.inference_basis)


@pytest.mark.parametrize(
    ("company_id", "window_start", "window_end"),
    [
        ("tfc", date(2026, 10, 16), date(2026, 10, 20)),
        ("pfsi", date(2026, 10, 20), date(2026, 10, 27)),
    ],
)
def test_official_config_populates_two_issuer_offline_calendar(
    company_id: str,
    window_start: date,
    window_end: date,
) -> None:
    result = build_earnings_calendar_from_official_config(
        config_path=_CONFIG,
        company_id=company_id,
        as_of=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    assert result.last_reported_period.period_end == date(2026, 6, 30)
    assert result.last_reported_period.is_inferred is False
    assert result.last_reported_period.exhibit_url.startswith(
        "https://www.sec.gov/Archives/edgar/data/"
    )
    assert result.inferred_window.is_inferred is True
    assert result.window_start == window_start
    assert result.window_end == window_end
    assert result.freshness_state is CalendarFreshnessState.NOT_YET_EXPECTED


def test_calendar_cli_labels_actual_and_inferred_fields(
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
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    rows = payload["calendar"]
    assert {item["ticker"] for item in rows} == {"TFC", "PFSI"}
    assert all(item["last_reported_period"]["is_inferred"] is False for item in rows)
    assert all(item["next_expected_report_window"]["is_inferred"] is True for item in rows)


def test_calendar_inference_fails_closed_without_eligible_history() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    company = config.company("tfc")
    filings = EdgarEarningsEventAdapter().parse(
        (_FIXTURES / "tfc_edgar_earnings.json").read_bytes(),
        company=company,
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
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
            filing_events=(),
        )
    with pytest.raises(EarningsCalendarError, match="insufficient matching-quarter"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
            filing_events=filings[-3:],
        )
    bad_lag = tuple(
        replace(event, accepted_at=datetime(2022, 1, 1, tzinfo=UTC))
        if event.period_end == date(2023, 9, 30)
        else event
        for event in filings
    )
    with pytest.raises(EarningsCalendarError, match="outside the configured"):
        calendar.build(
            company_id="tfc",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
            filing_events=bad_lag,
        )
    with pytest.raises(EarningsCalendarError, match="not uniquely configured"):
        calendar.build(
            company_id="missing",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
            filing_events=filings,
        )


def test_edgar_adapter_requires_matching_cik_source_and_one_ex99() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    company = config.company("tfc")
    adapter = EdgarEarningsEventAdapter()
    wrong_cik = _payload("tfc_edgar_earnings.json")
    wrong_cik["cik"] = "1"
    with pytest.raises(EarningsCalendarError, match="CIK does not match"):
        adapter.parse(json.dumps(wrong_cik).encode(), company=company)
    wrong_source = _payload("tfc_edgar_earnings.json")
    wrong_source["source_url"] = "https://data.sec.gov/submissions/CIK0000000001.json"
    with pytest.raises(EarningsCalendarError, match="does not match calendar"):
        adapter.parse(json.dumps(wrong_source).encode(), company=company)
    missing_exhibit = _payload("tfc_edgar_earnings.json")
    filing = cast("dict[str, Any]", cast("list[Any]", missing_exhibit["filings"])[0])
    filing["documents"] = [
        {
            "type": "8-K",
            "url": "https://www.sec.gov/Archives/edgar/data/92230/form8k.htm",
        }
    ]
    with pytest.raises(EarningsCalendarError, match="exactly one EX-99"):
        adapter.parse(json.dumps(missing_exhibit).encode(), company=company)
    duplicate = _payload("tfc_edgar_earnings.json")
    filings = cast("list[dict[str, Any]]", duplicate["filings"])
    filings[1]["accession"] = filings[0]["accession"]
    with pytest.raises(EarningsCalendarError, match="duplicate event IDs"):
        adapter.parse(json.dumps(duplicate).encode(), company=company)
    with pytest.raises(EarningsCalendarError, match="valid UTF-8 JSON"):
        adapter.parse(b"not-json", company=company)


def test_issuer_ir_adapter_enforces_official_host_time_and_identity() -> None:
    config = load_earnings_calendar_config(_CONFIG)
    company = config.company("tfc")
    adapter = IssuerIrEventAdapter()
    wrong_company = _payload("tfc_ir_events.json")
    wrong_company["company_id"] = "pfsi"
    with pytest.raises(EarningsCalendarError, match="company does not match"):
        adapter.parse(json.dumps(wrong_company).encode(), company=company)
    wrong_host = _payload("tfc_ir_events.json")
    wrong_host["source_url"] = "https://example.test/events"
    with pytest.raises(EarningsCalendarError, match="configured official IR"):
        adapter.parse(json.dumps(wrong_host).encode(), company=company)
    bad_time = _payload("tfc_ir_events.json")
    first = cast("dict[str, Any]", cast("list[Any]", bad_time["events"])[0])
    first["announced_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(EarningsCalendarError, match="announced after"):
        adapter.parse(json.dumps(bad_time).encode(), company=company)
    duplicate = _payload("tfc_ir_events.json")
    events = cast("list[dict[str, Any]]", duplicate["events"])
    events[1]["event_id"] = events[0]["event_id"]
    with pytest.raises(EarningsCalendarError, match="duplicate event IDs"):
        adapter.parse(json.dumps(duplicate).encode(), company=company)

    wrong_source_path = _payload("tfc_ir_events.json")
    wrong_source_path["source_url"] = "https://ir.truist.com/other-events"
    with pytest.raises(EarningsCalendarError, match="does not match calendar"):
        adapter.parse(json.dumps(wrong_source_path).encode(), company=company)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"cik": "not-a-cik"}), "CIK must contain"),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["filings"])[0].update(
                {"accession": "bad"}
            ),
            "accession has an invalid",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["filings"])[0].update(
                {"reported_period_end": "not-a-date"}
            ),
            "must be an ISO date",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["filings"])[0].update(
                {"accepted_at": "not-a-datetime"}
            ),
            "must be an ISO datetime",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["filings"])[0].update(
                {"accepted_at": "2026-01-01T00:00:00"}
            ),
            "must be timezone-aware",
        ),
        (lambda payload: payload.update({"filings": {}}), "filings must be a list"),
        (
            lambda payload: payload.update({"source_url": "http://data.sec.gov/input.json"}),
            "official SEC HTTPS host",
        ),
    ],
)
def test_edgar_adapter_rejects_malformed_controlled_fields(
    mutation: Any,
    message: str,
) -> None:
    config = load_earnings_calendar_config(_CONFIG)
    payload = _payload("tfc_edgar_earnings.json")
    mutation(payload)
    with pytest.raises(EarningsCalendarError, match=message):
        EdgarEarningsEventAdapter().parse(
            json.dumps(payload).encode(), company=config.company("tfc")
        )


def test_calendar_configuration_validation(tmp_path: Path) -> None:
    with pytest.raises(EarningsCalendarError, match="could not be loaded"):
        load_earnings_calendar_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(EarningsCalendarError, match="must be a mapping"):
        load_earnings_calendar_config(invalid)
    bad_policy = tmp_path / "bad-policy.yaml"
    bad_policy.write_text(
        _CONFIG.read_text(encoding="utf-8").replace(
            "minimum_matching_periods: 2",
            "minimum_matching_periods: 4",
        ),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="cannot exceed"):
        load_earnings_calendar_config(bad_policy)
    bad_quarters = tmp_path / "bad-quarters.yaml"
    bad_quarters.write_text(
        _CONFIG.read_text(encoding="utf-8").replace(
            '["03-31", "06-30", "09-30", "12-31"]',
            '["03-31"]',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="four unique"):
        load_earnings_calendar_config(bad_quarters)
    with pytest.raises(EarningsCalendarError, match="input file could not be read"):
        build_earnings_calendar_from_files(
            config_path=_CONFIG,
            company_id="tfc",
            edgar_input_path=tmp_path / "missing.json",
            as_of=datetime(2026, 8, 12, tzinfo=UTC),
        )

    duplicate_company = tmp_path / "duplicate-company.yaml"
    duplicate_company.write_text(
        _CONFIG.read_text(encoding="utf-8").replace("company_id: pfsi", "company_id: tfc"),
        encoding="utf-8",
    )
    with pytest.raises(EarningsCalendarError, match="duplicate company IDs"):
        load_earnings_calendar_config(duplicate_company)

    for name, old, new, message in (
        (
            "bad-lookback",
            "lookback_matching_periods: 3",
            "lookback_matching_periods: 0",
            "positive",
        ),
        ("bad-padding", "padding_days: 1", "padding_days: -1", "nonnegative"),
        ("bad-month", '"03-31"', '"13-31"', "MM-DD"),
        (
            "bad-hosts",
            'investor_relations_hosts: ["ir.truist.com"]',
            "investor_relations_hosts: []",
            "nonempty list",
        ),
    ):
        path = tmp_path / f"{name}.yaml"
        path.write_text(_CONFIG.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
        with pytest.raises(EarningsCalendarError, match=message):
            load_earnings_calendar_config(path)
