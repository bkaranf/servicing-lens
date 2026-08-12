# ruff: noqa: C901, EM101, EM102, FBT003, PERF401, PLR0913, PLR2004, TRY003
"""Offline, fail-closed Wells Fargo Phase 4a retained-evidence parser."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, cast

import yaml

from mortgage_servicing_dashboard.domain import (
    ObservationState,
    ParsedObservationCandidate,
    decimal_places,
    parse_decimal,
)
from mortgage_servicing_dashboard.metric_engine import (
    CalculationTrace,
    Completeness,
    DerivationRequest,
    MetricCatalog,
    MetricDimension,
    MetricInput,
    MetricMethodology,
    MetricUnit,
    PeriodType,
    PublicationStatus,
    ValueState,
    derive_metric,
    load_metric_catalog,
    validate_metric_input,
)
from mortgage_servicing_dashboard.sources import _TableRows

_COMPANY_ID: Final = "wfc"
_REPORTING_ENTITY_ID: Final = "wfc_sec_registrant"
_PERIOD_START: Final = {
    date(2025, 9, 30): date(2025, 7, 1),
    date(2025, 12, 31): date(2025, 10, 1),
    date(2026, 3, 31): date(2026, 1, 1),
    date(2026, 6, 30): date(2026, 4, 1),
}
_SCALE_MULTIPLIERS: Final = {
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
}
_METRIC_SCOPES: Final = {
    "servicing_for_others_upb": "wfc_consolidated_residential_mortgage_servicing",
    "total_servicing_upb": "wfc_managed_residential_mortgage_servicing",
    "bank_owned_loans_serviced_upb": "wfc_owned_residential_loans_serviced",
    "servicing_revenue": "wfc_mortgage_banking_servicing_economics",
    "msr_fair_value": "wfc_owned_residential_msr",
    "msr_realization_or_amortization": "wfc_owned_residential_msr",
    "msr_fair_value_market_change": "wfc_owned_residential_msr",
    "msr_fair_value_assumption_change": "wfc_owned_residential_msr",
    "msr_ending_balance": "wfc_owned_residential_msr",
    "msr_hedging_result": "wfc_owned_residential_msr",
    "msr_fair_value_multiple_of_related_upb": "wfc_owned_residential_msr",
    "msr_fair_value_bps_of_related_upb": "wfc_owned_residential_msr",
}
_CANONICAL_METHODS: Final = {
    "msr_fair_value_market_change": "MSR_FAIR_VALUE_ROLLFORWARD_REPORTED",
    "msr_fair_value_assumption_change": "MSR_FAIR_VALUE_ROLLFORWARD_REPORTED",
    "msr_hedging_result": "MSR_HEDGE_RESULT_REPORTED",
}
_MSR_DIMENSION_METRICS: Final = frozenset(
    {
        "msr_fair_value",
        "msr_ending_balance",
        "msr_fair_value_market_change",
        "msr_fair_value_assumption_change",
        "msr_hedging_result",
    }
)
_NUMERIC_TOKEN = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$")


class WfcPhase4Error(ValueError):
    """Raised when WFC configuration, evidence, semantics, or parity drift."""


@dataclass(frozen=True, slots=True)
class WfcEvidence:
    """One immutable, hash-verified WFC official response body."""

    evidence_id: str
    source_key: str
    company_id: str
    source_class: str
    accession: str | None
    form: str | None
    document_type: str | None
    url: str
    period_end: date | None
    accepted_at: datetime | None
    published_at: datetime | None
    retrieved_at: datetime
    sha256: str
    byte_length: int
    media_type: str
    representation: str
    capture_method: str
    locator: str
    parser_name: str
    parser_version: str
    retention_location: str
    actual_fixture_path: Path


@dataclass(frozen=True, slots=True)
class WfcCellAssessment:
    """One explicit WFC metric-period disclosure disposition."""

    company_id: str
    metric_id: str
    period_end: date
    reporting_entity_id: str
    reporting_scope_id: str
    dimensions: tuple[MetricDimension, ...]
    assessment_status: str
    result_state: str
    source_keys: tuple[str, ...]
    locators: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True, slots=True)
class WfcNormalizationTrace:
    """Exact replay controls for one verbatim WFC reported token."""

    rule: str
    sign_normalization: str
    dash_policy: str | None
    reported_scale: str
    multiplier: Decimal
    parentheses_negative: bool


@dataclass(frozen=True, slots=True)
class WfcReportedCandidate:
    """One exact reported candidate with controlled semantic dimensions."""

    candidate: ParsedObservationCandidate
    dimensions: tuple[MetricDimension, ...]
    source_key: str
    source_methodology: str
    normalization_rule: str
    normalization_trace: WfcNormalizationTrace
    evidence_state: str


@dataclass(frozen=True, slots=True)
class WfcDerivedCandidate:
    """One metric-engine-authorized WFC derived candidate."""

    candidate_id: str
    company_id: str
    metric_id: str
    metric_version: str
    period_start: date | None
    period_end: date
    normalized_value: Decimal
    unit: str
    reporting_entity_id: str
    reporting_scope_id: str
    methodology: str
    formula: str
    formula_version: str
    input_candidate_ids: tuple[str, ...]
    input_roles: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evidence_locator: str
    request: DerivationRequest
    trace: CalculationTrace


@dataclass(frozen=True, slots=True)
class WfcBlockedDerivation:
    """One governed WFC request rejected by the current catalog."""

    company_id: str
    metric_id: str
    period_end: date
    formula_version: str
    missing_input_metric_ids: tuple[str, ...]
    input_candidate_ids: tuple[str, ...]
    request: DerivationRequest
    decision_reasons: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class WfcRegulatoryResearchExpectation:
    """Native-scope regulatory research that never satisfies the SEC grid."""

    family: str
    reporter_rssd: str
    reporting_scope_id: str
    official_url: str
    expected_series: tuple[str, ...]
    acquired: bool
    grid_eligible: bool


@dataclass(frozen=True, slots=True)
class WfcPhase4Dataset:
    """Complete deterministic WFC Phase 4a offline parse result."""

    dataset_version: str
    status: str
    publication_authorized: bool
    parser_implemented: bool
    parser_name: str
    parser_version: str
    knowledge_at: datetime
    catalog: MetricCatalog
    evidence: tuple[WfcEvidence, ...]
    assessments: tuple[WfcCellAssessment, ...]
    reported_candidates: tuple[WfcReportedCandidate, ...]
    support_candidates: tuple[WfcReportedCandidate, ...]
    derived_candidates: tuple[WfcDerivedCandidate, ...]
    blocked_derivations: tuple[WfcBlockedDerivation, ...]
    missing_cells: tuple[WfcCellAssessment, ...]
    regulatory_research_expectations: tuple[WfcRegulatoryResearchExpectation, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedSource:
    evidence: WfcEvidence
    content: bytes


def load_wfc_phase4_dataset(config_dir: Path) -> WfcPhase4Dataset:
    """Load the WFC Phase 4a retained-evidence dataset without network access.

    Args:
        config_dir: Repository ``config`` directory.

    Returns:
        Verified evidence, complete assessments, exact reported candidates, and
        governed derived or blocked requests.

    Raises:
        WfcPhase4Error: If configuration, evidence, parsing, or parity drifts.
    """
    root = config_dir.resolve()
    config = _yaml(root / "phase4" / "wfc_sources.yaml")
    status = _text(config.get("status"), "status")
    if status != "PUBLICATION_VALIDATED":
        raise WfcPhase4Error("WFC dataset status is not publication validated")
    if config.get("publication_authorized") is not True:
        raise WfcPhase4Error("WFC publication is not authorized")
    if config.get("parser_implemented") is not True:
        raise WfcPhase4Error("WFC parser is not marked implemented")
    parser_config = _mapping(config.get("offline_parser"), "offline_parser")
    parser_name = _text(parser_config.get("parser_name"), "parser_name")
    parser_version = _text(parser_config.get("parser_version"), "parser_version")
    catalog = load_metric_catalog(
        root / "metrics" / "catalog.yaml",
        extension_paths=(
            root / "metrics" / "phase3_deepening.v1.yaml",
            root / "metrics" / "phase4_wfc.v1.yaml",
        ),
    )
    manifest_path = (root / "phase4" / _text(config.get("manifest"), "manifest")).resolve()
    sources, evidence = _load_evidence(
        manifest_path=manifest_path,
        parser_name=parser_name,
        parser_version=parser_version,
    )
    reported = _parse_reported(
        parser_config=parser_config,
        sources=sources,
        catalog=catalog,
    )
    support = _build_support_candidates(reported=reported, catalog=catalog)
    assessments = _build_assessments(config=config, reported=reported, catalog=catalog)
    _validate_reported_parity(assessments, reported)
    derived, blocked = _derive(
        catalog=catalog,
        assessments=assessments,
        reported=reported,
        support=support,
    )
    missing = tuple(item for item in assessments if item.result_state == "NOT_DISCLOSED")
    research = _regulatory_research(config)
    expected_counts = _mapping(
        _mapping(config["eligible_source_assessment"], "eligible_source_assessment").get(
            "classification_counts"
        ),
        "classification_counts",
    )
    actual_counts = {
        "R": len(reported),
        "D": len(derived) + len(blocked),
        "ND": len(missing),
        "SOURCE_NOT_CHECKED": sum(
            item.result_state == "SOURCE_NOT_CHECKED" for item in assessments
        ),
    }
    if actual_counts != {key: int(value) for key, value in expected_counts.items()}:
        raise WfcPhase4Error(f"WFC matrix count drift: {actual_counts}")
    return WfcPhase4Dataset(
        dataset_version=_text(config.get("version"), "version"),
        status=status,
        publication_authorized=True,
        parser_implemented=True,
        parser_name=parser_name,
        parser_version=parser_version,
        knowledge_at=max(item.retrieved_at for item in evidence),
        catalog=catalog,
        evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        assessments=tuple(sorted(assessments, key=_assessment_key)),
        reported_candidates=tuple(sorted(reported, key=_reported_key)),
        support_candidates=tuple(sorted(support, key=_reported_key)),
        derived_candidates=tuple(sorted(derived, key=_derived_key)),
        blocked_derivations=tuple(sorted(blocked, key=_blocked_key)),
        missing_cells=tuple(sorted(missing, key=_assessment_key)),
        regulatory_research_expectations=research,
    )


def apply_wfc_normalization_trace(raw_value: str, trace: WfcNormalizationTrace) -> Decimal:
    """Replay a WFC raw token through its exact reported-scale controls."""
    parsed = parse_decimal(raw_value, scale="ones")
    if not trace.parentheses_negative and raw_value.strip().startswith("("):
        raise WfcPhase4Error("parenthetical raw token lacks negative-sign authority")
    normalized = parsed * trace.multiplier
    if trace.sign_normalization == "positive_reduction_magnitude":
        return abs(normalized)
    if trace.sign_normalization != "source_signed":
        raise WfcPhase4Error("unsupported WFC sign normalization")
    return normalized


def _load_evidence(
    *,
    manifest_path: Path,
    parser_name: str,
    parser_version: str,
) -> tuple[dict[str, _VerifiedSource], tuple[WfcEvidence, ...]]:
    manifest = _yaml(manifest_path)
    rows = _mapping_rows(manifest.get("sources"), "manifest sources")
    sources: dict[str, _VerifiedSource] = {}
    evidence: list[WfcEvidence] = []
    for row in rows:
        source_key = _text(row.get("source_key"), "source_key")
        digest = _sha256(row.get("sha256"))
        relative = Path(_text(row.get("path"), "path"))
        expected = Path("sha256") / digest[:2] / f"{digest}.bin"
        if relative.as_posix() != expected.as_posix():
            raise WfcPhase4Error(f"non-content-addressed WFC path: {source_key}")
        fixture = (manifest_path.parent / relative).resolve()
        try:
            content = fixture.read_bytes()
        except OSError as error:
            raise WfcPhase4Error(f"WFC evidence body unavailable: {source_key}") from error
        if len(content) != int(row["byte_length"]):
            raise WfcPhase4Error(f"WFC evidence byte-length mismatch: {source_key}")
        if hashlib.sha256(content).hexdigest() != digest:
            raise WfcPhase4Error(f"WFC evidence hash mismatch: {source_key}")
        if row.get("representation") != "ORIGINAL_HTTP_RESPONSE":
            raise WfcPhase4Error(f"unsupported WFC representation: {source_key}")
        if row.get("capture_method") != "sec_http_get":
            raise WfcPhase4Error(f"unsupported WFC capture method: {source_key}")
        item = WfcEvidence(
            evidence_id=_text(row.get("evidence_id"), "evidence_id"),
            source_key=source_key,
            company_id=_COMPANY_ID,
            source_class=_text(row.get("source_class"), "source_class"),
            accession=_optional_text(row.get("accession")),
            form=_optional_text(row.get("form")),
            document_type=_optional_text(row.get("document_type")),
            url=_official_url(row.get("url")),
            period_end=_optional_date(row.get("report_date")),
            accepted_at=_optional_datetime(row.get("accepted_at")),
            published_at=_optional_datetime(row.get("accepted_at")),
            retrieved_at=_datetime(row.get("retrieved_at"), "retrieved_at"),
            sha256=digest,
            byte_length=int(row["byte_length"]),
            media_type=_text(row.get("media_type"), "media_type"),
            representation="ORIGINAL_HTTP_RESPONSE",
            capture_method="sec_http_get",
            locator=_text(row.get("locator"), "locator"),
            parser_name=parser_name,
            parser_version=parser_version,
            retention_location=f"content-sha256://{digest}",
            actual_fixture_path=fixture,
        )
        if source_key in sources:
            raise WfcPhase4Error(f"duplicate WFC source key: {source_key}")
        sources[source_key] = _VerifiedSource(item, content)
        evidence.append(item)
    if len(evidence) != 18 or len({item.sha256 for item in evidence}) != 18:
        raise WfcPhase4Error("WFC evidence manifest must contain 18 unique bodies")
    _validate_submissions(sources)
    return sources, tuple(evidence)


def _validate_submissions(sources: dict[str, _VerifiedSource]) -> None:
    try:
        payload = json.loads(sources["wfc_sec_submissions"].content)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WfcPhase4Error("WFC SEC submissions evidence is invalid") from error
    identity = (
        payload.get("name"),
        str(payload.get("cik", "")).zfill(10),
        tuple(payload.get("tickers", ()))[:1],
        tuple(payload.get("exchanges", ()))[:1],
        payload.get("fiscalYearEnd"),
    )
    if identity != (
        "WELLS FARGO & COMPANY/MN",
        "0000072971",
        ("WFC",),
        ("NYSE",),
        "1231",
    ):
        raise WfcPhase4Error("WFC SEC identity drift")


def _parse_reported(
    *,
    parser_config: dict[str, Any],
    sources: dict[str, _VerifiedSource],
    catalog: MetricCatalog,
) -> tuple[WfcReportedCandidate, ...]:
    windows = int(parser_config.get("table_window_rows", 0))
    if windows < 1:
        raise WfcPhase4Error("WFC parser requires a positive table window")
    quarter_rows = _mapping(parser_config.get("sources"), "offline parser sources")
    parsed: list[WfcReportedCandidate] = []
    for quarter_name, raw_quarter in quarter_rows.items():
        quarter = _mapping(raw_quarter, f"offline parser {quarter_name}")
        source_key = _text(quarter.get("source_key"), "source_key")
        source = sources.get(source_key)
        if source is None:
            raise WfcPhase4Error(f"WFC parser source is not retained: {source_key}")
        period_end = _date(quarter.get("period_end"), "period_end")
        collector = _TableRows()
        try:
            collector.feed(source.content.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise WfcPhase4Error(f"WFC source is not UTF-8: {source_key}") from error
        recipes = _mapping_rows(quarter.get("rows"), f"{quarter_name} rows")
        for recipe in recipes:
            parsed.append(
                _parse_recipe(
                    source=source,
                    collector=collector,
                    quarter=quarter,
                    recipe=recipe,
                    period_end=period_end,
                    window_rows=windows,
                    catalog=catalog,
                )
            )
    if len(parsed) != 31:
        raise WfcPhase4Error(f"WFC parser expected 31 reported candidates, got {len(parsed)}")
    keys = {(item.candidate.metric_id, item.candidate.period_end) for item in parsed}
    if len(keys) != len(parsed):
        raise WfcPhase4Error("WFC reported candidates collide on metric-period")
    return tuple(parsed)


def _parse_recipe(
    *,
    source: _VerifiedSource,
    collector: _TableRows,
    quarter: dict[str, Any],
    recipe: dict[str, Any],
    period_end: date,
    window_rows: int,
    catalog: MetricCatalog,
) -> WfcReportedCandidate:
    metric_id = _text(recipe.get("metric_id"), "metric_id")
    raw_label = _text(recipe.get("raw_label"), "raw_label")
    table = _text(recipe.get("table"), "table")
    scale = _text(recipe.get("scale"), "scale")
    period_type = _text(recipe.get("period_type"), "period_type")
    if scale not in _SCALE_MULTIPLIERS:
        raise WfcPhase4Error(f"unsupported WFC reported scale: {scale}")
    if period_type not in {"instant", "duration"}:
        raise WfcPhase4Error(f"unsupported WFC period type: {period_type}")
    matches: list[tuple[int, tuple[str, ...]]] = []
    for index, row in enumerate(collector.rows):
        if not row or row[0] != raw_label:
            continue
        prior = collector.rows[max(0, index - window_rows) : index]
        prior_text = " | ".join(" | ".join(item) for item in prior)
        if table == "income":
            qualifier = _text(quarter.get("income_anchor"), "income_anchor")
        elif table == "portfolio":
            qualifier = _text(quarter.get("portfolio_header"), "portfolio_header")
        elif table == "rollforward":
            qualifier = _text(quarter.get("rollforward_header"), "rollforward_header")
        else:
            raise WfcPhase4Error(f"unsupported WFC table selector: {table}")
        if all(part.strip() in prior_text for part in qualifier.split(" | ")):
            matches.append((index, row))
    if len(matches) != 1:
        raise WfcPhase4Error(
            f"WFC row not uniquely qualified: {source.evidence.source_key}:{raw_label}"
        )
    row_index, row = matches[0]
    raw_tokens = tuple(cell for cell in row[1:] if _NUMERIC_TOKEN.fullmatch(cell.strip()))
    if not raw_tokens:
        raise WfcPhase4Error(f"WFC row has no reported numeric token: {raw_label}")
    raw_value = raw_tokens[0]
    sign_normalization = (
        "positive_reduction_magnitude"
        if metric_id == "msr_realization_or_amortization"
        else "source_signed"
    )
    trace = WfcNormalizationTrace(
        f"usd_from_{scale}",
        sign_normalization,
        None,
        scale,
        _SCALE_MULTIPLIERS[scale],
        True,
    )
    normalized = apply_wfc_normalization_trace(raw_value, trace)
    definition = _latest_definition(catalog, metric_id)
    canonical_method = _CANONICAL_METHODS.get(metric_id, "ISSUER_REPORTED")
    dimensions = _dimensions(metric_id)
    scope = _METRIC_SCOPES.get(metric_id)
    if scope is None:
        raise WfcPhase4Error(f"WFC metric has no explicit scope: {metric_id}")
    start = None if period_type == "instant" else _PERIOD_START[period_end]
    source_method = _source_methodology(metric_id, table)
    locator = (
        f"{source.evidence.locator}; table={table}; qualifier={qualifier!r}; "
        f"exact row={raw_label!r}; row_index={row_index}; current residential/current-quarter "
        "numeric token[0]"
    )
    identity = {
        "evidence_id": source.evidence.evidence_id,
        "evidence_sha256": source.evidence.sha256,
        "metric_id": metric_id,
        "metric_version": definition.semantic_version,
        "period_start": start.isoformat() if start else None,
        "period_end": period_end.isoformat(),
        "period_type": period_type,
        "raw_label": raw_label,
        "raw_value": raw_value,
        "normalized_value": str(normalized),
        "reported_scale": scale,
        "canonical_unit": definition.unit.value,
        "methodology": canonical_method,
        "source_methodology": source_method,
        "reporting_entity_id": _REPORTING_ENTITY_ID,
        "reporting_scope_id": scope,
        "dimensions": [(item.name, item.value) for item in dimensions],
        "locator": locator,
        "parser_name": source.evidence.parser_name,
        "parser_version": source.evidence.parser_version,
    }
    candidate_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
    candidate = ParsedObservationCandidate(
        candidate_id=candidate_id,
        company_id=_COMPANY_ID,
        metric_id=metric_id,
        metric_version=definition.semantic_version,
        period_start=start,
        period_end=period_end,
        fiscal_year=period_end.year,
        fiscal_quarter=((period_end.month - 1) // 3) + 1,
        period_type=period_type,
        raw_label=raw_label,
        raw_value=raw_value,
        normalized_value=normalized,
        currency="USD",
        unit=definition.unit.value,
        reported_scale=scale,
        reported_decimals=decimal_places(raw_value),
        observation_state=ObservationState.REPORTED_ACTUAL,
        methodology=canonical_method,
        reporting_entity_id=_REPORTING_ENTITY_ID,
        reporting_scope_id=scope,
        evidence_id=source.evidence.evidence_id,
        evidence_locator=locator,
        extraction_method="deterministic_bounded_html_table",
        parser_name=source.evidence.parser_name,
        parser_version=source.evidence.parser_version,
    )
    wrapper = WfcReportedCandidate(
        candidate=candidate,
        dimensions=dimensions,
        source_key=source.evidence.source_key,
        source_methodology=source_method,
        normalization_rule=f"usd_from_{scale}",
        normalization_trace=trace,
        evidence_state="REPORTED_ACTUAL_FINAL_PERIODIC_FILING",
    )
    _validate_candidate(catalog, wrapper)
    return wrapper


def _validate_candidate(catalog: MetricCatalog, wrapper: WfcReportedCandidate) -> None:
    candidate = wrapper.candidate
    if (
        apply_wfc_normalization_trace(candidate.raw_value, wrapper.normalization_trace)
        != candidate.normalized_value
    ):
        raise WfcPhase4Error(f"WFC normalization replay failed: {candidate.candidate_id}")
    metric_input = _metric_input(wrapper)
    decision = validate_metric_input(metric_input, catalog)
    if decision.reasons:
        reasons = ",".join(item.value for item in decision.reasons)
        raise WfcPhase4Error(f"WFC candidate violates catalog: {candidate.metric_id}:{reasons}")


def _build_support_candidates(
    *,
    reported: tuple[WfcReportedCandidate, ...],
    catalog: MetricCatalog,
) -> tuple[WfcReportedCandidate, ...]:
    """Govern the exact same-table residential MSR denominator outside the grid."""
    metric_id = "wfc_residential_msr_related_upb"
    definition = _latest_definition(catalog, metric_id)
    dimensions = (MetricDimension("msr_population", "owned_msr"),)
    support: list[WfcReportedCandidate] = []
    for source in reported:
        if source.candidate.metric_id != "servicing_for_others_upb":
            continue
        candidate = source.candidate
        locator = (
            f"{candidate.evidence_locator}; governed support boundary=Note 6 residential "
            "mortgage servicing UPB used with the same Note 6 residential MSR fair value"
        )
        source_methodology = (
            "WFC_NOTE6_RESIDENTIAL_MORTGAGE_SERVICING_UPB_IN_RESIDENTIAL_MSR_CONTEXT"
        )
        identity = {
            "source_candidate_id": candidate.candidate_id,
            "evidence_id": candidate.evidence_id,
            "metric_id": metric_id,
            "metric_version": definition.semantic_version,
            "raw_label": candidate.raw_label,
            "raw_value": candidate.raw_value,
            "normalized_value": str(candidate.normalized_value),
            "period_end": candidate.period_end.isoformat(),
            "reporting_entity_id": candidate.reporting_entity_id,
            "reporting_scope_id": "wfc_owned_residential_msr",
            "dimensions": [(item.name, item.value) for item in dimensions],
            "locator": locator,
            "source_methodology": source_methodology,
            "normalization_rule": source.normalization_rule,
            "parser_name": candidate.parser_name,
            "parser_version": candidate.parser_version,
        }
        cloned = replace(
            candidate,
            candidate_id=hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:32],
            metric_id=metric_id,
            metric_version=definition.semantic_version,
            methodology="ISSUER_REPORTED",
            reporting_scope_id="wfc_owned_residential_msr",
            evidence_locator=locator,
        )
        wrapper = replace(
            source,
            candidate=cloned,
            dimensions=dimensions,
            source_methodology=source_methodology,
        )
        _validate_candidate(catalog, wrapper)
        support.append(wrapper)
    if len(support) != 4:
        raise WfcPhase4Error("WFC dataset must contain four exact MSR-related UPB supports")
    return tuple(support)


def _build_assessments(
    *,
    config: dict[str, Any],
    reported: tuple[WfcReportedCandidate, ...],
    catalog: MetricCatalog,
) -> tuple[WfcCellAssessment, ...]:
    assessment_root = _mapping(
        config.get("eligible_source_assessment"), "eligible_source_assessment"
    )
    cells = _mapping(assessment_root.get("cells"), "assessment cells")
    quarters = _mapping(config.get("quarters"), "quarters")
    source_sets = _mapping(config.get("eligible_source_sets"), "eligible_source_sets")
    profiles = _mapping(config.get("check_profiles"), "check_profiles")
    reported_by_key = {
        (item.candidate.metric_id, item.candidate.period_end): item for item in reported
    }
    assessments: list[WfcCellAssessment] = []
    for metric_id, raw_cell in cells.items():
        cell = _mapping(raw_cell, f"assessment {metric_id}")
        periods = _mapping(cell.get("periods"), f"assessment periods {metric_id}")
        profile_name = _text(cell.get("check_profile"), "check_profile")
        profile = _mapping(profiles.get(profile_name), "check profile")
        for quarter_name, raw_status in periods.items():
            status = _text(raw_status, "matrix status")
            quarter = _mapping(quarters.get(quarter_name), f"quarter {quarter_name}")
            period_end = _date(quarter.get("period_end"), "period_end")
            dimensions = _assessment_dimensions(metric_id, catalog)
            scope = _assessment_scope(metric_id, cell)
            source_keys: tuple[str, ...]
            locators: tuple[str, ...]
            if status == "R":
                wrapper = reported_by_key.get((metric_id, period_end))
                if wrapper is None:
                    raise WfcPhase4Error(f"WFC reported assessment lacks candidate: {metric_id}")
                source_keys = (wrapper.source_key,)
                locators = (wrapper.candidate.evidence_locator,)
                result_state = "PUBLISHED"
                reason = "EXACT_REPORTED_DISCLOSURE"
            elif status == "D":
                inputs = tuple(
                    item
                    for item in reported
                    if item.candidate.period_end == period_end
                    and item.candidate.metric_id in {"msr_fair_value", "servicing_for_others_upb"}
                )
                source_keys = tuple(sorted({item.source_key for item in inputs}))
                locators = tuple(sorted({item.candidate.evidence_locator for item in inputs}))
                result_state = "DERIVED"
                reason = "GOVERNED_WFC_SCOPE_BRIDGE"
            elif status == "ND":
                keys = tuple(
                    _text(item, "eligible source key") for item in source_sets[quarter_name]
                )
                source_keys = keys
                locators = tuple(_checked_locator(key, profile) for key in keys)
                result_state = "NOT_DISCLOSED"
                reason = "CHECKED_COMPLETE_NOT_DISCLOSED"
            else:
                raise WfcPhase4Error(f"unsupported WFC matrix status: {status}")
            assessments.append(
                WfcCellAssessment(
                    company_id=_COMPANY_ID,
                    metric_id=metric_id,
                    period_end=period_end,
                    reporting_entity_id=_REPORTING_ENTITY_ID,
                    reporting_scope_id=scope,
                    dimensions=dimensions,
                    assessment_status=("DISCLOSURE_FOUND" if status == "R" else "CHECKED_COMPLETE"),
                    result_state=result_state,
                    source_keys=source_keys,
                    locators=locators,
                    reason_code=reason,
                )
            )
    if len(assessments) != 212:
        raise WfcPhase4Error("WFC assessment grid must contain 212 cells")
    return tuple(assessments)


def _validate_reported_parity(
    assessments: tuple[WfcCellAssessment, ...],
    reported: tuple[WfcReportedCandidate, ...],
) -> None:
    expected = {
        (item.metric_id, item.period_end)
        for item in assessments
        if item.result_state == "PUBLISHED"
    }
    actual = {(item.candidate.metric_id, item.candidate.period_end) for item in reported}
    if actual != expected:
        raise WfcPhase4Error("WFC reported candidate parity mismatch")


def _derive(
    *,
    catalog: MetricCatalog,
    assessments: tuple[WfcCellAssessment, ...],
    reported: tuple[WfcReportedCandidate, ...],
    support: tuple[WfcReportedCandidate, ...],
) -> tuple[tuple[WfcDerivedCandidate, ...], tuple[WfcBlockedDerivation, ...]]:
    by_key = {
        (item.candidate.metric_id, item.candidate.period_end): item
        for item in (*reported, *support)
    }
    derived: list[WfcDerivedCandidate] = []
    blocked: list[WfcBlockedDerivation] = []
    for assessment in assessments:
        if assessment.result_state != "DERIVED":
            continue
        definition = _latest_definition(catalog, assessment.metric_id)
        if definition.derivation is None:
            raise WfcPhase4Error(f"WFC D cell lacks governed derivation: {assessment.metric_id}")
        fair = by_key[("msr_fair_value", assessment.period_end)]
        upb = by_key[("wfc_residential_msr_related_upb", assessment.period_end)]
        inputs = (("fair_value", fair), ("related_upb", upb))
        request_id = hashlib.sha256(
            f"wfc:{assessment.metric_id}:{assessment.period_end.isoformat()}".encode()
        ).hexdigest()[:32]
        request = DerivationRequest(
            derived_observation_id=request_id,
            metric_id=assessment.metric_id,
            metric_version=definition.semantic_version,
            issuer_id=_COMPANY_ID,
            reporting_entity_id=_REPORTING_ENTITY_ID,
            reporting_scope_id="wfc_owned_residential_msr",
            period_type=PeriodType.INSTANT,
            period_start=None,
            period_end=assessment.period_end,
            dimensions=(MetricDimension("msr_population", "owned_msr"),),
            inputs=tuple((role, _metric_input(wrapper)) for role, wrapper in inputs),
        )
        decision = derive_metric(request, catalog)
        if decision.result is None:
            reasons = tuple(item.value for item in decision.reasons)
            blocked.append(
                WfcBlockedDerivation(
                    company_id=_COMPANY_ID,
                    metric_id=assessment.metric_id,
                    period_end=assessment.period_end,
                    formula_version=definition.derivation.formula_version,
                    missing_input_metric_ids=(),
                    input_candidate_ids=tuple(
                        wrapper.candidate.candidate_id for _, wrapper in inputs
                    ),
                    request=request,
                    decision_reasons=reasons,
                    reason=(
                        "Governed WFC metric-engine derivation rejected the exact inputs: "
                        + ",".join(reasons)
                    ),
                )
            )
            continue
        result = decision.result
        wrappers = tuple(wrapper for _, wrapper in inputs)
        derived.append(
            WfcDerivedCandidate(
                candidate_id=result.observation_id,
                company_id=_COMPANY_ID,
                metric_id=result.metric_id,
                metric_version=result.metric_version,
                period_start=result.period_start,
                period_end=result.period_end,
                normalized_value=result.value,
                unit=result.unit.value,
                reporting_entity_id=result.reporting_entity_id,
                reporting_scope_id=result.reporting_scope_id,
                methodology=result.methodology.value,
                formula=result.trace.formula.value,
                formula_version=result.trace.formula_version,
                input_candidate_ids=tuple(item.candidate.candidate_id for item in wrappers),
                input_roles=tuple(role for role, _ in inputs),
                evidence_ids=tuple(sorted({item.candidate.evidence_id for item in wrappers})),
                evidence_locator="; ".join(
                    sorted({item.candidate.evidence_locator for item in wrappers})
                ),
                request=request,
                trace=result.trace,
            )
        )
    if len(derived) + len(blocked) != 8:
        raise WfcPhase4Error("WFC dataset must resolve exactly eight governed D cells")
    return tuple(derived), tuple(blocked)


def _metric_input(wrapper: WfcReportedCandidate) -> MetricInput:
    item = wrapper.candidate
    return MetricInput(
        observation_id=item.candidate_id,
        issuer_id=item.company_id,
        metric_id=item.metric_id,
        metric_version=item.metric_version,
        value=item.normalized_value,
        unit=MetricUnit(item.unit),
        period_type=PeriodType(item.period_type),
        period_start=item.period_start,
        period_end=item.period_end,
        reporting_entity_id=item.reporting_entity_id,
        reporting_scope_id=item.reporting_scope_id,
        methodology=MetricMethodology(item.methodology),
        publication_status=PublicationStatus.PUBLISHED,
        value_state=ValueState.REPORTED_ACTUAL,
        completeness=Completeness.COMPLETE,
        dimensions=wrapper.dimensions,
    )


def _regulatory_research(
    config: dict[str, Any],
) -> tuple[WfcRegulatoryResearchExpectation, ...]:
    root = _mapping(config.get("regulatory_research_expectations"), "regulatory research")
    rows = _mapping_rows(root.get("sources"), "regulatory sources")
    return tuple(
        sorted(
            (
                WfcRegulatoryResearchExpectation(
                    family=_text(row.get("family"), "family"),
                    reporter_rssd=_text(row.get("reporter_rssd"), "reporter_rssd"),
                    reporting_scope_id=_text(row.get("reporting_scope_id"), "reporting_scope_id"),
                    official_url=_official_url(row.get("official_url")),
                    expected_series=tuple(
                        _text(item.get("series"), "series")
                        for item in _mapping_rows(row.get("expected_items"), "expected_items")
                    ),
                    acquired=False,
                    grid_eligible=False,
                )
                for row in rows
            ),
            key=lambda item: item.family,
        )
    )


def _dimensions(metric_id: str) -> tuple[MetricDimension, ...]:
    if metric_id in _MSR_DIMENSION_METRICS:
        return (MetricDimension("msr_population", "owned_msr"),)
    return ()


def _assessment_dimensions(metric_id: str, catalog: MetricCatalog) -> tuple[MetricDimension, ...]:
    definition = _latest_definition(catalog, metric_id)
    population = {
        "servicing_for_others_upb": "servicing_for_others",
        "total_servicing_upb": "total_servicing",
        "servicing_loan_count": "total_servicing",
        "subservicing_upb": "subservicing",
        "interim_servicing_upb": "interim_servicing",
        "owned_msr_upb": "owned_msr",
    }.get(metric_id, "issuer_disclosed")
    values = {
        requirement.taxonomy: requirement.fixed_value
        for requirement in definition.dimensions
        if requirement.fixed_value is not None
    }
    defaults = {
        "portfolio_population": population,
        "portfolio_mix_overlap": "source_defined",
        "delinquency_foreclosure_treatment": "included",
        "delinquency_bankruptcy_treatment": "source_defined",
        "delinquency_forbearance_treatment": "source_defined",
        "msr_population": "owned_msr",
    }
    dimensions: list[MetricDimension] = []
    for requirement in definition.dimensions:
        value = values.get(requirement.taxonomy) or defaults.get(requirement.taxonomy)
        if value is None:
            raise WfcPhase4Error(
                f"WFC cannot resolve assessment dimension {requirement.taxonomy}:{metric_id}"
            )
        dimensions.append(MetricDimension(requirement.taxonomy, value))
    resolved = tuple(sorted(dimensions))
    _validate_dimensions_against_definition(resolved, definition, catalog, metric_id)
    return resolved


def _validate_dimensions_against_definition(
    dimensions: tuple[MetricDimension, ...],
    definition: Any,
    catalog: MetricCatalog,
    metric_id: str,
) -> None:
    expected = {item.taxonomy: item.fixed_value for item in definition.dimensions}
    supplied = {item.name: item.value for item in dimensions}
    if supplied.keys() != expected.keys():
        raise WfcPhase4Error(f"WFC assessment dimension taxonomy mismatch: {metric_id}")
    taxonomies = {item.taxonomy_id: set(item.values) for item in catalog.dimension_taxonomies}
    for name, value in supplied.items():
        if name not in taxonomies or value not in taxonomies[name]:
            raise WfcPhase4Error(f"WFC assessment dimension member is uncontrolled: {name}={value}")
        fixed = expected[name]
        if fixed is not None and value != fixed:
            raise WfcPhase4Error(f"WFC assessment fixed dimension mismatch: {metric_id}:{name}")


def _assessment_scope(metric_id: str, cell: dict[str, Any]) -> str:
    configured = _optional_text(cell.get("scope_id"))
    if configured is not None:
        return configured
    return _METRIC_SCOPES.get(metric_id, "wfc_sec_checked_metric_boundary")


def _checked_locator(source_key: str, profile: dict[str, Any]) -> str:
    if "supplement" in source_key:
        region = profile.get("supplement_region")
    elif "presentation" in source_key:
        region = profile.get("presentation_region")
    elif "earnings" in source_key:
        region = profile.get("release_region")
    else:
        region = profile.get("periodic_region")
    return f"{source_key}: {_text(region, 'checked region')}"


def _source_methodology(metric_id: str, table: str) -> str:
    if metric_id == "servicing_revenue":
        return "WFC_NOTE6_TOTAL_NET_SERVICING_INCOME_INCLUDING_MSR_MARKS_AND_HEDGES"
    if table == "portfolio":
        return "WFC_NOTE6_RESIDENTIAL_MORTGAGE_SERVICING_POPULATION_TABLE"
    if metric_id == "msr_hedging_result":
        return "WFC_NOTE6_RESIDENTIAL_MSR_ECONOMIC_HEDGE_RESULT"
    if metric_id.startswith("msr_"):
        return "WFC_NOTE6_RESIDENTIAL_MSR_FAIR_VALUE_ROLLFORWARD"
    return "WFC_NOTE6_ISSUER_REPORTED"


def _latest_definition(catalog: MetricCatalog, metric_id: str) -> Any:
    versions = catalog.versions(metric_id)
    if not versions:
        raise WfcPhase4Error(f"WFC metric absent from catalog: {metric_id}")
    return versions[-1]


def _yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise WfcPhase4Error(f"WFC configuration could not be loaded: {path.name}") from error
    if not isinstance(loaded, dict):
        raise WfcPhase4Error(f"WFC configuration root must be a mapping: {path.name}")
    _reject_float(loaded, str(path))
    return cast("dict[str, Any]", loaded)


def _reject_float(value: object, location: str) -> None:
    if isinstance(value, float):
        raise WfcPhase4Error(f"binary float prohibited in WFC configuration: {location}")
    if isinstance(value, dict):
        for key, nested in value.items():
            _reject_float(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_float(nested, f"{location}[{index}]")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WfcPhase4Error(f"{label} must be a mapping")
    return cast("dict[str, Any]", value)


def _mapping_rows(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise WfcPhase4Error(f"{label} must be a nonempty list")
    return tuple(_mapping(item, label) for item in value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WfcPhase4Error(f"{label} must be nonempty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, "optional text")


def _date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(_text(value, label))
    except ValueError as error:
        raise WfcPhase4Error(f"{label} must be an ISO date") from error


def _optional_date(value: object) -> date | None:
    return None if value is None or value == "" else _date(value, "optional date")


def _datetime(value: object, label: str) -> datetime:
    text = _text(value, label).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise WfcPhase4Error(f"{label} must be an ISO datetime") from error
    if parsed.tzinfo is None:
        raise WfcPhase4Error(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None or value == "" else _datetime(value, "optional datetime")


def _sha256(value: object) -> str:
    digest = _text(value, "sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WfcPhase4Error("WFC SHA-256 must contain 64 lowercase hex characters")
    return digest


def _official_url(value: object) -> str:
    url = _text(value, "official URL")
    if not url.startswith(
        (
            "https://www.sec.gov/",
            "https://data.sec.gov/",
            "https://www.ffiec.gov/",
            "https://cdr.ffiec.gov/",
        )
    ):
        raise WfcPhase4Error("WFC source URL is not on an allow-listed official host")
    return url


def _assessment_key(item: WfcCellAssessment) -> tuple[str, date]:
    return item.metric_id, item.period_end


def _reported_key(item: WfcReportedCandidate) -> tuple[str, date]:
    return item.candidate.metric_id, item.candidate.period_end


def _derived_key(item: WfcDerivedCandidate) -> tuple[str, date]:
    return item.metric_id, item.period_end


def _blocked_key(item: WfcBlockedDerivation) -> tuple[str, date]:
    return item.metric_id, item.period_end
