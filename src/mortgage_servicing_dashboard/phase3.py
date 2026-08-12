# ruff: noqa: C901, EM101, EM102, PLR0911, PLR0913, PLR0915, PLR0917, PLR2004, S101, TRY003
"""Deterministic Phase 3 configuration and retained-evidence pipeline.

The loader in this module is intentionally offline.  It verifies every retained
source before parsing, extracts exact source text into :class:`Decimal` values,
and materializes the complete issuer/metric/period assessment grid.  Derived
values are accepted only from the governed metric engine and retain its complete
request, trace, and input lineage.
"""

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
    normalize_reported_value,
)
from mortgage_servicing_dashboard.metric_engine import (
    AnnualizationParameters,
    AveragingParameters,
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
)
from mortgage_servicing_dashboard.sources import (
    PublicSourceError,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
    _TableRows,
)

_PERIOD_START: Final = {
    date(2025, 9, 30): date(2025, 7, 1),
    date(2025, 12, 31): date(2025, 10, 1),
    date(2026, 3, 31): date(2026, 1, 1),
    date(2026, 6, 30): date(2026, 4, 1),
}
_SUPPORT_METRICS: Final = frozenset(
    {
        "fha_servicing_upb",
        "va_servicing_upb",
        "usda_servicing_upb",
        "closed_end_second_lien_servicing_upb",
        "other_servicing_upb",
        "owned_msr_msl_upb",
        "msr_additions_related_upb",
        "delinquency_30_to_89_upb",
        "delinquency_90_plus_upb",
        "foreclosure_upb",
    }
)
_PFSI_MSR_METRICS: Final = frozenset(
    {
        "owned_msr_upb",
        "msr_fair_value",
        "msr_beginning_balance",
        "msr_additions",
        "msr_sales",
        "msr_realization_or_amortization",
        "msr_ending_balance",
        "msr_fair_value_inputs_or_assumptions_change",
        "msr_hedging_result",
        "weighted_average_servicing_fee_bps",
        "capitalized_servicing_rate_on_additions",
        "msr_fair_value_multiple_of_related_upb",
        "msr_fair_value_bps_of_related_upb",
        "msr_additions_related_upb",
    }
)
_PFSI_TOTAL_METRICS: Final = frozenset(
    {"total_servicing_upb", "servicing_loan_count", "cost_to_service_per_loan"}
)
_PFSI_OWNED_MSL_METRICS: Final = frozenset(
    {
        "government_servicing_upb",
        "conventional_servicing_upb",
        "fnma_servicing_upb",
        "fhlmc_servicing_upb",
        "delinquency_30_plus_upb_rate",
        "delinquency_60_plus_upb_rate",
        "delinquency_90_plus_upb_rate",
        "foreclosure_upb_rate",
        "reo_upb",
        *_SUPPORT_METRICS,
    }
)
_INSTANT_EARNINGS_METRICS: Final = frozenset(
    {
        "total_servicing_upb",
        "subservicing_upb",
        "servicing_loan_count",
        "delinquency_60_plus_count_rate",
        "weighted_average_servicing_fee_bps",
    }
)


class Phase3Error(ValueError):
    """Raised when governed Phase 3 configuration or evidence fails closed."""


@dataclass(frozen=True, slots=True)
class Phase3Evidence:
    """One hash-verified immutable official source."""

    evidence_id: str
    source_key: str
    company_id: str
    source_class: str
    accession: str | None
    url: str
    period_end: date | None
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
class Phase3CellAssessment:
    """Explicit disposition for one issuer/metric/selected-period cell."""

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
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class Phase3BlockedDerivation:
    """Derived cell that fails closed before publication."""

    company_id: str
    metric_id: str
    period_end: date
    formula_version: str | None
    missing_input_metric_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class Phase3DerivedCandidate:
    """Metric-engine-validated derived candidate with exact lineage."""

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
class Phase3ReportedCandidate:
    """Exact reported candidate paired with its governed dimensions."""

    candidate: ParsedObservationCandidate
    dimensions: tuple[MetricDimension, ...]
    source_methodology: str
    normalization_rule: str
    normalization_trace: NormalizationTrace


@dataclass(frozen=True, slots=True)
class NormalizationTrace:
    """Exact deterministic replay instructions for one retained raw token."""

    rule: str
    sign_normalization: str
    dash_policy: str | None


@dataclass(frozen=True, slots=True)
class Phase3Dataset:
    """Complete deterministic Phase 3 parse result."""

    knowledge_at: datetime
    catalog: MetricCatalog
    evidence: tuple[Phase3Evidence, ...]
    assessments: tuple[Phase3CellAssessment, ...]
    reported_candidates: tuple[Phase3ReportedCandidate, ...]
    support_candidates: tuple[Phase3ReportedCandidate, ...]
    derived_candidates: tuple[Phase3DerivedCandidate, ...]
    blocked_derivations: tuple[Phase3BlockedDerivation, ...]
    missing_cells: tuple[Phase3CellAssessment, ...]


@dataclass(frozen=True, slots=True)
class _Source:
    definition: RecordedSourceDefinition
    evidence: Phase3Evidence
    content: bytes


def load_phase3_dataset(config_dir: Path) -> Phase3Dataset:
    """Load and verify the complete Phase 3 dataset without network access.

    Args:
        config_dir: Repository ``config`` directory.

    Returns:
        Stable typed evidence, assessments, exact candidates, and lineage.

    Raises:
        Phase3Error: If configuration, evidence, parity, or derivation fails.
        PublicSourceError: If a retained evidence body fails integrity checks.
    """
    root = config_dir.resolve()
    catalog = load_metric_catalog(
        root / "metrics" / "catalog.yaml",
        extension_paths=(root / "metrics" / "phase3_deepening.v1.yaml",),
    )
    universe = _yaml(root / "universe.yaml")
    companies = {
        str(item["id"]): item for item in cast("list[dict[str, Any]]", universe["companies"])
    }
    tfc_config = _yaml(root / "phase3" / "tfc_sources.yaml")
    pfsi_config = _yaml(root / "phase3" / "pfsi_sources.yaml")
    sources, evidence = _load_sources(root, tfc_config, pfsi_config)
    assessments = _assessments(tfc_config, pfsi_config, catalog)
    tfc, tfc_support = _parse_tfc(
        config=tfc_config,
        company=companies["tfc"],
        sources=sources,
        catalog=catalog,
    )
    pfsi, pfsi_support = _parse_pfsi(
        config=pfsi_config,
        sources=sources,
        catalog=catalog,
    )
    parsed_reported = tuple(
        sorted(
            (_governed_candidate(catalog, item) for item in (*tfc, *pfsi)),
            key=_candidate_sort_key,
        )
    )
    parsed_support = tuple(
        sorted(
            (_governed_candidate(catalog, item) for item in (*tfc_support, *pfsi_support)),
            key=_candidate_sort_key,
        )
    )
    for item in (*parsed_reported, *parsed_support):
        dimensions = _catalog_candidate_dimensions(catalog, item.company_id, item.metric_id)
        _validate_reported_candidate(
            catalog,
            item,
            dimensions,
        )
        trace = _normalization_trace(item)
        replayed = _replay_normalization(item.raw_value, trace)
        if replayed != item.normalized_value:
            raise Phase3Error(f"normalization replay failed: {item.candidate_id}")
    _validate_candidate_parity(assessments, parsed_reported, parsed_support)
    derived, blocked = _derive_candidates(
        catalog=catalog,
        assessments=assessments,
        reported=(*parsed_reported, *parsed_support),
    )
    missing = tuple(item for item in assessments if item.result_state == "NOT_DISCLOSED")
    knowledge_at = max(item.retrieved_at for item in evidence)
    return Phase3Dataset(
        knowledge_at=knowledge_at,
        catalog=catalog,
        evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        assessments=tuple(sorted(assessments, key=_assessment_sort_key)),
        reported_candidates=tuple(
            Phase3ReportedCandidate(
                item,
                _catalog_candidate_dimensions(catalog, item.company_id, item.metric_id),
                _source_methodology(item, (*tfc, *pfsi)),
                _normalization_rule(item),
                _normalization_trace(item),
            )
            for item in parsed_reported
        ),
        support_candidates=tuple(
            Phase3ReportedCandidate(
                item,
                _catalog_candidate_dimensions(catalog, item.company_id, item.metric_id),
                _source_methodology(item, (*tfc_support, *pfsi_support)),
                _normalization_rule(item),
                _normalization_trace(item),
            )
            for item in parsed_support
        ),
        derived_candidates=tuple(sorted(derived, key=_derived_sort_key)),
        blocked_derivations=tuple(sorted(blocked, key=_blocked_sort_key)),
        missing_cells=tuple(sorted(missing, key=_assessment_sort_key)),
    )


