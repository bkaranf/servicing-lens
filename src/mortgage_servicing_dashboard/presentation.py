"""Typed, exact view models for the Servicing Lens presentation surface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict
from urllib.parse import quote

from mortgage_servicing_dashboard.repository import ObservationRecord


class CompanyIdentity(TypedDict):
    """Minimum governed company identity required by the presentation layer."""

    id: str
    legal_name: str
    ticker: str
    classification: str


class EarningsIdentity(TypedDict):
    """Governed earnings-event fields consumed by the presentation layer."""

    id: str
    company_id: str
    ticker: str
    fiscal_year: int
    fiscal_quarter: int
    event_at: str
    evidence_id: str
    source_url: str


@dataclass(frozen=True, slots=True)
class MetricInput:
    """One exact observation input behind a reported or derived presentation value."""

    observation_id: str
    evidence_id: str | None
    locator_url: str | None
    label: str
    display: str
    period_label: str


@dataclass(frozen=True, slots=True)
class PresentationMetric:
    """One deterministic or explicitly unavailable presentation metric."""

    key: str
    label: str
    value: Decimal | None
    display: str
    status: str
    note: str
    inputs: tuple[MetricInput, ...] = ()


@dataclass(frozen=True, slots=True)
class CompanyPresentation:
    """One issuer card assembled solely from governed observations."""

    id: str
    legal_name: str
    ticker: str
    classification: str
    platform: str | None
    period_label: str
    period_end: str | None
    upb: PresentationMetric
    customer_loans: PresentationMetric
    growth: PresentationMetric
    owned_mix: PresentationMetric


@dataclass(frozen=True, slots=True)
class EarningsPresentation:
    """A deterministic earnings brief backed by the existing event pipeline."""

    company_id: str
    ticker: str
    legal_name: str
    platform: str | None
    reporting_period: str
    earnings_date: str
    read: str
    read_status: str
    headline: str
    summary: str
    signals: tuple[PresentationMetric, ...]
    source_url: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class ScaleAssessment:
    """Authoritative permission to render a cross-company relative scale."""

    status: str
    reasons: tuple[str, ...]

    @property
    def available(self) -> bool:
        """Return whether governed comparability permits relative-scale ranking."""
        return self.status == "comparable"


_HUNDRED = Decimal(100)
_BILLION = Decimal(1_000_000_000)


def _quantized(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _format_percent(value: Decimal) -> str:
    rounded = _quantized(value, "0.1")
    prefix = "+" if rounded > 0 else ""
    return f"{prefix}{rounded}%"


def _format_upb(value: Decimal) -> str:
    billions = value / _BILLION
    if billions >= Decimal(1000):
        return f"${_quantized(billions / Decimal(1000), '0.1')}T"
    decimals = "0" if billions == billions.to_integral() else "0.1"
    return f"${_quantized(billions, decimals)}B"


def _format_count(value: Decimal) -> str:
    millions = value / Decimal(1_000_000)
    return f"{_quantized(millions, '0.1')}M"


def _format_money_millions(value: Decimal) -> str:
    return f"${_quantized(value / Decimal(1_000_000), '0')}M"


def _to_base_units(row: ObservationRecord) -> Decimal | None:
    return Decimal(row.value) if row.value is not None else None


def fiscal_period_label(*, fiscal_year: int, fiscal_quarter: int) -> str:
    """Render the repository's zero sentinel as an annual fiscal period."""
    if fiscal_quarter == 0:
        return f"FY {fiscal_year}"
    return f"Q{fiscal_quarter} {fiscal_year}"


def _period_label(row: ObservationRecord) -> str:
    return fiscal_period_label(
        fiscal_year=row.fiscal_year,
        fiscal_quarter=row.fiscal_quarter,
    )


def _locator_url(row: ObservationRecord) -> str | None:
    if row.evidence_id is None:
        return None
    evidence_id = quote(row.evidence_id, safe="")
    observation_id = quote(row.id, safe="")
    return f"/evidence/{evidence_id}/observations/{observation_id}#cited-source-locator"


def _metric_input(row: ObservationRecord) -> MetricInput:
    return MetricInput(
        observation_id=row.id,
        evidence_id=row.evidence_id,
        locator_url=_locator_url(row),
        label=row.metric_name,
        display=row.reported_value,
        period_label=_period_label(row),
    )


def _unavailable(key: str, label: str, note: str) -> PresentationMetric:
    return PresentationMetric(key, label, None, "Unavailable", "unavailable", note)


def _find_row(
    rows: list[ObservationRecord],
    metric_id: str,
    *,
    period_end: str | None,
) -> ObservationRecord | None:
    candidates = [
        row
        for row in rows
        if row.metric_id == metric_id
        and row.value is not None
        and (period_end is None or row.period_end == period_end)
    ]
    return max(candidates, key=lambda row: row.period_end, default=None)


def _reported_metric(
    *,
    key: str,
    label: str,
    row: ObservationRecord | None,
    formatter: Callable[[Decimal], str],
    unavailable_note: str,
) -> PresentationMetric:
    value = _to_base_units(row) if row is not None else None
    if row is None or value is None:
        return _unavailable(key, label, unavailable_note)
    return PresentationMetric(
        key,
        label,
        value,
        formatter(value),
        "reported",
        "Reported by issuer",
        (_metric_input(row),),
    )


def _quarter_index(row: ObservationRecord) -> int:
    return row.fiscal_year * 4 + row.fiscal_quarter


def _growth_semantics_align(current: ObservationRecord, prior: ObservationRecord) -> bool:
    return (
        current.metric_id == prior.metric_id
        and current.metric_version == prior.metric_version
        and current.reporting_entity_id == prior.reporting_entity_id
        and current.reporting_scope_id == prior.reporting_scope_id
        and current.methodology == prior.methodology
        and current.currency == prior.currency
        and current.unit == prior.unit
        and _quarter_index(current) - _quarter_index(prior) == 1
    )


def _growth_metric(
    rows: list[ObservationRecord],
    current: ObservationRecord | None,
) -> PresentationMetric:
    if current is None:
        return _unavailable(
            "growth",
            "UPB growth",
            "A current reported servicing UPB observation is required",
        )
    prior_candidates = sorted(
        (
            row
            for row in rows
            if row.metric_id == current.metric_id
            and row.value is not None
            and row.period_end < current.period_end
        ),
        key=lambda row: row.period_end,
    )
    if not prior_candidates:
        return _unavailable(
            "growth",
            "UPB growth",
            "An adjacent comparable prior quarter is required",
        )
    prior = prior_candidates[-1]
    if not _growth_semantics_align(current, prior):
        return _unavailable(
            "growth",
            "UPB growth",
            "The latest prior observation is not an adjacent quarter with matching semantics",
        )
    prior_value = _to_base_units(prior)
    current_value = _to_base_units(current)
    if prior_value is None or prior_value == 0 or current_value is None:
        return _unavailable(
            "growth",
            "UPB growth",
            "A non-zero comparable prior quarter is required",
        )
    growth = (current_value - prior_value) / prior_value * _HUNDRED
    return PresentationMetric(
        "growth",
        "UPB growth",
        growth,
        _format_percent(growth),
        "derived",
        f"Derived QoQ from {_period_label(prior)}; exact Decimal",
        (_metric_input(current), _metric_input(prior)),
    )


def _mix_semantics_align(total: ObservationRecord, numerator: ObservationRecord) -> bool:
    return (
        total.period_end == numerator.period_end
        and total.fiscal_year == numerator.fiscal_year
        and total.fiscal_quarter == numerator.fiscal_quarter
        and total.reporting_entity_id == numerator.reporting_entity_id
        and total.reporting_scope_id == numerator.reporting_scope_id
        and total.currency == numerator.currency
        and total.unit == numerator.unit
        and total.period_type == numerator.period_type
    )