def _yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise Phase3Error(f"configuration root must be a mapping: {path}")
    _reject_float(loaded, str(path))
    return cast("dict[str, Any]", loaded)


def _reject_float(value: object, location: str) -> None:
    if isinstance(value, float):
        raise Phase3Error(f"binary float is prohibited in Phase 3 configuration: {location}")
    if isinstance(value, dict):
        for key, nested in value.items():
            _reject_float(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_float(nested, f"{location}[{index}]")


def _load_sources(
    root: Path,
    tfc_config: dict[str, Any],
    pfsi_config: dict[str, Any],
) -> tuple[dict[str, _Source], tuple[Phase3Evidence, ...]]:
    manifests: dict[str, dict[str, Any]] = {}
    manifest_paths = (
        root / "recorded_evidence" / "phase3" / "tfc" / "manifest.v1.yaml",
        root / "recorded_evidence" / "phase3" / "pfsi" / "manifest.v1.yaml",
    )
    evidence: list[Phase3Evidence] = []
    for manifest_path in manifest_paths:
        manifest = _yaml(manifest_path)
        issuer = str(manifest["issuer_id"])
        for item in cast("list[dict[str, Any]]", manifest["sources"]):
            path = manifest_path.parent / str(item["path"])
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != int(item["byte_length"]) or digest != str(item["sha256"]):
                raise PublicSourceError(f"recorded evidence integrity mismatch: {path.name}")
            url = str(item["url"])
            if url in manifests:
                raise Phase3Error(f"duplicate manifest URL: {url}")
            manifests[url] = item
            evidence.append(
                Phase3Evidence(
                    evidence_id=str(item["evidence_id"]),
                    source_key="",
                    company_id=issuer,
                    source_class=str(item["source_class"]),
                    accession=(str(item["accession"]) if item.get("accession") else None),
                    url=url,
                    period_end=_optional_date(item.get("period_of_report")),
                    published_at=_optional_datetime(
                        item.get("accepted_at", item.get("published_at"))
                    ),
                    retrieved_at=_datetime(item["retrieved_at"]),
                    sha256=digest,
                    byte_length=len(content),
                    media_type=str(item["media_type"]),
                    representation=str(item["representation"]),
                    capture_method=str(item["capture_method"]),
                    locator="; ".join(str(value) for value in item["locators"]),
                    parser_name="phase3_manifest_verifier",
                    parser_version=str(manifest["manifest_version"]),
                    retention_location=f"content-sha256://{digest}",
                    actual_fixture_path=path.resolve(),
                )
            )
    by_url = {item.url: item for item in evidence}
    sources: dict[str, _Source] = {}
    for config in (tfc_config, pfsi_config):
        for key, payload in cast("dict[str, dict[str, Any]]", config["sources"]).items():
            definition = RecordedSourceDefinition.from_mapping(
                key=key,
                payload=payload,
                config_root=root,
            )
            acquired = RecordedEvidenceAcquirer().acquire(definition)
            manifest_evidence = by_url.get(definition.url)
            if manifest_evidence is None:
                raise Phase3Error(f"configured source is absent from manifest: {key}")
            if (
                acquired.sha256 != manifest_evidence.sha256
                or acquired.byte_length != manifest_evidence.byte_length
            ):
                raise Phase3Error(f"source and manifest identities disagree: {key}")
            typed_evidence = replace(
                manifest_evidence,
                source_key=key,
                period_end=date.fromisoformat(definition.period_end),
                published_at=definition.published_at,
                locator=definition.locator,
                parser_name=definition.parser_name,
                parser_version=definition.parser_version,
            )
            by_url[definition.url] = typed_evidence
            sources[key] = _Source(definition, typed_evidence, acquired.content)
    return sources, tuple(by_url.values())


def _assessments(
    tfc_config: dict[str, Any],
    pfsi_config: dict[str, Any],
    catalog: MetricCatalog,
) -> tuple[Phase3CellAssessment, ...]:
    result: list[Phase3CellAssessment] = []
    tfc_cells = cast(
        "dict[str, dict[str, dict[str, Any]]]",
        cast("dict[str, Any]", tfc_config["eligible_source_assessment"])["cells"],
    )
    for metric_id, periods in tfc_cells.items():
        for period_text, raw in periods.items():
            source_keys = _assessment_source_keys(raw)
            result.append(
                Phase3CellAssessment(
                    company_id="tfc",
                    metric_id=metric_id,
                    period_end=date.fromisoformat(period_text),
                    reporting_entity_id="tfc_registrant",
                    reporting_scope_id=_scope("tfc", metric_id),
                    dimensions=_catalog_candidate_dimensions(catalog, "tfc", metric_id),
                    assessment_status=str(raw["assessment_status"]),
                    result_state=_published_state(str(raw["result_state"])),
                    source_keys=source_keys,
                    locators=_assessment_locators(raw),
                    reason_code=(str(raw["reason_code"]) if raw.get("reason_code") else None),
                )
            )
    pfsi_cells = cast(
        "list[dict[str, Any]]",
        cast("dict[str, Any]", pfsi_config["eligible_source_assessment"])["cells"],
    )
    check_profiles = cast("dict[str, str]", pfsi_config.get("eligible_check_profiles", {}))
    for raw in pfsi_cells:
        metric_id = str(raw["metric_id"])
        source_keys = tuple(str(value) for value in raw["checked_source_keys"])
        locators = tuple(
            str(value) for value in raw.get("checked_locators", raw.get("locators", []))
        )
        if not locators and str(raw["result_state"]) == "NOT_DISCLOSED":
            locators = tuple(check_profiles.get(source_key, "") for source_key in source_keys)
        if str(raw["result_state"]) == "NOT_DISCLOSED" and (
            len(locators) != len(source_keys) or any(not value.strip() for value in locators)
        ):
            raise Phase3Error(
                f"PFSI NOT_DISCLOSED provenance is incomplete: {metric_id}:{raw['period_end']}"
            )
        result.append(
            Phase3CellAssessment(
                company_id="pfsi",
                metric_id=metric_id,
                period_end=date.fromisoformat(str(raw["period_end"])),
                reporting_entity_id="pfsi_registrant",
                reporting_scope_id=_scope("pfsi", metric_id),
                dimensions=_catalog_candidate_dimensions(catalog, "pfsi", metric_id),
                assessment_status=str(raw["assessment_status"]),
                result_state=_published_state(str(raw["result_state"])),
                source_keys=source_keys,
                locators=locators,
                reason_code=(
                    str(raw.get("reason_code", raw.get("rationale")))
                    if raw.get("reason_code", raw.get("rationale"))
                    else None
                ),
            )
        )
    keys = {(item.company_id, item.metric_id, item.period_end) for item in result}
    if len(keys) != len(result):
        raise Phase3Error("Phase 3 disclosure matrix contains duplicate cells")
    return tuple(result)


def _parse_tfc(
    *,
    config: dict[str, Any],
    company: dict[str, Any],
    sources: dict[str, _Source],
    catalog: MetricCatalog,
) -> tuple[tuple[ParsedObservationCandidate, ...], tuple[ParsedObservationCandidate, ...]]:
    parser = StageARecordedDocumentParser()
    candidates: list[ParsedObservationCandidate] = []
    for key in cast("dict[str, Any]", config["sources"]):
        source = sources[key]
        parsed = parser.parse(
            source=source.definition,
            content=source.content,
            company=company,
            quarters=cast("list[dict[str, Any]]", config["quarters"]),
        )
        recipe_by_metric = {str(item["metric_id"]): item for item in source.definition.rows}
        for candidate in parsed:
            recipe = recipe_by_metric[candidate.metric_id]
            scope = (
                "tfc_owned_residential_msr"
                if candidate.metric_id.startswith("msr_")
                else candidate.reporting_scope_id
            )
            candidates.append(
                replace(
                    candidate,
                    metric_version=_metric_version(catalog, candidate.metric_id),
                    period_start=(
                        None
                        if candidate.metric_id == "msr_beginning_balance"
                        else candidate.period_start
                    ),
                    period_type=(
                        "instant"
                        if candidate.metric_id == "msr_beginning_balance"
                        else candidate.period_type
                    ),
                    reporting_scope_id=scope,
                    evidence_id=source.evidence.evidence_id,
                    evidence_locator=(
                        f"{source.definition.locator}; exact row '{recipe['raw_label']}'; "
                        f"period {candidate.period_end.isoformat()}"
                    ),
                )
            )
    support: list[ParsedObservationCandidate] = []
    reported: list[ParsedObservationCandidate] = []
    for candidate in candidates:
        if candidate.metric_id != "msr_beginning_balance":
            if candidate.period_type not in {"duration_ytd", "duration_annual"}:
                reported.append(candidate)
            continue
    return tuple(reported), tuple(support)


def _parse_pfsi(
    *,
    config: dict[str, Any],
    sources: dict[str, _Source],
    catalog: MetricCatalog,
) -> tuple[tuple[ParsedObservationCandidate, ...], tuple[ParsedObservationCandidate, ...]]:
    recipes = cast("dict[str, Any]", config["recipes"])
    candidates: list[ParsedObservationCandidate] = []
    support: list[ParsedObservationCandidate] = []
    earnings = cast("dict[str, Any]", recipes["q2_release_five_quarter_table"])
    earnings_source = sources[str(earnings["source_key"])]
    rows = _rows(earnings_source.content)
    header = tuple(str(value) for value in earnings["column_headers"])
    header_indices = [index for index, row in enumerate(rows) if row[: len(header)] == header]
    table_ranges = [
        rows[start + 1 : end]
        for start, end in zip(header_indices, (*header_indices[1:], len(rows)), strict=True)
    ]
    period_indices = cast("dict[str, int]", earnings["period_value_indices"])
    for recipe in cast("list[dict[str, Any]]", earnings["rows"]):
        label = str(recipe["raw_label"])
        metric_id = str(recipe["metric_id"])
        table_rows = _qualified_earnings_table(table_ranges, metric_id)
        matches = [row for row in table_rows if row[0] == label]
        occurrence = 0
        try:
            row = matches[occurrence]
        except IndexError as error:
            raise Phase3Error(f"PFSI earnings row not found: {label}") from error
        values = _numeric_tokens(row)
        for period_text, index in period_indices.items():
            raw_value = values[index]
            candidates.append(
                _candidate(
                    catalog=catalog,
                    source=earnings_source,
                    metric_id=metric_id,
                    period_end=date.fromisoformat(period_text),
                    period_type=(
                        "instant" if metric_id in _INSTANT_EARNINGS_METRICS else "duration"
                    ),
                    raw_label=label,
                    raw_value=raw_value,
                    normalization=str(recipe["normalization"]),
                    unit=str(recipe["unit"]),
                    methodology=str(recipe["methodology"]),
                    scope=str(recipe["reporting_scope_id"]),
                    locator=(
                        f"table '{earnings['table_anchor']}'; exact row '{label}'; "
                        f"column {period_text} (configured index {index})"
                    ),
                )
            )
    support.append(
        _candidate(
            catalog=catalog,
            source=earnings_source,
            metric_id="servicing_loan_count",
            period_end=date(2025, 6, 30),
            period_type="instant",
            raw_label="Total loans serviced (in thousands)",
            raw_value=_numeric_tokens(
                next(
                    row
                    for row in _qualified_earnings_table(table_ranges, "servicing_loan_count")
                    if row[0] == "Total loans serviced (in thousands)"
                )
            )[4],
            normalization="count_from_thousands",
            unit="count",
            methodology="TOTAL_SERVICING_PORTFOLIO_COUNT",
            scope="pfsi_total_servicing_portfolio",
            locator=(
                f"table '{earnings['table_anchor']}'; exact row "
                "'Total loans serviced (in thousands)'; column 2Q25"
            ),
        )
    )
    _parse_pfsi_expense(recipes, sources, catalog, candidates)
    _parse_pfsi_periodic(recipes, sources, catalog, candidates, support)
    support_metrics = {item.metric_id for item in support}
    if not support_metrics >= _SUPPORT_METRICS:
        missing = sorted(_SUPPORT_METRICS - support_metrics)
        raise Phase3Error(f"PFSI supporting facts are incomplete: {missing}")
    return tuple(candidates), tuple(support)


def _qualified_earnings_table(
    tables: list[list[tuple[str, ...]]], metric_id: str
) -> list[tuple[str, ...]]:
    """Select the configured servicing table, never a document-wide label match."""
    if metric_id == "servicing_adjusted_pretax_income":
        anchor = "Servicing pretax income net of valuation related changes"
    elif metric_id in {
        "total_servicing_upb",
        "subservicing_upb",
        "servicing_loan_count",
        "delinquency_60_plus_count_rate",
        "weighted_average_servicing_fee_bps",
    }:
        anchor = "Total UPB"
    else:
        anchor = "Net loan servicing fees"
    matches = [table for table in tables if any(row and row[0] == anchor for row in table)]
    if len(matches) != 1:
        raise Phase3Error(
            f"PFSI servicing table qualification failed for {metric_id}: "
            f"anchor={anchor!r}; matches={len(matches)}"
        )
    return matches[0]


def _parse_pfsi_expense(
    recipes: dict[str, Any],
    sources: dict[str, _Source],
    catalog: MetricCatalog,
    candidates: list[ParsedObservationCandidate],
) -> None:
    recipe = cast("dict[str, Any]", recipes["presentation_operating_expense"])
    for period_text, source_spec in cast("dict[str, dict[str, Any]]", recipe["by_period"]).items():
        source = sources[str(source_spec["source_key"])]
        anchor = str(recipe["table_anchor"])
        slide_rows = [
            " | ".join(row)
            for row in _rows(source.content)
            if "Servicing expenses: Operating expenses" in " | ".join(row)
            and "Payoff-related expense" in " | ".join(row)
        ]
        if len(slide_rows) != 1:
            raise Phase3Error(
                f"PFSI expense slide-row qualification failed: {period_text}; "
                f"matches={len(slide_rows)}"
            )
        bounded = slide_rows[0]
        if anchor.upper() not in bounded.upper() and "SERVICING" not in bounded.upper():
            raise Phase3Error(f"PFSI expense table anchor not found: {period_text}")
        headers = tuple(str(value) for value in source_spec["column_headers"])
        header_positions = tuple(bounded.find(value) for value in headers)
        if (
            any(index < 0 for index in header_positions)
            or tuple(sorted(header_positions)) != header_positions
        ):
            raise Phase3Error(f"PFSI expense headers are absent or out of order: {period_text}")
        match = re.search(
            r"Operating expenses\s+(.+?)\s+Payoff-related expense",
            bounded,
            flags=re.DOTALL,
        )
        if match is None:
            raise Phase3Error(f"PFSI operating-expense sequence not found: {period_text}")
        values = re.findall(r"\([\d,]+(?:\.\d+)?\)|[\d,]+(?:\.\d+)?", match.group(1))
        if len(values) != 6:
            raise Phase3Error(
                f"PFSI operating-expense triplet structure is not exact: {period_text}"
            )
        raw_value = values[-2]
        candidates.append(
            _candidate(
                catalog=catalog,
                source=source,
                metric_id="servicing_operating_expense",
                period_end=date.fromisoformat(period_text),
                period_type="duration",
                raw_label=str(recipe["raw_label"]),
                raw_value=raw_value,
                normalization=str(recipe["normalization"]),
                unit=str(recipe["unit"]),
                methodology=str(recipe["methodology"]),
                scope=str(recipe["reporting_scope_id"]),
                locator=(
                    f"filed presentation slide table '{anchor}'; visible-text sequence "
                    f"under headers {source_spec['column_headers']}; exact "
                    "'Operating expenses' token"
                ),
            )
        )


def _parse_pfsi_periodic(
    recipes: dict[str, Any],
    sources: dict[str, _Source],
    catalog: MetricCatalog,
    candidates: list[ParsedObservationCandidate],
    support: list[ParsedObservationCandidate],
) -> None:
    portfolio = cast("dict[str, Any]", recipes["periodic_portfolio_and_delinquency"])
    fair_recipe = cast("dict[str, Any]", recipes["periodic_msr_fair_value_and_related_upb"])
    roll_recipe = cast("dict[str, Any]", recipes["periodic_msr_rollforward"])
    for period_text, period_spec in cast(
        "dict[str, dict[str, Any]]", portfolio["by_period"]
    ).items():
        period_end = date.fromisoformat(period_text)
        source = sources[str(period_spec["source_key"])]
        rows = _rows(source.content)
        total_index = int(period_spec["total_row"])
        total = rows[total_index]
        component_labels = {
            "FHA",
            "VA",
            "USDA",
            "Freddie Mac",
            "Fannie Mae",
            "Closed-end second lien mortgage loans",
            "Other (3)",
        }
        components = {
            rows[index][0]: rows[index]
            for index in range(max(0, total_index - 20), total_index)
            if rows[index][0] in component_labels
        }
        for metric_id, label in (
            ("fhlmc_servicing_upb", "Freddie Mac"),
            ("fnma_servicing_upb", "Fannie Mae"),
        ):
            candidates.append(
                _periodic_row_candidate(
                    catalog, source, metric_id, period_end, components[label], label
                )
            )
        support_labels = {
            "fha_servicing_upb": "FHA",
            "va_servicing_upb": "VA",
            "usda_servicing_upb": "USDA",
            "closed_end_second_lien_servicing_upb": "Closed-end second lien mortgage loans",
            "other_servicing_upb": "Other (3)",
        }
        for metric_id, label in support_labels.items():
            support.append(
                _periodic_row_candidate(
                    catalog, source, metric_id, period_end, components[label], label
                )
            )
        support.append(
            _candidate(
                catalog=catalog,
                source=source,
                metric_id="owned_msr_msl_upb",
                period_end=period_end,
                period_type="instant",
                raw_label="source-reported total",
                raw_value=_numeric_tokens(total)[0],
                normalization="usd_from_thousands",
                unit="USD",
                methodology="ISSUER_REPORTED",
                scope="pfsi_owned_msr_and_msl_portfolio",
                locator=(
                    f"table '{portfolio['table_anchor']}'; exact total row {total_index}; "
                    "Unpaid principal balance column"
                ),
            )
        )
        candidates.append(
            _candidate(
                catalog=catalog,
                source=source,
                metric_id="delinquency_60_plus_upb_rate",
                period_end=period_end,
                period_type="instant",
                raw_label="source-reported total",
                raw_value=_numeric_tokens(total)[-1],
                normalization="percent_to_ratio",
                unit="ratio",
                methodology="ISSUER_REPORTED_UPB_WEIGHTED_OWNED_MSR_PORTFOLIO",
                scope="pfsi_owned_msr_and_msl_portfolio",
                locator=(
                    f"table '{portfolio['table_anchor']}'; exact total row {total_index}; "
                    "60+ Delinquency (by UPB) column"
                ),
            )
        )
        delinquent_index = next(
            index
            for index in range(total_index - 50, total_index)
            if rows[index][0] == "Delinquencies:" and rows[index + 1][0] == "Owned servicing:"
        )
        owned_delinquency = {
            rows[index][0]: rows[index]
            for index in range(delinquent_index + 2, delinquent_index + 6)
        }
        owned_total_raw = _numeric_tokens(rows[delinquent_index + 4])[0]
        detailed_matches = [
            index
            for index in range(len(rows) - 7)
            if rows[index][0] == "Delinquent loans:"
            and rows[index + 1][0] == "30 days"
            and rows[index + 2][0] == "60 days"
            and rows[index + 3][0] == "90 days or more:"
            and rows[index + 4][0] == "Not in foreclosure"
            and rows[index + 5][0] == "In foreclosure"
            and rows[index + 6][0] == "Foreclosed"
            and _numeric_tokens(rows[index + 7])[0] == owned_total_raw
        ]
        if len(detailed_matches) != 1:
            raise Phase3Error(
                f"PFSI detailed owned-delinquency table is not unique for {period_text}: "
                f"matches={detailed_matches}"
            )
        detailed_anchor = detailed_matches[0]
        foreclosure_row = rows[detailed_anchor + 5]
        for metric_id, label, raw_value in (
            (
                "delinquency_30_to_89_upb",
                "30-89 days",
                _numeric_tokens(owned_delinquency["30-89 days"])[0],
            ),
            (
                "delinquency_90_plus_upb",
                "90 days or more",
                _numeric_tokens(owned_delinquency["90 days or more"])[0],
            ),
            (
                "foreclosure_upb",
                "In foreclosure",
                _numeric_tokens(foreclosure_row)[0],
            ),
        ):
            support.append(
                _candidate(
                    catalog=catalog,
                    source=source,
                    metric_id=metric_id,
                    period_end=period_end,
                    period_type="instant",
                    raw_label=label,
                    raw_value=raw_value,
                    normalization="usd_from_thousands_with_reported_dash_zero",
                    unit="USD",
                    methodology="ISSUER_REPORTED",
                    scope="pfsi_owned_msr_and_msl_portfolio",
                    locator=(
                        "loan-servicing-portfolio table; 'Delinquencies' > "
                        f"'Owned servicing'; exact row '{label}'; exact current owned "
                        "servicing column"
                    ),
                )
            )
        if period_end != date(2025, 9, 30):
            interim_matches = [
                row
                for row in rows[max(0, total_index - 30) : total_index + 10]
                if row[0] == "Interim servicing"
            ]
            if len(interim_matches) != 1:
                raise Phase3Error(
                    f"PFSI portfolio-bounded Interim servicing row count is "
                    f"{len(interim_matches)} for {period_text}"
                )
            interim = interim_matches[0]
            raw_interim = _numeric_tokens(interim, dashes_as_zero=True)[0]
            candidates.append(
                _candidate(
                    catalog=catalog,
                    source=source,
                    metric_id="interim_servicing_upb",
                    period_end=period_end,
                    period_type="instant",
                    raw_label="Interim servicing",
                    raw_value=raw_interim,
                    normalization="usd_from_thousands_with_reported_dash_zero",
                    unit="USD",
                    methodology="ISSUER_REPORTED",
                    scope="pfsi_interim_servicing_portfolio",
                    locator=(
                        "unpaid principal balance of loan servicing portfolio; "
                        "Interim servicing row; current-period column"
                    ),
                )
            )
        fair_source = sources[
            str(cast("dict[str, Any]", fair_recipe["by_period"])[period_text]["source_key"])
        ]
        fair_rows = _rows(fair_source.content)
        roll_spec = cast("dict[str, Any]", roll_recipe["by_period"])[period_text]
        roll_source = sources[str(roll_spec["source_key"])]
        roll_rows = _rows(roll_source.content)
        roll_anchor = next(
            index
            for index, row in enumerate(roll_rows)
            if row[0] == "MSRs resulting from loan sales"
        )
        roll = {
            roll_rows[index][0]: roll_rows[index]
            for index in range(roll_anchor - 2, roll_anchor + 15)
        }
        ending_label = next(label for label in roll if label.startswith("Balance at end"))
        ending_raw = _numeric_tokens(roll[ending_label])[0]
        related_label = next(
            label
            for label in roll
            if label.startswith("Unpaid principal balance of underlying loans at end")
        )
        related_raw = _numeric_tokens(roll[related_label])[0]
        for metric_id, label, raw_value in (
            ("owned_msr_upb", related_label, related_raw),
            ("msr_fair_value", ending_label, ending_raw),
            ("msr_ending_balance", ending_label, ending_raw),
        ):
            candidates.append(
                _candidate(
                    catalog=catalog,
                    source=fair_source,
                    metric_id=metric_id,
                    period_end=period_end,
                    period_type="instant",
                    raw_label=label,
                    raw_value=raw_value,
                    normalization="usd_from_thousands",
                    unit="USD",
                    methodology="FAIR_VALUE"
                    if metric_id != "owned_msr_upb"
                    else "RELATED_LOANS_UNDERLYING_RECOGNIZED_MSR_FAIR_VALUE",
                    scope="pfsi_owned_msr_portfolio",
                    locator=(
                        f"table '{fair_recipe['table_anchor']}'; exact row '{label}'; "
                        "current-period column"
                    ),
                )
            )
        if period_end != date(2025, 12, 31):
            beginning_label = next(
                label for label in roll if label.startswith("Balance at beginning")
            )
            for metric_id, label, raw_value in (
                (
                    "msr_beginning_balance",
                    beginning_label,
                    _numeric_tokens(roll[beginning_label])[0],
                ),
                (
                    "msr_additions",
                    "MSRs resulting from loan sales",
                    _numeric_tokens(roll["MSRs resulting from loan sales"])[0],
                ),
            ):
                parsed = _candidate(
                    catalog=catalog,
                    source=roll_source,
                    metric_id=metric_id,
                    period_end=(
                        _PERIOD_START[period_end]
                        if metric_id == "msr_beginning_balance"
                        else period_end
                    ),
                    period_type=("instant" if metric_id == "msr_beginning_balance" else "duration"),
                    raw_label=label,
                    raw_value=raw_value,
                    normalization="usd_from_thousands",
                    unit="USD",
                    methodology="ISSUER_REPORTED",
                    scope="pfsi_owned_msr_portfolio",
                    locator=(
                        f"MSR fair-value roll-forward '{roll_recipe['table_anchor']}'; "
                        f"exact row/component '{label}'; current-quarter column"
                    ),
                )
                if metric_id != "msr_beginning_balance":
                    candidates.append(parsed)
        if period_end != date(2025, 12, 31):
            characteristics = next(
                index
                for index, row in enumerate(fair_rows)
                if row[0] == "MSR and underlying loan characteristics:"
            )
            upb_row = next(
                row
                for row in fair_rows[characteristics + 1 : characteristics + 5]
                if row[0].startswith("Unpaid principal balance")
            )
            support.append(
                _candidate(
                    catalog=catalog,
                    source=fair_source,
                    metric_id="msr_additions_related_upb",
                    period_end=period_end,
                    period_type="duration",
                    raw_label=upb_row[0],
                    raw_value=_numeric_tokens(upb_row)[0],
                    normalization="usd_from_thousands",
                    unit="USD",
                    methodology="ISSUER_REPORTED",
                    scope="pfsi_owned_msr_portfolio",
                    locator=(
                        "table 'MSR and underlying loan characteristics'; exact "
                        f"'{upb_row[0]}' row; current-quarter column"
                    ),
                )
            )


def _periodic_row_candidate(
    catalog: MetricCatalog,
    source: _Source,
    metric_id: str,
    period_end: date,
    row: tuple[str, ...],
    label: str,
) -> ParsedObservationCandidate:
    return _candidate(
        catalog=catalog,
        source=source,
        metric_id=metric_id,
        period_end=period_end,
        period_type="instant",
        raw_label=label,
        raw_value=_numeric_tokens(row)[0],
        normalization="usd_from_thousands",
        unit="USD",
        methodology="ISSUER_REPORTED_OWNED_MSR_INVESTOR_MIX",
        scope="pfsi_owned_msr_and_msl_portfolio",
        locator=(
            "table 'Loan type | Unpaid principal balance | Loan count'; "
            f"exact row '{label}'; Unpaid principal balance column"
        ),
    )


def _candidate(
    *,
    catalog: MetricCatalog,
    source: _Source,
    metric_id: str,
    period_end: date,
    period_type: str,
    raw_label: str,
    raw_value: str,
    normalization: str,
    unit: str,
    methodology: str,
    scope: str,
    locator: str,
) -> ParsedObservationCandidate:
    normalized = _normalize(raw_value, normalization)
    canonical_unit = "ratio" if normalization == "percent_to_ratio" else unit
    candidate_id = hashlib.sha256(
        f"{source.evidence.sha256}:{metric_id}:{period_end}:{raw_value}:{scope}".encode()
    ).hexdigest()[:32]
    start = _PERIOD_START.get(period_end) if period_type != "instant" else None
    fiscal_quarter = ((period_end.month - 1) // 3) + 1
    return ParsedObservationCandidate(
        candidate_id=candidate_id,
        company_id="pfsi",
        metric_id=metric_id,
        metric_version=_metric_version(catalog, metric_id),
        period_start=start,
        period_end=period_end,
        fiscal_year=period_end.year,
        fiscal_quarter=fiscal_quarter,
        period_type=period_type,
        raw_label=raw_label,
        raw_value=raw_value,
        normalized_value=normalized,
        currency="USD" if canonical_unit == "USD" else None,
        unit=canonical_unit,
        reported_scale=_reported_scale(normalization),
        reported_decimals=decimal_places(raw_value),
        observation_state=ObservationState.REPORTED_ACTUAL,
        methodology=methodology,
        reporting_entity_id="pfsi_registrant",
        reporting_scope_id=scope,
        evidence_id=source.evidence.evidence_id,
        evidence_locator=f"{source.definition.locator}; {locator}",
        extraction_method="deterministic_html_table",
        parser_name=source.definition.parser_name,
        parser_version=source.definition.parser_version,
    )


def _normalize(raw: str, rule: str) -> Decimal:
    if rule == "usd_from_thousands_with_reported_dash_zero" and raw in {
        "—",
        "â€”",
        "--",
    }:
        return Decimal(0)
    aliases = {
        "signed_usd_from_millions": "usd_from_millions",
        "expense_magnitude_from_millions": "usd_from_millions",
        "reduction_magnitude_from_parenthetical_millions": "usd_from_millions",
        "count_from_thousands": "identity",
        "usd_from_thousands": "identity",
        "reduction_magnitude_from_thousands": "identity",
        "signed_usd_from_thousands": "identity",
        "usd_from_thousands_with_reported_dash_zero": "identity",
    }
    effective = aliases.get(rule, rule)
    value = normalize_reported_value(raw, rule=effective)
    if rule in {
        "count_from_thousands",
        "usd_from_thousands",
        "reduction_magnitude_from_thousands",
        "signed_usd_from_thousands",
        "usd_from_thousands_with_reported_dash_zero",
    }:
        value *= Decimal(1000)
    if rule.startswith(("expense_magnitude", "reduction_magnitude")):
        value = abs(value)
    return value


def _derive_candidates(
    *,
    catalog: MetricCatalog,
    assessments: tuple[Phase3CellAssessment, ...],
    reported: tuple[ParsedObservationCandidate, ...],
) -> tuple[tuple[Phase3DerivedCandidate, ...], tuple[Phase3BlockedDerivation, ...]]:
    by_key = {(item.company_id, item.metric_id, item.period_end): item for item in reported}
    derived: list[Phase3DerivedCandidate] = []
    blocked: list[Phase3BlockedDerivation] = []
    for assessment in assessments:
        if assessment.result_state != "DERIVED":
            continue
        definition = catalog.versions(assessment.metric_id)[-1]
        if definition.derivation is None:
            blocked.append(
                Phase3BlockedDerivation(
                    assessment.company_id,
                    assessment.metric_id,
                    assessment.period_end,
                    None,
                    (),
                    "governed catalog has no derivation definition",
                )
            )
            continue
        role_candidates: list[tuple[str, ParsedObservationCandidate]] = []
        missing: list[str] = []
        for requirement in definition.derivation.inputs:
            input_candidate = _select_input(
                by_key=by_key,
                assessment=assessment,
                role=requirement.role,
                metric_ids=requirement.metric_ids,
            )
            if input_candidate is None:
                missing.extend(requirement.metric_ids)
            else:
                role_candidates.append((requirement.role, input_candidate))
        if missing:
            blocked.append(
                Phase3BlockedDerivation(
                    assessment.company_id,
                    assessment.metric_id,
                    assessment.period_end,
                    definition.derivation.formula_version,
                    tuple(sorted(set(missing))),
                    "one or more governed published inputs are unavailable",
                )
            )
            continue
        dimensions = _dimensions(assessment.metric_id)
        request_id = hashlib.sha256(
            f"derived:{assessment.company_id}:{assessment.metric_id}:{assessment.period_end}".encode()
        ).hexdigest()[:32]
        period_type = (
            PeriodType.INSTANT
            if definition.period_types == (PeriodType.INSTANT,)
            else PeriodType.DURATION
        )
        period_start = (
            _PERIOD_START[assessment.period_end] if period_type is not PeriodType.INSTANT else None
        )
        component_formula = definition.derivation.formula.value in {
            "SUM_INPUTS",
            "RATIO",
            "SUM_INPUTS_OVER_DENOMINATOR",
        }
        metric_inputs = tuple(
            (
                role,
                _metric_input(
                    candidate,
                    dimensions=(
                        _catalog_input_dimensions(catalog, candidate.metric_id, dimensions)
                        if component_formula
                        else dimensions
                    ),
                ),
            )
            for role, candidate in role_candidates
        )
        averaging = None
        if definition.derivation.averaging.value == "ARITHMETIC_BEGIN_END":
            beginning = next(item for role, item in role_candidates if role.startswith("beginning"))
            ending = next(item for role, item in role_candidates if role.startswith("ending"))
            averaging = AveragingParameters(beginning.period_end, ending.period_end)
        annualization = None
        if definition.derivation.annualization.value != "NONE":
            assert period_start is not None
            annualization = AnnualizationParameters(
                observed_days=(assessment.period_end - period_start).days + 1,
                basis_days=Decimal(365),
            )
        request = DerivationRequest(
            derived_observation_id=request_id,
            metric_id=assessment.metric_id,
            metric_version=definition.semantic_version,
            issuer_id=assessment.company_id,
            reporting_entity_id=assessment.reporting_entity_id,
            reporting_scope_id=assessment.reporting_scope_id,
            period_type=period_type,
            period_start=period_start,
            period_end=assessment.period_end,
            dimensions=dimensions,
            inputs=metric_inputs,
            averaging=averaging,
            annualization=annualization,
        )
        decision = derive_metric(request, catalog)
        if decision.result is None:
            blocked.append(
                Phase3BlockedDerivation(
                    assessment.company_id,
                    assessment.metric_id,
                    assessment.period_end,
                    definition.derivation.formula_version,
                    (),
                    ",".join(reason.value for reason in decision.reasons),
                )
            )
            continue
        result = decision.result
        candidate_by_id = {item.candidate_id: item for _, item in role_candidates}
        evidence_ids = tuple(
            dict.fromkeys(
                candidate_by_id[item.input_observation_id].evidence_id for item in result.lineage
            )
        )
        derived.append(
            Phase3DerivedCandidate(
                candidate_id=result.observation_id,
                company_id=result.issuer_id,
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
                input_candidate_ids=tuple(item.input_observation_id for item in result.lineage),
                input_roles=tuple(item.input_role for item in result.lineage),
                evidence_ids=evidence_ids,
                evidence_locator="derived from exact published candidates: "
                + ", ".join(item.input_observation_id for item in result.lineage),
                request=request,
                trace=result.trace,
            )
        )
    return tuple(derived), tuple(blocked)


def _select_input(
    *,
    by_key: dict[tuple[str, str, date], ParsedObservationCandidate],
    assessment: Phase3CellAssessment,
    role: str,
    metric_ids: tuple[str, ...],
) -> ParsedObservationCandidate | None:
    target = assessment.period_end
    if role.startswith("beginning"):
        target = _previous_period_end(assessment.period_end)
    for metric_id in metric_ids:
        found = by_key.get((assessment.company_id, metric_id, target))
        if found is not None:
            return found
    return None


def _metric_input(
    candidate: ParsedObservationCandidate,
    *,
    dimensions: tuple[MetricDimension, ...],
) -> MetricInput:
    period_type = _period_type(candidate.period_type)
    return MetricInput(
        observation_id=candidate.candidate_id,
        issuer_id=candidate.company_id,
        metric_id=candidate.metric_id,
        metric_version=candidate.metric_version,
        value=candidate.normalized_value,
        unit=MetricUnit(candidate.unit),
        period_type=period_type,
        period_start=candidate.period_start,
        period_end=candidate.period_end,
        reporting_entity_id=candidate.reporting_entity_id,
        reporting_scope_id=candidate.reporting_scope_id,
        methodology=MetricMethodology(candidate.methodology),
        publication_status=PublicationStatus.PUBLISHED,
        value_state=ValueState.REPORTED_ACTUAL,
        completeness=Completeness.COMPLETE,
        dimensions=dimensions,
    )


def _dimensions(metric_id: str) -> tuple[MetricDimension, ...]:
    values: dict[str, dict[str, str]] = {
        "cost_to_service_per_loan": {"portfolio_population": "total_servicing"},
        "msr_fair_value_multiple_of_related_upb": {"msr_population": "owned_msr"},
        "msr_fair_value_bps_of_related_upb": {"msr_population": "owned_msr"},
        "capitalized_servicing_rate_on_additions": {"msr_population": "owned_msr"},
        "government_servicing_upb": {
            "portfolio_population": "owned_msr_and_msl",
            "portfolio_mix_category": "government",
            "portfolio_mix_basis": "upb",
            "portfolio_mix_overlap": "mutually_exclusive",
        },
        "conventional_servicing_upb": {
            "portfolio_population": "owned_msr_and_msl",
            "portfolio_mix_category": "conventional",
            "portfolio_mix_basis": "upb",
            "portfolio_mix_overlap": "mutually_exclusive",
        },
    }
    if metric_id.startswith("delinquency_") and metric_id.endswith("_upb_rate"):
        threshold = metric_id.removeprefix("delinquency_").removesuffix("_upb_rate")
        values[metric_id] = {
            "portfolio_population": "owned_msr_and_msl",
            "delinquency_measure_basis": "upb",
            "delinquency_threshold": threshold,
            "delinquency_denominator": "unpaid_principal_balance",
            "delinquency_foreclosure_treatment": "included",
            "delinquency_bankruptcy_treatment": "source_defined",
            "delinquency_forbearance_treatment": "source_defined",
        }
    if metric_id == "foreclosure_upb_rate":
        values[metric_id] = {
            "portfolio_population": "owned_msr_and_msl",
            "delinquency_measure_basis": "upb",
            "delinquency_threshold": "foreclosure",
            "delinquency_denominator": "unpaid_principal_balance",
            "delinquency_foreclosure_treatment": "separately_reported",
            "delinquency_bankruptcy_treatment": "source_defined",
            "delinquency_forbearance_treatment": "source_defined",
        }
    return tuple(
        MetricDimension(name, value) for name, value in sorted(values.get(metric_id, {}).items())
    )


def _input_dimensions(metric_id: str) -> tuple[MetricDimension, ...]:
    mix = {
        "fha_servicing_upb": "fha",
        "va_servicing_upb": "va",
        "usda_servicing_upb": "usda",
        "fnma_servicing_upb": "fnma",
        "fhlmc_servicing_upb": "fhlmc",
        "closed_end_second_lien_servicing_upb": "closed_end_second_lien",
        "other_servicing_upb": "other",
    }
    if metric_id in mix:
        return tuple(
            MetricDimension(name, value)
            for name, value in sorted(
                {
                    "portfolio_population": "owned_msr_and_msl",
                    "portfolio_mix_category": mix[metric_id],
                    "portfolio_mix_basis": "upb",
                    "portfolio_mix_overlap": "mutually_exclusive",
                }.items()
            )
        )
    if metric_id in {"delinquency_30_to_89_upb", "delinquency_90_plus_upb", "foreclosure_upb"}:
        threshold = {
            "delinquency_30_to_89_upb": "30_to_89",
            "delinquency_90_plus_upb": "90_plus",
            "foreclosure_upb": "foreclosure",
        }[metric_id]
        return tuple(
            MetricDimension(name, value)
            for name, value in sorted(
                {
                    "portfolio_population": "owned_msr_and_msl",
                    "delinquency_measure_basis": "upb",
                    "delinquency_threshold": threshold,
                    "delinquency_denominator": "unpaid_principal_balance",
                    "delinquency_foreclosure_treatment": (
                        "excluded"
                        if metric_id == "delinquency_30_to_89_upb"
                        else "included"
                        if metric_id == "delinquency_90_plus_upb"
                        else "separately_reported"
                    ),
                    "delinquency_bankruptcy_treatment": "source_defined",
                    "delinquency_forbearance_treatment": "source_defined",
                }.items()
            )
        )
    if metric_id == "owned_msr_msl_upb":
        return (MetricDimension("portfolio_population", "owned_msr_and_msl"),)
    if metric_id in _PFSI_MSR_METRICS:
        return (MetricDimension("msr_population", "owned_msr"),)
    if metric_id == "servicing_loan_count":
        return (MetricDimension("portfolio_population", "total_servicing"),)
    return ()


def _catalog_input_dimensions(
    catalog: MetricCatalog,
    metric_id: str,
    output_dimensions: tuple[MetricDimension, ...],
) -> tuple[MetricDimension, ...]:
    definition = catalog.versions(metric_id)[-1]
    output = {item.name: item.value for item in output_dimensions}
    dimensions: list[MetricDimension] = []
    for requirement in definition.dimensions:
        value = requirement.fixed_value or output.get(requirement.taxonomy)
        if value is None:
            raise Phase3Error(
                f"cannot resolve input dimension {requirement.taxonomy} for {metric_id}"
            )
        dimensions.append(MetricDimension(requirement.taxonomy, value))
    return tuple(sorted(dimensions))


def _validate_candidate_parity(
    assessments: tuple[Phase3CellAssessment, ...],
    reported: tuple[ParsedObservationCandidate, ...],
    support: tuple[ParsedObservationCandidate, ...],
) -> None:
    expected = {
        (item.company_id, item.metric_id, item.period_end)
        for item in assessments
        if item.result_state == "PUBLISHED"
    }
    grid_support = tuple(item for item in support if item.metric_id in _SUPPORT_METRICS)
    actual = {
        (item.company_id, item.metric_id, item.period_end) for item in (*reported, *grid_support)
    }
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise Phase3Error(
            f"reported-candidate disclosure parity failed; missing={missing}; extra={extra}"
        )
    if len(actual) != len((*reported, *grid_support)):
        raise Phase3Error("published candidates contain duplicate grid cells")
    support_ids = {item.candidate_id for item in support}
    if len(support_ids) != len(support) or support_ids & {item.candidate_id for item in reported}:
        raise Phase3Error("reported/support candidates contain duplicate identities")


def _scope(company_id: str, metric_id: str) -> str:
    if company_id == "tfc":
        return (
            "tfc_owned_residential_msr"
            if metric_id.startswith("msr_")
            else "tfc_consolidated_residential_mortgage_servicing"
        )
    if metric_id in _PFSI_MSR_METRICS:
        return "pfsi_owned_msr_portfolio"
    if metric_id in _PFSI_TOTAL_METRICS - {"cost_to_service_per_loan"}:
        return "pfsi_total_servicing_portfolio"
    if metric_id in _PFSI_OWNED_MSL_METRICS:
        return "pfsi_owned_msr_and_msl_portfolio"
    if metric_id == "subservicing_upb":
        return "pfsi_subservicing_portfolio"
    if metric_id == "interim_servicing_upb":
        return "pfsi_interim_servicing_portfolio"
    return "pfsi_servicing_segment"


def _assessment_dimensions(
    company_id: str,
    metric_id: str,
) -> tuple[MetricDimension, ...]:
    """Return the complete governed semantic dimensions for any matrix cell."""
    if metric_id == "weighted_average_servicing_fee_bps":
        return (
            MetricDimension(
                "portfolio_population",
                "servicing_for_others" if company_id == "tfc" else "owned_msr",
            ),
        )
    if company_id == "pfsi":
        specific = _input_dimensions(metric_id)
        if specific:
            return specific
        derived = _dimensions(metric_id)
        if derived:
            return derived
    population = (
        "servicing_for_others"
        if metric_id == "servicing_for_others_upb"
        or (company_id == "tfc" and metric_id == "weighted_average_servicing_fee_bps")
        else "owned_msr"
        if company_id == "pfsi"
        and metric_id in {"weighted_average_servicing_fee_bps", "delinquency_60_plus_count_rate"}
        else "subservicing"
        if metric_id == "subservicing_upb"
        else "interim_servicing"
        if metric_id == "interim_servicing_upb"
        else "total_servicing"
        if metric_id in {"total_servicing_upb", "servicing_loan_count"}
        else "owned_msr"
        if metric_id.startswith("msr_")
        else "issuer_disclosed"
    )
    mix_category = {
        "government_servicing_upb": "government",
        "conventional_servicing_upb": "conventional",
        "gnma_servicing_upb": "gnma",
        "fnma_servicing_upb": "fnma",
        "fhlmc_servicing_upb": "fhlmc",
    }.get(metric_id)
    if mix_category is not None:
        return tuple(
            MetricDimension(name, value)
            for name, value in sorted(
                {
                    "portfolio_population": population,
                    "portfolio_mix_category": mix_category,
                    "portfolio_mix_basis": "upb",
                    "portfolio_mix_overlap": "source_defined",
                }.items()
            )
        )
    if "delinquency_" in metric_id and metric_id.endswith("_rate"):
        basis = "count" if "count" in metric_id else "upb"
        threshold = metric_id.removeprefix("delinquency_").split("_")[0] + "_plus"
        return tuple(
            MetricDimension(name, value)
            for name, value in sorted(
                {
                    "portfolio_population": population,
                    "delinquency_measure_basis": basis,
                    "delinquency_threshold": threshold,
                    "delinquency_denominator": (
                        "loan_count" if basis == "count" else "unpaid_principal_balance"
                    ),
                    "delinquency_foreclosure_treatment": "included",
                    "delinquency_bankruptcy_treatment": "source_defined",
                    "delinquency_forbearance_treatment": "source_defined",
                }.items()
            )
        )
    if metric_id == "foreclosure_upb_rate":
        return _dimensions(metric_id)
    return ()


def _catalog_candidate_dimensions(
    catalog: MetricCatalog,
    company_id: str,
    metric_id: str,
) -> tuple[MetricDimension, ...]:
    """Resolve every dimension required by the latest governed definition."""
    definition = catalog.versions(metric_id)[-1]
    suggested = {item.name: item.value for item in _assessment_dimensions(company_id, metric_id)}
    dimensions: list[MetricDimension] = []
    for requirement in definition.dimensions:
        value = requirement.fixed_value or suggested.get(requirement.taxonomy)
        if value is None:
            defaults = {
                "portfolio_population": (
                    "owned_msr_and_msl"
                    if company_id == "pfsi" and metric_id in _PFSI_OWNED_MSL_METRICS
                    else "issuer_disclosed"
                ),
                "portfolio_mix_overlap": "source_defined",
                "delinquency_foreclosure_treatment": "included",
                "delinquency_bankruptcy_treatment": "source_defined",
                "delinquency_forbearance_treatment": "source_defined",
                "msr_population": "owned_msr",
            }
            value = defaults.get(requirement.taxonomy)
        if value is None:
            raise Phase3Error(
                f"cannot resolve assessment dimension {requirement.taxonomy} for "
                f"{company_id}:{metric_id}"
            )
        dimensions.append(MetricDimension(requirement.taxonomy, value))
    return tuple(sorted(dimensions))


def _rows(content: bytes) -> list[tuple[str, ...]]:
    collector = _TableRows()
    try:
        collector.feed(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise Phase3Error("retained Phase 3 HTML is not UTF-8") from error
    return collector.rows


def _numeric_tokens(
    row: tuple[str, ...],
    *,
    dashes_as_zero: bool = False,
) -> tuple[str, ...]:
    text = " ".join(row[1:])
    text = re.sub(r"\(\s+([\d,]+(?:\.\d+)?)\s+\)", r"(\1)", text)
    tokens = re.findall(r"\([\d,]+(?:\.\d+)?\)|[\d,]+(?:\.\d+)?|—|--", text)
    values: list[str] = []
    for token in tokens:
        if token in {"—", "--"}:
            if dashes_as_zero:
                values.append(token)
            continue
        values.append(token)
    if not values:
        raise Phase3Error(f"source row contains no exact numeric text: {row[0]}")
    return tuple(values)


def _decimal_text_sum(*values: str, absolute: bool = False) -> str:
    total = sum((_source_decimal(value) for value in values), Decimal(0))
    if absolute:
        total = abs(total)
    return format(total, "f")


def _source_decimal(raw: str) -> Decimal:
    negative = raw.startswith("(") and raw.endswith(")")
    value = Decimal(raw.strip("()").replace(",", ""))
    return -value if negative else value


def _metric_version(catalog: MetricCatalog, metric_id: str) -> str:
    versions = catalog.versions(metric_id)
    if not versions:
        raise Phase3Error(f"Phase 3 candidate metric is absent from catalog: {metric_id}")
    return versions[-1].semantic_version


def _period_type(raw: str) -> PeriodType:
    aliases = {"duration_annual": PeriodType.ANNUAL, "annual": PeriodType.ANNUAL}
    aliased = aliases.get(raw)
    return aliased if aliased is not None else PeriodType(raw)


def _source_methodology(
    candidate: ParsedObservationCandidate,
    originals: tuple[ParsedObservationCandidate, ...],
) -> str:
    matches = [
        item.methodology
        for item in originals
        if (
            item.company_id,
            item.metric_id,
            item.period_end,
            item.raw_label,
            item.raw_value,
            item.evidence_id,
        )
        == (
            candidate.company_id,
            candidate.metric_id,
            candidate.period_end,
            candidate.raw_label,
            candidate.raw_value,
            candidate.evidence_id,
        )
    ]
    if len(matches) != 1:
        raise Phase3Error(
            f"source methodology identity is not unique for candidate {candidate.candidate_id}"
        )
    return matches[0]


def _governed_candidate(
    catalog: MetricCatalog, candidate: ParsedObservationCandidate
) -> ParsedObservationCandidate:
    """Map a source-specific label to the narrowest governed methodology."""
    definition = catalog.definition(candidate.metric_id, candidate.metric_version)
    if definition is None:
        raise Phase3Error(
            f"candidate references unknown metric version: "
            f"{candidate.metric_id}:{candidate.metric_version}"
        )
    metric_id = candidate.metric_id
    source = candidate.methodology.upper()
    preferences: list[MetricMethodology] = []
    if "DELINQUENCY" in metric_id:
        preferences.append(
            MetricMethodology.DELINQUENCY_COUNT_REPORTED
            if "COUNT" in metric_id
            else MetricMethodology.DELINQUENCY_UPB_REPORTED
        )
    if metric_id == "msr_hedging_result":
        preferences.append(MetricMethodology.MSR_HEDGE_RESULT_REPORTED)
    if (
        metric_id
        in {
            "msr_beginning_balance",
            "msr_additions",
            "msr_purchases",
            "msr_sales",
            "msr_realization_or_amortization",
            "msr_ending_balance",
            "msr_fair_value_inputs_or_assumptions_change",
        }
        or "ROLLFORWARD" in source
    ):
        preferences.append(MetricMethodology.MSR_FAIR_VALUE_ROLLFORWARD_REPORTED)
    if metric_id == "capitalized_servicing_rate_on_additions":
        preferences.append(MetricMethodology.CAPITALIZED_SERVICING_RATE_REPORTED)
    if metric_id in {
        "government_servicing_upb",
        "conventional_servicing_upb",
        "gnma_servicing_upb",
        "fnma_servicing_upb",
        "fhlmc_servicing_upb",
    }:
        preferences.append(MetricMethodology.PORTFOLIO_MIX_REPORTED)
    if metric_id == "servicing_adjusted_pretax_income" or "NON_GAAP" in source:
        preferences.append(MetricMethodology.NON_GAAP_REPORTED)
    if candidate.company_id == "tfc" and metric_id in {
        "servicing_for_others_upb",
        "msr_fair_value",
        "msr_ending_balance",
        "servicing_fee_income",
    }:
        preferences.append(MetricMethodology.SEC_FILING_EXHIBIT)
    preferences.extend(
        (
            MetricMethodology.ISSUER_REPORTED,
            MetricMethodology.SEC_FILING_EXHIBIT,
        )
    )
    methodology = next((item for item in preferences if item in definition.methodologies), None)
    if methodology is None:
        raise Phase3Error(
            f"no semantic governed methodology for {candidate.candidate_id}: "
            f"{candidate.metric_id}:{candidate.metric_version}; source={candidate.methodology}"
        )
    dimensions = _catalog_candidate_dimensions(catalog, candidate.company_id, candidate.metric_id)
    return replace(
        candidate,
        candidate_id=_candidate_identity(
            candidate, methodology.value, dimensions, candidate.methodology
        ),
        methodology=methodology.value,
    )


def _candidate_identity(
    candidate: ParsedObservationCandidate,
    governed_methodology: str,
    dimensions: tuple[MetricDimension, ...],
    source_methodology: str | None = None,
) -> str:
    identity = (
        candidate.evidence_id,
        candidate.metric_id,
        candidate.metric_version,
        candidate.period_start.isoformat() if candidate.period_start else None,
        candidate.period_end.isoformat(),
        candidate.period_type,
        candidate.fiscal_year,
        candidate.fiscal_quarter,
        candidate.raw_label,
        candidate.raw_value,
        format(candidate.normalized_value, "f"),
        candidate.reported_scale,
        _normalization_rule(candidate),
        candidate.currency,
        candidate.unit,
        source_methodology or candidate.methodology,
        governed_methodology,
        candidate.reporting_entity_id,
        candidate.reporting_scope_id,
        tuple((item.name, item.value) for item in dimensions),
        candidate.evidence_locator,
        candidate.parser_name,
        candidate.parser_version,
    )
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


def _reported_scale(normalization: str) -> str:
    if "thousands" in normalization:
        return "thousands"
    if "millions" in normalization:
        return "millions"
    if "billions" in normalization:
        return "billions"
    if normalization.startswith("percent_to_"):
        return "percent"
    return "1"


def _normalization_rule(candidate: ParsedObservationCandidate) -> str:
    scale = candidate.reported_scale
    if scale == "thousands":
        return "count_from_thousands" if candidate.unit == "count" else "usd_from_thousands"
    if scale == "millions":
        return "usd_from_millions"
    if scale == "billions":
        return "usd_from_billions"
    if scale == "percent":
        return "percent_to_basis_points" if candidate.unit == "basis_points" else "percent_to_ratio"
    return "identity"


def _normalization_trace(candidate: ParsedObservationCandidate) -> NormalizationTrace:
    rule = _normalization_rule(candidate)
    dash_policy = (
        "PUBLISH_ZERO_ONLY_WHEN_ROW_PRESENTS_EM_DASH"
        if candidate.raw_value in {"—", "â€”", "--"}
        else None
    )
    sign_normalization = "PRESERVE_REPORTED_SIGN"
    if dash_policy is None:
        preserved = _replay_normalization(
            candidate.raw_value,
            NormalizationTrace(rule, sign_normalization, None),
        )
        if preserved != candidate.normalized_value and abs(preserved) == abs(
            candidate.normalized_value
        ):
            sign_normalization = "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE"
    return NormalizationTrace(rule, sign_normalization, dash_policy)


def _replay_normalization(raw: str, trace: NormalizationTrace) -> Decimal:
    if trace.dash_policy is not None:
        if raw not in {"—", "â€”", "--"}:
            raise Phase3Error("dash-zero policy applied to a non-dash raw token")
        return Decimal(0)
    if trace.rule in {"usd_from_thousands", "count_from_thousands"}:
        value = normalize_reported_value(raw, rule="identity") * Decimal(1000)
    else:
        value = normalize_reported_value(raw, rule=trace.rule)
    if trace.sign_normalization == "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE":
        value = abs(value)
    elif trace.sign_normalization != "PRESERVE_REPORTED_SIGN":
        raise Phase3Error(f"unknown normalization sign policy: {trace.sign_normalization}")
    return value


def _validate_reported_candidate(
    catalog: MetricCatalog,
    candidate: ParsedObservationCandidate,
    dimensions: tuple[MetricDimension, ...],
) -> None:
    """Fail closed on every governed observation semantic boundary."""
    definition = catalog.definition(candidate.metric_id, candidate.metric_version)
    if definition is None:
        raise Phase3Error("reported candidate metric version is absent from catalog")
    try:
        methodology = MetricMethodology(candidate.methodology)
        period_type = _period_type(candidate.period_type)
        unit = MetricUnit(candidate.unit)
    except ValueError as error:
        raise Phase3Error(
            f"reported candidate has uncontrolled enum: {candidate.candidate_id}"
        ) from error
    failures: list[str] = []
    if methodology not in definition.methodologies:
        failures.append("methodology")
    if period_type not in definition.period_types:
        failures.append("period_type")
    if unit is not definition.unit:
        failures.append("unit")
    if {item.name for item in dimensions} != {item.taxonomy for item in definition.dimensions}:
        failures.append("dimension_taxonomies")
    if not candidate.reporting_entity_id or not candidate.reporting_scope_id:
        failures.append("reporting_boundary")
    if failures:
        raise Phase3Error(
            f"reported candidate violates catalog ({','.join(failures)}): "
            f"{candidate.company_id}:{candidate.metric_id}:{candidate.period_end}"
        )


def _published_state(raw: str) -> str:
    return "PUBLISHED" if raw == "REPORTED" else raw


def _assessment_source_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    for key in ("source_keys", "checked_source_keys"):
        if key in raw:
            return tuple(str(value) for value in raw[key])
    source = raw.get("source_key")
    return (str(source),) if source else ()


def _assessment_locators(raw: dict[str, Any]) -> tuple[str, ...]:
    if "checked_locators" in raw:
        return tuple(str(value) for value in raw["checked_locators"])
    locator = raw.get("locator")
    return (str(locator),) if locator else ()


def _previous_period_end(period_end: date) -> date:
    mapping = {
        date(2025, 9, 30): date(2025, 6, 30),
        date(2025, 12, 31): date(2025, 9, 30),
        date(2026, 3, 31): date(2025, 12, 31),
        date(2026, 6, 30): date(2026, 3, 31),
    }
    return mapping[period_end]


def _datetime(raw: object) -> datetime:
    parsed = datetime.fromisoformat(str(raw))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_datetime(raw: object) -> datetime | None:
    return None if raw is None else _datetime(raw)


def _optional_date(raw: object) -> date | None:
    return None if raw is None else date.fromisoformat(str(raw))


def _candidate_sort_key(item: ParsedObservationCandidate) -> tuple[str, str, date]:
    return item.company_id, item.metric_id, item.period_end


def _assessment_sort_key(item: Phase3CellAssessment) -> tuple[str, str, date]:
    return item.company_id, item.metric_id, item.period_end


def _derived_sort_key(item: Phase3DerivedCandidate) -> tuple[str, str, date]:
    return item.company_id, item.metric_id, item.period_end


def _blocked_sort_key(item: Phase3BlockedDerivation) -> tuple[str, str, date]:
    return item.company_id, item.metric_id, item.period_end