def _mix_metric(  # noqa: PLR0911
    classification: str,
    rows: list[ObservationRecord],
    total: ObservationRecord | None,
) -> PresentationMetric:
    contract = {
        "bank": (
            "Bank-owned share",
            "bank_owned_loans_serviced_upb",
            "Bank-owned loans serviced / total UPB; not an owned-MSR measure",
        ),
        "nonbank": (
            "Owned MSR mix",
            "owned_msr_upb",
            "Owned MSR UPB / total UPB; exact Decimal",
        ),
    }.get(classification.casefold())
    if contract is None:
        return _unavailable(
            "owned_mix",
            "Ownership mix",
            "A governed bank or nonbank classification is required",
        )
    label, numerator_id, note = contract
    if total is None:
        return _unavailable("owned_mix", label, "A current total UPB denominator is required")
    if total.metric_id != "total_servicing_upb":
        return _unavailable(
            "owned_mix",
            label,
            "The selected servicing exposure is not a total-UPB denominator",
        )
    numerator = _find_row(rows, numerator_id, period_end=total.period_end)
    if numerator is None:
        return _unavailable(
            "owned_mix",
            label,
            f"A governed {total.period_end} ownership numerator is not disclosed",
        )
    if not _mix_semantics_align(total, numerator):
        return _unavailable(
            "owned_mix",
            label,
            "Numerator and denominator do not share compatible period, entity, and scope semantics",
        )
    total_value = _to_base_units(total)
    numerator_value = _to_base_units(numerator)
    if total_value is None or total_value == 0 or numerator_value is None:
        return _unavailable("owned_mix", label, "A non-zero compatible denominator is required")
    mix = numerator_value / total_value * _HUNDRED
    return PresentationMetric(
        "owned_mix",
        label,
        mix,
        _format_percent(mix).lstrip("+"),
        "derived",
        note,
        (_metric_input(numerator), _metric_input(total)),
    )


def _normalize_company(
    company: CompanyIdentity,
    rows: list[ObservationRecord],
    *,
    target_period: str | None,
) -> CompanyPresentation:
    upb_row = next(
        (
            row
            for metric_id in (
                "total_servicing_upb",
                "servicing_for_others_upb",
                "owned_msr_upb",
            )
            if (row := _find_row(rows, metric_id, period_end=target_period)) is not None
        ),
        None,
    )
    effective_period = target_period or (upb_row.period_end if upb_row else None)
    loan_row = _find_row(rows, "servicing_loan_count", period_end=effective_period)
    return CompanyPresentation(
        id=company["id"],
        legal_name=company["legal_name"],
        ticker=company["ticker"],
        classification=company["classification"],
        platform=None,
        period_label=_period_label(upb_row) if upb_row else "Unavailable",
        period_end=upb_row.period_end if upb_row else effective_period,
        upb=_reported_metric(
            key="upb",
            label=upb_row.metric_name if upb_row else "Servicing UPB",
            row=upb_row,
            formatter=_format_upb,
            unavailable_note=(
                "No configured servicing portfolio metric is disclosed for the selected period"
            ),
        ),
        customer_loans=_reported_metric(
            key="loans",
            label="Customer loans",
            row=loan_row,
            formatter=_format_count,
            unavailable_note="Servicing loan count is not disclosed in the governed dataset",
        ),
        growth=_growth_metric(rows, upb_row),
        owned_mix=_mix_metric(company["classification"], rows, upb_row),
    )


def normalize_companies(
    companies: list[CompanyIdentity],
    observations: list[ObservationRecord],
    *,
    target_periods: Mapping[str, str] | None = None,
) -> list[CompanyPresentation]:
    """Normalize governed identities and observations into exact company cards.

    Args:
        companies: Governed live company identities.
        observations: Published observations, including explicit missing states.
        target_periods: Optional exact period end per company; other companies use latest UPB.

    Returns:
        Presentation cards without invented platform, loan-count, or ownership values.
    """
    result: list[CompanyPresentation] = []
    for company in companies:
        company_rows = [row for row in observations if row.company_id == company["id"]]
        result.append(
            _normalize_company(
                company,
                company_rows,
                target_period=target_periods.get(company["id"]) if target_periods else None,
            )
        )
    return result


def _event_period_end(
    event: EarningsIdentity,
    company_rows: list[ObservationRecord],
) -> str | None:
    periods = {
        row.period_end
        for row in company_rows
        if row.fiscal_year == event["fiscal_year"] and row.fiscal_quarter == event["fiscal_quarter"]
    }
    return max(periods, default=None)


def normalize_earnings(
    companies: list[CompanyIdentity],
    observations: list[ObservationRecord],
    events: list[EarningsIdentity],
) -> list[EarningsPresentation]:
    """Build latest event briefs using only observations from each event period.

    Args:
        companies: Governed live company identities.
        observations: Published observations used for deterministic signals.
        events: Earnings events produced by the existing ingestion pipeline.

    Returns:
        Latest event-backed brief for each issuer, fail-closed on period gaps.
    """
    company_by_id = {company["id"]: company for company in companies}
    latest_events: dict[str, EarningsIdentity] = {}
    for event in events:
        current = latest_events.get(event["company_id"])
        if current is None or event["event_at"] > current["event_at"]:
            latest_events[event["company_id"]] = event
    briefs: list[EarningsPresentation] = []
    for company_id, event in latest_events.items():
        company = company_by_id.get(company_id)
        if company is None:
            continue
        company_rows = [row for row in observations if row.company_id == company_id]
        target_period = _event_period_end(event, company_rows)
        event_rows = company_rows if target_period is not None else []
        event_card = _normalize_company(company, event_rows, target_period=target_period)
        income = (
            _find_row(company_rows, "servicing_pretax_income", period_end=target_period)
            or _find_row(company_rows, "servicing_revenue", period_end=target_period)
            if target_period is not None
            else None
        )
        income_metric = _reported_metric(
            key="earnings_signal",
            label=income.metric_name if income else "Servicing earnings signal",
            row=income,
            formatter=_format_money_millions,
            unavailable_note=(
                "No governed servicing earnings signal is published for this event period"
            ),
        )
        has_period_data = target_period is not None and event_card.upb.value is not None
        summary = (
            f"Published servicing observations for Q{event['fiscal_quarter']} "
            f"{event['fiscal_year']} are shown below. Every presentation value is bound "
            "to that reporting period and its retained evidence."
            if has_period_data
            else (
                f"No governed servicing observations are published for Q"
                f"{event['fiscal_quarter']} {event['fiscal_year']}. Earlier-period values "
                "are not substituted."
            )
        )
        event_date = datetime.fromisoformat(event["event_at"]).date()
        briefs.append(
            EarningsPresentation(
                company_id=company_id,
                ticker=company["ticker"],
                legal_name=company["legal_name"],
                platform=None,
                reporting_period=f"Q{event['fiscal_quarter']} {event['fiscal_year']}",
                earnings_date=event_date.strftime("%B %d, %Y").replace(" 0", " "),
                read="Unavailable",
                read_status="Sentiment is not produced by the governed earnings pipeline",
                headline=f"{company['ticker']} servicing disclosure, distilled",
                summary=summary,
                signals=(event_card.upb, event_card.growth, income_metric),
                source_url=event["source_url"],
                evidence_id=event["evidence_id"],
            )
        )
    return briefs


def serialize_cards(
    cards: list[CompanyPresentation],
    *,
    scale_assessment: ScaleAssessment,
) -> list[dict[str, object]]:
    """Serialize cards and fail closed unless portfolio scale is authoritatively comparable.

    Args:
        cards: Exact governed presentation cards.
        scale_assessment: Repository-owned comparability result for servicing UPB.

    Returns:
        JSON-safe Jinja payloads with provenance and guarded scale geometry.
    """
    maximum = max((card.upb.value or Decimal(0) for card in cards), default=Decimal(0))
    serialized: list[dict[str, object]] = []
    for card in cards:
        payload = asdict(card)
        scale = (
            (card.upb.value or Decimal(0)) / maximum * _HUNDRED
            if scale_assessment.available and maximum and card.upb.value is not None
            else None
        )
        payload["relative_scale"] = str(_quantized(scale, "0.1")) if scale is not None else None
        payload["scale_status"] = scale_assessment.status
        payload["scale_reasons"] = scale_assessment.reasons
        payload["search_text"] = " ".join(
            value for value in (card.legal_name, card.ticker, card.platform) if value
        ).casefold()
        payload["kpis"] = {
            "loans": payload["customer_loans"],
            "owned": payload["owned_mix"],
            "growth": payload["growth"],
            "upb": payload["upb"],
        }
        for key, metric in (
            ("upb", card.upb),
            ("loans", card.customer_loans),
            ("growth", card.growth),
            ("owned", card.owned_mix),
        ):
            payload[f"sort_{key}"] = (
                str((metric.value * Decimal(1000)).to_integral_value())
                if metric.value is not None
                else "-999999999999999999999999999"
            )
        serialized.append(payload)
    return serialized


def serialize_earnings(briefs: list[EarningsPresentation]) -> list[dict[str, object]]:
    """Serialize immutable earnings models for Jinja rendering."""
    return [asdict(brief) for brief in briefs]
