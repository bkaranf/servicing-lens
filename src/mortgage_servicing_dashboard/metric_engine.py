# ruff: noqa: C901, EM101, EM102, PERF401, PLR0911, PLR0912, PLR0915, PLR2004, TRY003
"""Typed, deterministic Phase 3 metric catalog and calculation engine.

The module is deliberately pure: it reads local catalog bytes, validates
published observation inputs, performs exact :class:`~decimal.Decimal`
arithmetic, and returns persistence-ready lineage.  It has no network or
database dependency and never selects a preferred value after a conflict.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    InvalidOperation,
    localcontext,
)
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeAlias, TypeVar, cast

import yaml

_BPS: Final = Decimal(10000)
_TWO: Final = Decimal(2)
_DERIVATION_CONTEXT_DIMENSIONS: Final = frozenset({"portfolio_population", "msr_population"})
_DEFAULT_QUANTA: Final[Mapping[str, str]] = {
    "USD": "0.01",
    "count": "1",
    "ratio": "0.0000000001",
    "percent": "0.000001",
    "basis_points": "0.000001",
    "USD_per_loan": "0.01",
    "multiple": "0.0000000001",
}
_SCALES: Final[Mapping[str, str]] = {
    "ones": "1",
    "thousands": "1000",
    "millions": "1000000",
    "billions": "1000000000",
    "percent": "0.01",
    "basis_points": "0.0001",
}

YamlMapping: TypeAlias = Mapping[str, object]
ItemT = TypeVar("ItemT", bound=Hashable)
EnumT = TypeVar("EnumT", bound=StrEnum)


class MetricEngineError(ValueError):
    """Raised when catalog bytes or engine construction violate invariants."""


class DefinitionLifecycle(StrEnum):
    """Lifecycle of one immutable semantic metric definition."""

    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"


class MetricUnit(StrEnum):
    """Canonical unit attached to a metric value."""

    USD = "USD"
    COUNT = "count"
    RATIO = "ratio"
    PERCENT = "percent"
    BASIS_POINTS = "basis_points"
    USD_PER_LOAN = "USD_per_loan"
    MULTIPLE = "multiple"


class PeriodType(StrEnum):
    """Supported observation period semantics."""

    INSTANT = "instant"
    DURATION = "duration"
    DURATION_YTD = "duration_ytd"
    ANNUAL = "annual"


class MetricMethodology(StrEnum):
    """Controlled calculation or source methodology labels."""

    ISSUER_REPORTED = "ISSUER_REPORTED"
    DETERMINISTIC_DERIVED = "DETERMINISTIC_DERIVED"
    SEC_COMPANY_FACTS_XBRL = "SEC_COMPANY_FACTS_XBRL"
    SEC_FILING_XBRL = "SEC_FILING_XBRL"
    SEC_FILING_EXHIBIT = "SEC_FILING_EXHIBIT"
    REGULATORY_REPORTED = "REGULATORY_REPORTED"
    FR_Y9C = "FR_Y9C"
    FFIEC_CALL_REPORT = "FFIEC_CALL_REPORT"
    DELINQUENCY_COUNT_REPORTED = "DELINQUENCY_COUNT_REPORTED"
    DELINQUENCY_UPB_REPORTED = "DELINQUENCY_UPB_REPORTED"
    MSR_HEDGE_RESULT_REPORTED = "MSR_HEDGE_RESULT_REPORTED"
    MSR_FAIR_VALUE_ROLLFORWARD_REPORTED = "MSR_FAIR_VALUE_ROLLFORWARD_REPORTED"
    CAPITALIZED_SERVICING_RATE_REPORTED = "CAPITALIZED_SERVICING_RATE_REPORTED"
    PORTFOLIO_MIX_REPORTED = "PORTFOLIO_MIX_REPORTED"
    NON_GAAP_REPORTED = "NON_GAAP_REPORTED"


class FormulaKind(StrEnum):
    """Supported deterministic derivation formulas."""

    ANNUALIZED_EXPENSE_PER_AVERAGE_LOAN = "ANNUALIZED_EXPENSE_PER_AVERAGE_LOAN"
    ANNUALIZED_RATE_BPS = "ANNUALIZED_RATE_BPS"
    FAIR_VALUE_TO_UPB_MULTIPLE = "FAIR_VALUE_TO_UPB_MULTIPLE"
    FAIR_VALUE_TO_UPB_BPS = "FAIR_VALUE_TO_UPB_BPS"
    SUM_INPUTS = "SUM_INPUTS"
    RATIO = "RATIO"
    SUM_INPUTS_OVER_DENOMINATOR = "SUM_INPUTS_OVER_DENOMINATOR"


class AveragingMethod(StrEnum):
    """Explicit denominator averaging policy."""

    NONE = "NONE"
    ARITHMETIC_BEGIN_END = "ARITHMETIC_BEGIN_END"


class AnnualizationMethod(StrEnum):
    """Explicit annualization policy."""

    NONE = "NONE"
    ACTUAL_DAYS = "ACTUAL_DAYS"
    ACTUAL_OVER_BASIS = "ACTUAL_OVER_BASIS"


class PeriodRelation(StrEnum):
    """Required relationship between an input and output period."""

    SAME_INSTANT = "SAME_INSTANT"
    SAME_DURATION = "SAME_DURATION"
    BEGINNING_INSTANT = "BEGINNING_INSTANT"
    ENDING_INSTANT = "ENDING_INSTANT"


class PublicationStatus(StrEnum):
    """Whether an input represents a published observation."""

    PUBLISHED = "PUBLISHED"
    UNPUBLISHED = "UNPUBLISHED"
    WITHDRAWN = "WITHDRAWN"


class ValueState(StrEnum):
    """Measurement state of an observation."""

    REPORTED_ACTUAL = "REPORTED_ACTUAL"
    DERIVED = "DERIVED"
    ESTIMATED = "ESTIMATED"
    PARTIAL = "PARTIAL"
    NOT_DISCLOSED = "NOT_DISCLOSED"


class Completeness(StrEnum):
    """Completeness of a published observation value."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class ValidationRule(StrEnum):
    """Deterministic scalar validation rule."""

    FINITE = "FINITE"
    NONNEGATIVE = "NONNEGATIVE"
    SIGNED = "SIGNED"
    RATIO_0_1 = "RATIO_0_1"
    BPS_0_10000 = "BPS_0_10000"


class MethodologyPolicy(StrEnum):
    """Pairwise methodology comparison policy."""

    EXACT = "EXACT"


class RoundingMethod(StrEnum):
    """Stable output rounding modes supported by the catalog."""

    ROUND_HALF_EVEN = "ROUND_HALF_EVEN"


class DecisionDisposition(StrEnum):
    """Fail-closed engine decision disposition."""

    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"


class ReconciliationDisposition(StrEnum):
    """Configured disposition when cross-source values conflict."""

    QUARANTINE = "QUARANTINE"


class DecisionReason(StrEnum):
    """Stable reason codes emitted by validation and calculation decisions."""

    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    METRIC_DEFINITION_MISMATCH = "METRIC_DEFINITION_MISMATCH"
    DERIVATION_NOT_CONFIGURED = "DERIVATION_NOT_CONFIGURED"
    INPUT_ROLE_MISSING = "INPUT_ROLE_MISSING"
    INPUT_ROLE_DUPLICATE = "INPUT_ROLE_DUPLICATE"
    INPUT_ROLE_UNEXPECTED = "INPUT_ROLE_UNEXPECTED"
    INPUT_OBSERVATION_ID_MISSING = "INPUT_OBSERVATION_ID_MISSING"
    INPUT_OBSERVATION_ID_DUPLICATE = "INPUT_OBSERVATION_ID_DUPLICATE"
    INPUT_UNPUBLISHED = "INPUT_UNPUBLISHED"
    INPUT_INCOMPLETE = "INPUT_INCOMPLETE"
    INPUT_NOT_ACTUAL = "INPUT_NOT_ACTUAL"
    INPUT_VALUE_MISSING = "INPUT_VALUE_MISSING"
    INPUT_VALUE_NONFINITE = "INPUT_VALUE_NONFINITE"
    INPUT_METRIC_MISMATCH = "INPUT_METRIC_MISMATCH"
    INPUT_UNIT_MISMATCH = "INPUT_UNIT_MISMATCH"
    INPUT_SCALE_MISMATCH = "INPUT_SCALE_MISMATCH"
    INPUT_PERIOD_MISMATCH = "INPUT_PERIOD_MISMATCH"
    INPUT_ISSUER_MISMATCH = "INPUT_ISSUER_MISMATCH"
    INPUT_ENTITY_MISMATCH = "INPUT_ENTITY_MISMATCH"
    INPUT_SCOPE_MISMATCH = "INPUT_SCOPE_MISMATCH"
    INPUT_DIMENSION_MISMATCH = "INPUT_DIMENSION_MISMATCH"
    INPUT_FORMULA_VERSION_MISSING = "INPUT_FORMULA_VERSION_MISSING"
    AVERAGING_PARAMETERS_MISSING = "AVERAGING_PARAMETERS_MISSING"
    AVERAGING_PARAMETERS_MISMATCH = "AVERAGING_PARAMETERS_MISMATCH"
    ANNUALIZATION_PARAMETERS_MISSING = "ANNUALIZATION_PARAMETERS_MISSING"
    ANNUALIZATION_PARAMETERS_MISMATCH = "ANNUALIZATION_PARAMETERS_MISMATCH"
    DENOMINATOR_NOT_POSITIVE = "DENOMINATOR_NOT_POSITIVE"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    SCALE_MISMATCH = "SCALE_MISMATCH"
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    METHODOLOGY_MISMATCH = "METHODOLOGY_MISMATCH"
    DIMENSION_MISSING = "DIMENSION_MISSING"
    DIMENSION_UNEXPECTED = "DIMENSION_UNEXPECTED"
    DIMENSION_VALUE_INVALID = "DIMENSION_VALUE_INVALID"
    FIXED_DIMENSION_MISMATCH = "FIXED_DIMENSION_MISMATCH"
    VALUE_RULE_FAILED = "VALUE_RULE_FAILED"
    COMPARABILITY_CONTEXT_MISMATCH = "COMPARABILITY_CONTEXT_MISMATCH"
    CROSS_SOURCE_RULE_UNKNOWN = "CROSS_SOURCE_RULE_UNKNOWN"
    RECONCILIATION_SEMANTICS_MISMATCH = "RECONCILIATION_SEMANTICS_MISMATCH"
    RECONCILIATION_VALUE_MISMATCH = "RECONCILIATION_VALUE_MISMATCH"


class CatalogViolationCode(StrEnum):
    """Stable full-catalog invariant violation codes."""

    DUPLICATE_METRIC_VERSION = "DUPLICATE_METRIC_VERSION"
    DUPLICATE_TAXONOMY = "DUPLICATE_TAXONOMY"
    EMPTY_TAXONOMY = "EMPTY_TAXONOMY"
    UNKNOWN_TAXONOMY = "UNKNOWN_TAXONOMY"
    INVALID_FIXED_DIMENSION = "INVALID_FIXED_DIMENSION"
    DUPLICATE_DIMENSION = "DUPLICATE_DIMENSION"
    INVALID_SCALE = "INVALID_SCALE"
    INVALID_QUANTUM = "INVALID_QUANTUM"
    DERIVED_METHODOLOGY_MISSING = "DERIVED_METHODOLOGY_MISSING"
    DUPLICATE_INPUT_ROLE = "DUPLICATE_INPUT_ROLE"
    UNKNOWN_INPUT_METRIC = "UNKNOWN_INPUT_METRIC"
    UNKNOWN_COMPARABILITY_DIMENSION = "UNKNOWN_COMPARABILITY_DIMENSION"
    DUPLICATE_CROSS_SOURCE_RULE = "DUPLICATE_CROSS_SOURCE_RULE"
    UNKNOWN_RECONCILIATION_METRIC = "UNKNOWN_RECONCILIATION_METRIC"
    INVALID_RECONCILIATION_TOLERANCE = "INVALID_RECONCILIATION_TOLERANCE"
    INVALID_RECONCILIATION_METHODS = "INVALID_RECONCILIATION_METHODS"
    EMPTY_SCOPE_PAIR = "EMPTY_SCOPE_PAIR"
    DUPLICATE_SCOPE_PAIR = "DUPLICATE_SCOPE_PAIR"
    SAME_SCOPE_PAIR = "SAME_SCOPE_PAIR"
    DUPLICATE_CURRENT_DEFINITION = "DUPLICATE_CURRENT_DEFINITION"
    MISSING_CURRENT_DEFINITION = "MISSING_CURRENT_DEFINITION"


@dataclass(frozen=True, order=True, slots=True)
class MetricDimension:
    """One controlled metric dimension and member."""

    name: str
    value: str

    def __post_init__(self) -> None:
        """Reject incomplete dimension semantics."""
        if not self.name.strip() or not self.value.strip():
            raise MetricEngineError("metric dimensions require nonblank name and value")


@dataclass(frozen=True, slots=True)
class DimensionTaxonomy:
    """Controlled set of members for one dimension."""

    taxonomy_id: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DimensionRequirement:
    """Metric dimension requirement, optionally fixed to one member."""

    taxonomy: str
    fixed_value: str | None


@dataclass(frozen=True, slots=True)
class QuantizationRule:
    """Exact output quantum and rounding rule."""

    quantum: Decimal
    rounding: RoundingMethod

    def __post_init__(self) -> None:
        """Require a finite positive Decimal quantum."""
        _require_decimal(self.quantum, "quantum")
        if not self.quantum.is_finite() or self.quantum <= 0:
            raise MetricEngineError("quantum must be finite and positive")


@dataclass(frozen=True, slots=True)
class AllowedScopeRelationship:
    """One explicitly governed input-scope to output-scope relationship."""

    input_scope_id: str
    output_scope_id: str


@dataclass(frozen=True, slots=True)
class InputRequirement:
    """One complete input role for a deterministic formula."""

    role: str
    metric_ids: tuple[str, ...]
    unit: MetricUnit
    period_relation: PeriodRelation
    allowed_scope_pairs: tuple[AllowedScopeRelationship, ...]


@dataclass(frozen=True, slots=True)
class DerivationDefinition:
    """Versioned deterministic formula and its complete role list."""

    formula: FormulaKind
    formula_version: str
    averaging: AveragingMethod
    annualization: AnnualizationMethod
    inputs: tuple[InputRequirement, ...]


@dataclass(frozen=True, slots=True)
class ComparabilityDefinition:
    """Dimensions and methodology policy required for comparison."""

    methodology_policy: MethodologyPolicy
    dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One versioned metric ontology definition."""

    metric_id: str
    semantic_version: str
    category: str
    definition: str
    unit: MetricUnit
    scale: Decimal
    period_types: tuple[PeriodType, ...]
    methodologies: tuple[MetricMethodology, ...]
    dimensions: tuple[DimensionRequirement, ...]
    validation_rules: tuple[ValidationRule, ...]
    comparability: ComparabilityDefinition
    reconciliation_rules: tuple[str, ...]
    quantization: QuantizationRule
    derivation: DerivationDefinition | None
    lifecycle: DefinitionLifecycle = DefinitionLifecycle.CURRENT

    @property
    def key(self) -> tuple[str, str]:
        """Return the catalog's stable versioned key."""
        return self.metric_id, self.semantic_version

    @property
    def is_current(self) -> bool:
        """Return whether this immutable version is the current canonical one."""
        return self.lifecycle is DefinitionLifecycle.CURRENT


@dataclass(frozen=True, slots=True)
class ScopePair:
    """Exact SEC and regulatory reporting scopes allowed for reconciliation."""

    sec_scope_id: str
    regulatory_scope_id: str


@dataclass(frozen=True, slots=True)
class CrossSourceRule:
    """One fail-closed SEC-versus-regulatory reconciliation rule."""

    rule_id: str
    issuer_id: str
    metric_id: str
    sec_methodologies: tuple[MetricMethodology, ...]
    regulatory_methodologies: tuple[MetricMethodology, ...]
    allowed_scope_pairs: tuple[ScopePair, ...]
    unit: MetricUnit
    period_types: tuple[PeriodType, ...]
    absolute_tolerance: Decimal
    mismatch_disposition: ReconciliationDisposition


@dataclass(frozen=True, slots=True)
class CatalogViolation:
    """One stable full-catalog invariant violation."""

    code: CatalogViolationCode
    location: str


@dataclass(frozen=True, slots=True)
class MetricCatalog:
    """One canonical metric view with immutable version history."""

    base_version: str
    definitions: tuple[MetricDefinition, ...]
    dimension_taxonomies: tuple[DimensionTaxonomy, ...]
    cross_source_rules: tuple[CrossSourceRule, ...]

    def definition(self, metric_id: str, semantic_version: str) -> MetricDefinition | None:
        """Return one exact versioned definition, if present."""
        return next(
            (
                item
                for item in self.definitions
                if item.metric_id == metric_id and item.semantic_version == semantic_version
            ),
            None,
        )

    def versions(self, metric_id: str) -> tuple[MetricDefinition, ...]:
        """Return all immutable definitions in semantic-version order."""
        return tuple(
            sorted(
                (item for item in self.definitions if item.metric_id == metric_id),
                key=lambda item: _semantic_version_key(item.semantic_version),
            )
        )

    def current_definition(self, metric_id: str) -> MetricDefinition | None:
        """Return the one current canonical definition for a metric."""
        return next(
            (item for item in self.definitions if item.metric_id == metric_id and item.is_current),
            None,
        )

    def historical_versions(self, metric_id: str) -> tuple[MetricDefinition, ...]:
        """Return immutable historical versions retained for observation lineage."""
        return tuple(item for item in self.versions(metric_id) if not item.is_current)

    def cross_source_rule(self, rule_id: str) -> CrossSourceRule | None:
        """Return one cross-source rule by stable identifier."""
        return next((item for item in self.cross_source_rules if item.rule_id == rule_id), None)


@dataclass(frozen=True, slots=True)
class MetricInput:
    """Published observation supplied to validation, derivation, or reconciliation."""

    observation_id: str
    issuer_id: str
    metric_id: str
    metric_version: str
    value: Decimal | None
    unit: MetricUnit
    period_type: PeriodType
    period_start: date | None
    period_end: date
    reporting_entity_id: str
    reporting_scope_id: str
    methodology: MetricMethodology
    publication_status: PublicationStatus
    value_state: ValueState
    completeness: Completeness
    dimensions: tuple[MetricDimension, ...] = ()
    formula_version: str | None = None
    scale: str = "1"

    def __post_init__(self) -> None:
        """Enforce types and unambiguous local context structure."""
        if self.value is not None:
            _require_decimal(self.value, "input value")
        if self.scale != "1":
            raise MetricEngineError("metric inputs must use canonical scale '1'")
        if self.period_type is PeriodType.INSTANT and self.period_start is not None:
            raise MetricEngineError("instant input cannot have a period start")
        if self.period_type is not PeriodType.INSTANT and (
            self.period_start is None or self.period_start > self.period_end
        ):
            raise MetricEngineError("duration input requires an ordered period")
        if tuple(sorted(self.dimensions)) != self.dimensions:
            raise MetricEngineError("input dimensions must be sorted")
        if len({item.name for item in self.dimensions}) != len(self.dimensions):
            raise MetricEngineError("input dimensions cannot repeat a taxonomy")


@dataclass(frozen=True, slots=True)
class AveragingParameters:
    """Explicit dates used for a beginning/end arithmetic average."""

    beginning_date: date
    ending_date: date

    def __post_init__(self) -> None:
        """Require a strictly ordered averaging window."""
        if self.beginning_date >= self.ending_date:
            raise MetricEngineError("averaging dates must be strictly ordered")


@dataclass(frozen=True, slots=True)
class AnnualizationParameters:
    """Explicit actual-day observation and Decimal annual basis."""

    observed_days: int
    basis_days: Decimal

    def __post_init__(self) -> None:
        """Require exact positive annualization parameters."""
        if isinstance(self.observed_days, bool) or self.observed_days <= 0:
            raise MetricEngineError("observed days must be a positive integer")
        _require_decimal(self.basis_days, "annualization basis days")
        if not self.basis_days.is_finite() or self.basis_days <= 0:
            raise MetricEngineError("annualization basis days must be finite and positive")


@dataclass(frozen=True, slots=True)
class DerivationRequest:
    """Complete output context and role-tagged inputs for one formula."""

    derived_observation_id: str
    metric_id: str
    metric_version: str
    issuer_id: str
    reporting_entity_id: str
    reporting_scope_id: str
    period_type: PeriodType
    period_start: date | None
    period_end: date
    dimensions: tuple[MetricDimension, ...]
    inputs: tuple[tuple[str, MetricInput], ...]
    averaging: AveragingParameters | None = None
    annualization: AnnualizationParameters | None = None
    scale: str = "1"

    def __post_init__(self) -> None:
        """Require ordered output periods and deterministic dimension order."""
        if not self.derived_observation_id.strip():
            raise MetricEngineError("derived observation ID must not be blank")
        if self.scale != "1":
            raise MetricEngineError("derived outputs must use canonical scale '1'")
        if self.period_type is PeriodType.INSTANT and self.period_start is not None:
            raise MetricEngineError("instant output cannot have a period start")
        if self.period_type is not PeriodType.INSTANT and (
            self.period_start is None or self.period_start > self.period_end
        ):
            raise MetricEngineError("duration output requires an ordered period")
        if tuple(sorted(self.dimensions)) != self.dimensions:
            raise MetricEngineError("output dimensions must be sorted")
        if len({item.name for item in self.dimensions}) != len(self.dimensions):
            raise MetricEngineError("output dimensions cannot repeat a taxonomy")


@dataclass(frozen=True, slots=True)
class DerivedObservationLineage:
    """Persistence-ready row for ``DerivedObservationInput``."""

    derived_observation_id: str
    input_observation_id: str
    input_role: str
    input_ordinal: int
    formula_version: str
    input_value: Decimal


@dataclass(frozen=True, slots=True)
class CalculationTrace:
    """Exact formula controls and pre-quantization value."""

    formula: FormulaKind
    formula_version: str
    averaging: AveragingMethod
    annualization: AnnualizationMethod
    unquantized_value: Decimal
    quantum: Decimal
    observed_days: int | None
    basis_days: Decimal | None


@dataclass(frozen=True, slots=True)
class DerivedMetricResult:
    """Validated derived value with full context and persistence-ready lineage."""

    observation_id: str
    issuer_id: str
    metric_id: str
    metric_version: str
    value: Decimal
    unit: MetricUnit
    period_type: PeriodType
    period_start: date | None
    period_end: date
    reporting_entity_id: str
    reporting_scope_id: str
    methodology: MetricMethodology
    dimensions: tuple[MetricDimension, ...]
    trace: CalculationTrace
    lineage: tuple[DerivedObservationLineage, ...]
    scale: str = "1"


@dataclass(frozen=True, slots=True)
class DerivationDecision:
    """Validated result or stable quarantine reasons, never both."""

    disposition: DecisionDisposition
    reasons: tuple[DecisionReason, ...]
    result: DerivedMetricResult | None

    def __post_init__(self) -> None:
        """Keep validated and quarantined outcomes internally consistent."""
        if self.disposition is DecisionDisposition.VALIDATED:
            if self.result is None or self.reasons:
                raise MetricEngineError("validated derivation requires only a result")
        elif self.result is not None or not self.reasons:
            raise MetricEngineError("quarantined derivation requires only reasons")


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    """Scalar observation validation outcome."""

    disposition: DecisionDisposition
    reasons: tuple[DecisionReason, ...]


@dataclass(frozen=True, slots=True)
class ComparabilityDecision:
    """Pairwise metric comparability outcome."""

    comparable: bool
    reasons: tuple[DecisionReason, ...]


@dataclass(frozen=True, slots=True)
class CrossSourceReconciliationDecision:
    """SEC-versus-regulatory result without a preferred observation."""

    disposition: DecisionDisposition
    reasons: tuple[DecisionReason, ...]
    rule_id: str
    absolute_difference: Decimal | None

    @property
    def quarantine_required(self) -> bool:
        """Return whether consumers must quarantine the pair."""
        return self.disposition is DecisionDisposition.QUARANTINED


def load_metric_catalog(
    base_path: Path,
) -> MetricCatalog:
    """Load the sole declarative catalog and retain immutable version history."""
    base = _load_yaml_mapping(base_path)
    base_version = _text(base, "catalog_version", context=str(base_path))
    definitions = list(_parse_base_definitions(base))
    definitions.extend(_parse_versioned_definitions(base))
    taxonomies = list(_parse_taxonomies(base))
    rules = list(_parse_cross_source_rules(base))
    resolved_definitions = _assign_definition_lifecycle(tuple(definitions))
    catalog = MetricCatalog(
        base_version=base_version,
        definitions=resolved_definitions,
        dimension_taxonomies=tuple(taxonomies),
        cross_source_rules=tuple(rules),
    )
    validate_catalog_invariants(catalog)
    return catalog


def catalog_invariant_violations(catalog: MetricCatalog) -> tuple[CatalogViolation, ...]:
    """Return every deterministic invariant violation in stable sorted order."""
    violations: list[CatalogViolation] = []
    definition_keys = [item.key for item in catalog.definitions]
    metric_ids = {item.metric_id for item in catalog.definitions}
    taxonomy_ids = [item.taxonomy_id for item in catalog.dimension_taxonomies]
    taxonomies = {item.taxonomy_id: item for item in catalog.dimension_taxonomies}
    for metric_id in sorted(metric_ids):
        current = [
            item for item in catalog.definitions if item.metric_id == metric_id and item.is_current
        ]
        if len(current) > 1:
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.DUPLICATE_CURRENT_DEFINITION,
                    metric_id,
                )
            )
        elif not current:
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.MISSING_CURRENT_DEFINITION,
                    metric_id,
                )
            )
    for duplicate_definition_key in _duplicates(definition_keys):
        violations.append(
            CatalogViolation(
                CatalogViolationCode.DUPLICATE_METRIC_VERSION,
                ":".join(duplicate_definition_key),
            )
        )
    for duplicate_taxonomy_id in _duplicates(taxonomy_ids):
        violations.append(
            CatalogViolation(CatalogViolationCode.DUPLICATE_TAXONOMY, duplicate_taxonomy_id)
        )
    for taxonomy_definition in catalog.dimension_taxonomies:
        if not taxonomy_definition.values or any(
            not value.strip() for value in taxonomy_definition.values
        ):
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.EMPTY_TAXONOMY,
                    taxonomy_definition.taxonomy_id,
                )
            )
    for definition in catalog.definitions:
        location = f"{definition.metric_id}:{definition.semantic_version}"
        if not definition.scale.is_finite() or definition.scale <= 0:
            violations.append(CatalogViolation(CatalogViolationCode.INVALID_SCALE, location))
        if not definition.quantization.quantum.is_finite() or definition.quantization.quantum <= 0:
            violations.append(CatalogViolation(CatalogViolationCode.INVALID_QUANTUM, location))
        dimension_names = [item.taxonomy for item in definition.dimensions]
        for duplicate_dimension in _duplicates(dimension_names):
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.DUPLICATE_DIMENSION,
                    f"{location}:{duplicate_dimension}",
                )
            )
        for requirement in definition.dimensions:
            required_taxonomy = taxonomies.get(requirement.taxonomy)
            if required_taxonomy is None:
                violations.append(
                    CatalogViolation(
                        CatalogViolationCode.UNKNOWN_TAXONOMY,
                        f"{location}:{requirement.taxonomy}",
                    )
                )
            elif (
                requirement.fixed_value is not None
                and requirement.fixed_value not in required_taxonomy.values
            ):
                violations.append(
                    CatalogViolation(
                        CatalogViolationCode.INVALID_FIXED_DIMENSION,
                        f"{location}:{requirement.taxonomy}:{requirement.fixed_value}",
                    )
                )
        for dimension in definition.comparability.dimensions:
            if dimension not in dimension_names:
                violations.append(
                    CatalogViolation(
                        CatalogViolationCode.UNKNOWN_COMPARABILITY_DIMENSION,
                        f"{location}:{dimension}",
                    )
                )
        if definition.derivation is not None:
            if MetricMethodology.DETERMINISTIC_DERIVED not in definition.methodologies:
                violations.append(
                    CatalogViolation(CatalogViolationCode.DERIVED_METHODOLOGY_MISSING, location)
                )
            roles = [item.role for item in definition.derivation.inputs]
            for duplicate_role in _duplicates(roles):
                violations.append(
                    CatalogViolation(
                        CatalogViolationCode.DUPLICATE_INPUT_ROLE,
                        f"{location}:{duplicate_role}",
                    )
                )
            for input_requirement in definition.derivation.inputs:
                pair_location = f"{location}:{input_requirement.role}"
                if any(
                    not pair.input_scope_id.strip() or not pair.output_scope_id.strip()
                    for pair in input_requirement.allowed_scope_pairs
                ):
                    violations.append(
                        CatalogViolation(CatalogViolationCode.EMPTY_SCOPE_PAIR, pair_location)
                    )
                if _duplicates(input_requirement.allowed_scope_pairs):
                    violations.append(
                        CatalogViolation(CatalogViolationCode.DUPLICATE_SCOPE_PAIR, pair_location)
                    )
                if any(
                    pair.input_scope_id == pair.output_scope_id
                    for pair in input_requirement.allowed_scope_pairs
                ):
                    violations.append(
                        CatalogViolation(CatalogViolationCode.SAME_SCOPE_PAIR, pair_location)
                    )
                for metric_id in input_requirement.metric_ids:
                    if metric_id not in metric_ids:
                        violations.append(
                            CatalogViolation(
                                CatalogViolationCode.UNKNOWN_INPUT_METRIC,
                                f"{location}:{input_requirement.role}:{metric_id}",
                            )
                        )
    rule_ids = [item.rule_id for item in catalog.cross_source_rules]
    for duplicate_rule_id in _duplicates(rule_ids):
        violations.append(
            CatalogViolation(
                CatalogViolationCode.DUPLICATE_CROSS_SOURCE_RULE,
                duplicate_rule_id,
            )
        )
    for rule in catalog.cross_source_rules:
        if rule.metric_id not in metric_ids:
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.UNKNOWN_RECONCILIATION_METRIC,
                    rule.rule_id,
                )
            )
        if not rule.absolute_tolerance.is_finite() or rule.absolute_tolerance < 0:
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.INVALID_RECONCILIATION_TOLERANCE,
                    rule.rule_id,
                )
            )
        if not rule.sec_methodologies or not rule.regulatory_methodologies:
            violations.append(
                CatalogViolation(
                    CatalogViolationCode.INVALID_RECONCILIATION_METHODS,
                    rule.rule_id,
                )
            )
        if not rule.allowed_scope_pairs or any(
            not pair.sec_scope_id.strip() or not pair.regulatory_scope_id.strip()
            for pair in rule.allowed_scope_pairs
        ):
            violations.append(CatalogViolation(CatalogViolationCode.EMPTY_SCOPE_PAIR, rule.rule_id))
    return tuple(sorted(violations, key=lambda item: (item.code.value, item.location)))


def validate_catalog_invariants(catalog: MetricCatalog) -> None:
    """Raise one deterministic error containing all full-catalog violations."""
    violations = catalog_invariant_violations(catalog)
    if violations:
        detail = "; ".join(f"{item.code.value}@{item.location}" for item in violations)
        raise MetricEngineError(f"metric catalog invariant violations: {detail}")


def normalize_reported_value(raw_value: str, *, scale: Decimal = Decimal(1)) -> Decimal:
    """Normalize a published numeric string with exact Decimal-only scaling."""
    _require_decimal(scale, "scale")
    if not scale.is_finite() or scale <= 0:
        raise MetricEngineError("normalization scale must be finite and positive")
    text = raw_value.strip().replace(",", "")
    if not text:
        raise MetricEngineError("reported numeric value must not be blank")
    negative = text.startswith("(") and text.endswith(")")
    if text.startswith("(") != text.endswith(")"):
        raise MetricEngineError("reported numeric parentheses must be balanced")
    numeric = text[1:-1].strip() if negative else text
    try:
        value = Decimal(numeric)
    except InvalidOperation as exc:
        raise MetricEngineError(f"invalid reported numeric value: {raw_value!r}") from exc
    if not value.is_finite():
        raise MetricEngineError("reported numeric value must be finite")
    return (-value if negative else value) * scale


def validate_metric_input(observation: MetricInput, catalog: MetricCatalog) -> ValidationDecision:
    """Validate one observation against its exact versioned ontology definition."""
    definition = catalog.definition(observation.metric_id, observation.metric_version)
    if definition is None:
        return _validation_quarantine(DecisionReason.UNKNOWN_METRIC)
    reasons: list[DecisionReason] = []
    if observation.unit is not definition.unit:
        reasons.append(DecisionReason.UNIT_MISMATCH)
    if observation.period_type not in definition.period_types:
        reasons.append(DecisionReason.PERIOD_MISMATCH)
    if observation.methodology not in definition.methodologies:
        reasons.append(DecisionReason.METHODOLOGY_MISMATCH)
    reasons.extend(_dimension_reasons(observation.dimensions, definition, catalog))
    if observation.value is None:
        reasons.append(DecisionReason.INPUT_VALUE_MISSING)
    elif not observation.value.is_finite():
        reasons.append(DecisionReason.INPUT_VALUE_NONFINITE)
    else:
        reasons.extend(_value_rule_reasons(observation.value, definition.validation_rules))
    if reasons:
        return ValidationDecision(DecisionDisposition.QUARANTINED, _unique_reasons(reasons))
    return ValidationDecision(DecisionDisposition.VALIDATED, ())


def assess_metric_comparability(
    left: MetricInput,
    right: MetricInput,
    catalog: MetricCatalog,
) -> ComparabilityDecision:
    """Assess pairwise comparability without coercing either observation."""
    definition = catalog.definition(left.metric_id, left.metric_version)
    if definition is None or (left.metric_id, left.metric_version) != (
        right.metric_id,
        right.metric_version,
    ):
        return ComparabilityDecision(
            comparable=False,
            reasons=(DecisionReason.METRIC_DEFINITION_MISMATCH,),
        )
    reasons: list[DecisionReason] = []
    for observation in (left, right):
        if observation.publication_status is not PublicationStatus.PUBLISHED:
            reasons.append(DecisionReason.INPUT_UNPUBLISHED)
        if observation.completeness is not Completeness.COMPLETE:
            reasons.append(DecisionReason.INPUT_INCOMPLETE)
        if observation.value_state not in {ValueState.REPORTED_ACTUAL, ValueState.DERIVED}:
            reasons.append(DecisionReason.INPUT_NOT_ACTUAL)
        if observation.value is None:
            reasons.append(DecisionReason.INPUT_VALUE_MISSING)
        elif not observation.value.is_finite():
            reasons.append(DecisionReason.INPUT_VALUE_NONFINITE)
    if left.unit is not right.unit:
        reasons.append(DecisionReason.UNIT_MISMATCH)
    if left.scale != right.scale:
        reasons.append(DecisionReason.SCALE_MISMATCH)
    if (
        left.period_type is not right.period_type
        or left.period_start != right.period_start
        or left.period_end != right.period_end
    ):
        reasons.append(DecisionReason.PERIOD_MISMATCH)
    if (
        definition.comparability.methodology_policy is MethodologyPolicy.EXACT
        and left.methodology is not right.methodology
    ):
        reasons.append(DecisionReason.METHODOLOGY_MISMATCH)
    left_dimensions = _dimension_map(left.dimensions)
    right_dimensions = _dimension_map(right.dimensions)
    if any(
        left_dimensions.get(name) != right_dimensions.get(name)
        for name in definition.comparability.dimensions
    ):
        reasons.append(DecisionReason.INPUT_DIMENSION_MISMATCH)
    if (
        left.reporting_entity_id != right.reporting_entity_id
        or left.reporting_scope_id != right.reporting_scope_id
        or left.issuer_id != right.issuer_id
    ):
        reasons.append(DecisionReason.COMPARABILITY_CONTEXT_MISMATCH)
    if left.methodology is MetricMethodology.DETERMINISTIC_DERIVED and (
        left.formula_version is None or left.formula_version != right.formula_version
    ):
        reasons.append(DecisionReason.INPUT_FORMULA_VERSION_MISSING)
    unique = _unique_reasons(reasons)
    return ComparabilityDecision(not unique, unique)


def derive_metric(request: DerivationRequest, catalog: MetricCatalog) -> DerivationDecision:
    """Calculate one metric or return stable quarantine reasons without a value."""
    definition = catalog.definition(request.metric_id, request.metric_version)
    if definition is None:
        return _derivation_quarantine(DecisionReason.UNKNOWN_METRIC)
    derivation = definition.derivation
    if derivation is None:
        return _derivation_quarantine(DecisionReason.DERIVATION_NOT_CONFIGURED)
    reasons, role_inputs = _validate_derivation_inputs(request, definition, catalog)
    if reasons:
        return DerivationDecision(DecisionDisposition.QUARANTINED, reasons, None)
    assert all(item.value is not None for item in role_inputs.values())  # noqa: S101
    values = {role: cast("Decimal", item.value) for role, item in role_inputs.items()}
    unquantized, calculation_reason = _calculate(
        request,
        derivation,
        values,
        output_unit=definition.unit,
    )
    if calculation_reason is not None:
        return _derivation_quarantine(calculation_reason)
    assert unquantized is not None  # noqa: S101
    output_reasons = _value_rule_reasons(unquantized, definition.validation_rules)
    if output_reasons:
        return _derivation_quarantine(DecisionReason.OUTPUT_VALIDATION_FAILED)
    with localcontext() as context:
        context.prec = max(38, len(unquantized.as_tuple().digits) + 20)
        value = unquantized.quantize(
            definition.quantization.quantum,
            rounding=ROUND_HALF_EVEN,
        )
    lineage = tuple(
        DerivedObservationLineage(
            derived_observation_id=request.derived_observation_id,
            input_observation_id=role_inputs[input_requirement.role].observation_id,
            input_role=input_requirement.role,
            input_ordinal=ordinal,
            formula_version=derivation.formula_version,
            input_value=cast("Decimal", role_inputs[input_requirement.role].value),
        )
        for ordinal, input_requirement in enumerate(derivation.inputs)
    )
    trace = CalculationTrace(
        formula=derivation.formula,
        formula_version=derivation.formula_version,
        averaging=derivation.averaging,
        annualization=derivation.annualization,
        unquantized_value=unquantized,
        quantum=definition.quantization.quantum,
        observed_days=request.annualization.observed_days if request.annualization else None,
        basis_days=request.annualization.basis_days if request.annualization else None,
    )
    result = DerivedMetricResult(
        observation_id=request.derived_observation_id,
        issuer_id=request.issuer_id,
        metric_id=request.metric_id,
        metric_version=request.metric_version,
        value=value,
        unit=definition.unit,
        period_type=request.period_type,
        period_start=request.period_start,
        period_end=request.period_end,
        reporting_entity_id=request.reporting_entity_id,
        reporting_scope_id=request.reporting_scope_id,
        methodology=MetricMethodology.DETERMINISTIC_DERIVED,
        dimensions=request.dimensions,
        trace=trace,
        lineage=lineage,
        scale=request.scale,
    )
    return DerivationDecision(DecisionDisposition.VALIDATED, (), result)


def reconcile_cross_source(
    left: MetricInput,
    right: MetricInput,
    *,
    rule_id: str,
    catalog: MetricCatalog,
) -> CrossSourceReconciliationDecision:
    """Reconcile SEC and regulatory values; every mismatch is quarantined."""
    rule = catalog.cross_source_rule(rule_id)
    if rule is None:
        return _reconciliation_quarantine(
            rule_id,
            DecisionReason.CROSS_SOURCE_RULE_UNKNOWN,
        )
    sec, regulatory = _orient_cross_source(left, right, rule)
    if (
        sec is None
        or regulatory is None
        or not _cross_source_semantics_match(sec, regulatory, rule)
    ):
        return _reconciliation_quarantine(
            rule_id,
            DecisionReason.RECONCILIATION_SEMANTICS_MISMATCH,
        )
    assert sec.value is not None  # noqa: S101
    assert regulatory.value is not None  # noqa: S101
    difference = abs(sec.value - regulatory.value)
    if difference > rule.absolute_tolerance:
        return CrossSourceReconciliationDecision(
            disposition=DecisionDisposition.QUARANTINED,
            reasons=(DecisionReason.RECONCILIATION_VALUE_MISMATCH,),
            rule_id=rule_id,
            absolute_difference=difference,
        )
    return CrossSourceReconciliationDecision(
        disposition=DecisionDisposition.VALIDATED,
        reasons=(),
        rule_id=rule_id,
        absolute_difference=difference,
    )


def _load_yaml_mapping(path: Path) -> YamlMapping:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MetricEngineError(f"unable to load metric catalog {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise MetricEngineError(f"metric catalog root must be a mapping: {path}")
    return cast("YamlMapping", raw)


def _parse_base_definitions(root: YamlMapping) -> tuple[MetricDefinition, ...]:
    metrics = _mapping_sequence(root, "metrics", context="base catalog")
    parsed: list[MetricDefinition] = []
    for metric in metrics:
        unit = _enum(MetricUnit, _text(metric, "unit", context="base metric"))
        scale_text = _text(metric, "scale", context="base metric")
        scale = _decimal(_SCALES.get(scale_text, scale_text), "base metric scale")
        methods = tuple(
            _base_methodology(item)
            for item in _text_sequence(metric, "methodology_variants", default=("reported",))
        )
        parsed.append(
            MetricDefinition(
                metric_id=_text(metric, "id", context="base metric"),
                semantic_version=_text(metric, "semantic_version", context="base metric"),
                category=_text(metric, "category", context="base metric"),
                definition=_text(metric, "business_meaning", context="base metric"),
                unit=unit,
                scale=scale,
                period_types=(
                    _enum(
                        PeriodType,
                        _text(metric, "period_semantics", context="base metric"),
                    ),
                ),
                methodologies=methods,
                dimensions=(),
                validation_rules=(ValidationRule.FINITE,),
                comparability=ComparabilityDefinition(MethodologyPolicy.EXACT, ()),
                reconciliation_rules=_text_sequence(
                    metric,
                    "reconciliation_rules",
                    default=(),
                ),
                quantization=QuantizationRule(
                    _decimal(_DEFAULT_QUANTA[unit.value], "base metric quantum"),
                    RoundingMethod.ROUND_HALF_EVEN,
                ),
                derivation=None,
                lifecycle=_definition_lifecycle(metric),
            )
        )
    return tuple(parsed)


def _parse_taxonomies(root: YamlMapping) -> tuple[DimensionTaxonomy, ...]:
    return tuple(
        DimensionTaxonomy(
            taxonomy_id=_text(item, "id", context="dimension taxonomy"),
            values=_text_sequence(item, "values"),
        )
        for item in _mapping_sequence(root, "dimension_taxonomies", context="catalog")
    )


def _parse_versioned_definitions(root: YamlMapping) -> tuple[MetricDefinition, ...]:
    parsed: list[MetricDefinition] = []
    for metric in _mapping_sequence(root, "metric_versions", context="catalog"):
        unit = _enum(MetricUnit, _text(metric, "unit", context="metric version"))
        quantization_raw = _mapping(metric, "quantization", context="metric version")
        comparison_raw = _mapping(metric, "comparability", context="metric version")
        derivation_raw = metric.get("derivation")
        parsed.append(
            MetricDefinition(
                metric_id=_text(metric, "metric_id", context="metric version"),
                semantic_version=_text(metric, "semantic_version", context="metric version"),
                category=_text(metric, "category", context="metric version"),
                definition=_text(metric, "definition", context="metric version"),
                unit=unit,
                scale=_decimal(_text(metric, "scale", context="metric version"), "scale"),
                period_types=tuple(
                    _enum(PeriodType, item) for item in _text_sequence(metric, "period_types")
                ),
                methodologies=tuple(
                    _enum(MetricMethodology, item)
                    for item in _text_sequence(metric, "methodologies")
                ),
                dimensions=tuple(
                    DimensionRequirement(
                        taxonomy=_text(item, "taxonomy", context="metric dimension"),
                        fixed_value=_optional_text(item, "fixed_value"),
                    )
                    for item in _mapping_sequence(metric, "dimensions", context="metric version")
                ),
                validation_rules=tuple(
                    _enum(ValidationRule, item)
                    for item in _text_sequence(metric, "validation_rules")
                ),
                comparability=ComparabilityDefinition(
                    methodology_policy=_enum(
                        MethodologyPolicy,
                        _text(
                            comparison_raw,
                            "methodology_policy",
                            context="comparability",
                        ),
                    ),
                    dimensions=_text_sequence(comparison_raw, "dimensions"),
                ),
                reconciliation_rules=_text_sequence(metric, "reconciliation_rules"),
                quantization=QuantizationRule(
                    quantum=_decimal(
                        _text(quantization_raw, "quantum", context="quantization"),
                        "quantum",
                    ),
                    rounding=_enum(
                        RoundingMethod,
                        _text(quantization_raw, "rounding", context="quantization"),
                    ),
                ),
                derivation=(
                    None
                    if derivation_raw is None
                    else _parse_derivation(_as_mapping(derivation_raw, "derivation"))
                ),
                lifecycle=_definition_lifecycle(metric),
            )
        )
    return tuple(parsed)


def _definition_lifecycle(raw: YamlMapping) -> DefinitionLifecycle:
    """Read optional lifecycle metadata without making it a semantic field."""
    value = raw.get("lifecycle", DefinitionLifecycle.CURRENT.value)
    if not isinstance(value, str):
        raise MetricEngineError("metric definition lifecycle must be a string")
    return _enum(DefinitionLifecycle, value)


def _semantic_version_key(value: str) -> tuple[object, ...]:
    """Sort semantic versions numerically while failing closed on odd labels."""
    pieces = value.split(".")
    result: list[object] = []
    for piece in pieces:
        result.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(result)


def _assign_definition_lifecycle(
    definitions: tuple[MetricDefinition, ...],
) -> tuple[MetricDefinition, ...]:
    """Mark the newest version current and retain all prior versions historically."""
    by_metric: dict[str, list[MetricDefinition]] = {}
    for definition in definitions:
        by_metric.setdefault(definition.metric_id, []).append(definition)
    current_by_metric: dict[str, tuple[str, str]] = {}
    for metric_id, versions in by_metric.items():
        newest = max(versions, key=lambda item: _semantic_version_key(item.semantic_version))
        current_by_metric[metric_id] = newest.key
    return tuple(
        replace(
            definition,
            lifecycle=(
                DefinitionLifecycle.CURRENT
                if definition.key == current_by_metric[definition.metric_id]
                else DefinitionLifecycle.HISTORICAL
            ),
        )
        for definition in definitions
    )


def _parse_derivation(raw: YamlMapping) -> DerivationDefinition:
    return DerivationDefinition(
        formula=_enum(FormulaKind, _text(raw, "formula", context="derivation")),
        formula_version=_text(raw, "formula_version", context="derivation"),
        averaging=_enum(
            AveragingMethod,
            _text(raw, "averaging", context="derivation"),
        ),
        annualization=_enum(
            AnnualizationMethod,
            _text(raw, "annualization", context="derivation"),
        ),
        inputs=tuple(
            InputRequirement(
                role=_text(item, "role", context="derivation input"),
                metric_ids=_text_sequence(item, "metric_ids"),
                unit=_enum(MetricUnit, _text(item, "unit", context="derivation input")),
                period_relation=_enum(
                    PeriodRelation,
                    _text(item, "period_relation", context="derivation input"),
                ),
                allowed_scope_pairs=tuple(
                    _derivation_scope_relationship(pair)
                    for pair in _raw_sequence_optional(item, "allowed_scope_pairs")
                ),
            )
            for item in _mapping_sequence(raw, "inputs", context="derivation")
        ),
    )


def _parse_cross_source_rules(root: YamlMapping) -> tuple[CrossSourceRule, ...]:
    return tuple(
        CrossSourceRule(
            rule_id=_text(raw, "rule_id", context="cross-source rule"),
            issuer_id=_text(raw, "issuer_id", context="cross-source rule"),
            metric_id=_text(raw, "metric_id", context="cross-source rule"),
            sec_methodologies=tuple(
                _enum(MetricMethodology, item) for item in _text_sequence(raw, "sec_methodologies")
            ),
            regulatory_methodologies=tuple(
                _enum(MetricMethodology, item)
                for item in _text_sequence(raw, "regulatory_methodologies")
            ),
            allowed_scope_pairs=tuple(
                _scope_pair(item)
                for item in _raw_sequence(raw, "allowed_scope_pairs", "cross-source rule")
            ),
            unit=_enum(MetricUnit, _text(raw, "unit", context="cross-source rule")),
            period_types=tuple(
                _enum(PeriodType, item) for item in _text_sequence(raw, "period_types")
            ),
            absolute_tolerance=_decimal(
                _text(raw, "absolute_tolerance", context="cross-source rule"),
                "cross-source tolerance",
            ),
            mismatch_disposition=_enum(
                ReconciliationDisposition,
                _text(raw, "mismatch_disposition", context="cross-source rule"),
            ),
        )
        for raw in _mapping_sequence(root, "cross_source_rules", context="catalog")
    )


def _validate_derivation_inputs(
    request: DerivationRequest,
    definition: MetricDefinition,
    catalog: MetricCatalog,
) -> tuple[tuple[DecisionReason, ...], dict[str, MetricInput]]:
    assert definition.derivation is not None  # noqa: S101
    expected = {item.role: item for item in definition.derivation.inputs}
    supplied: dict[str, MetricInput] = {}
    reasons: list[DecisionReason] = []
    observation_ids: set[str] = set()
    for role, observation in request.inputs:
        if role not in expected:
            reasons.append(DecisionReason.INPUT_ROLE_UNEXPECTED)
            continue
        if role in supplied:
            reasons.append(DecisionReason.INPUT_ROLE_DUPLICATE)
            continue
        supplied[role] = observation
        if not observation.observation_id.strip():
            reasons.append(DecisionReason.INPUT_OBSERVATION_ID_MISSING)
        elif observation.observation_id in observation_ids:
            reasons.append(DecisionReason.INPUT_OBSERVATION_ID_DUPLICATE)
        observation_ids.add(observation.observation_id)
    if set(expected) - set(supplied):
        reasons.append(DecisionReason.INPUT_ROLE_MISSING)
    if request.period_type not in definition.period_types:
        reasons.append(DecisionReason.PERIOD_MISMATCH)
    reasons.extend(_dimension_reasons(request.dimensions, definition, catalog))
    for role, observation in supplied.items():
        requirement = expected[role]
        if observation.publication_status is not PublicationStatus.PUBLISHED:
            reasons.append(DecisionReason.INPUT_UNPUBLISHED)
        if observation.completeness is not Completeness.COMPLETE:
            reasons.append(DecisionReason.INPUT_INCOMPLETE)
        if observation.value_state not in {ValueState.REPORTED_ACTUAL, ValueState.DERIVED}:
            reasons.append(DecisionReason.INPUT_NOT_ACTUAL)
        if observation.value is None:
            reasons.append(DecisionReason.INPUT_VALUE_MISSING)
        elif not observation.value.is_finite():
            reasons.append(DecisionReason.INPUT_VALUE_NONFINITE)
        if (
            observation.metric_id not in requirement.metric_ids
            or catalog.definition(observation.metric_id, observation.metric_version) is None
        ):
            reasons.append(DecisionReason.INPUT_METRIC_MISMATCH)
        if observation.unit is not requirement.unit:
            reasons.append(DecisionReason.INPUT_UNIT_MISMATCH)
        if observation.scale != request.scale:
            reasons.append(DecisionReason.INPUT_SCALE_MISMATCH)
        if observation.issuer_id != request.issuer_id:
            reasons.append(DecisionReason.INPUT_ISSUER_MISMATCH)
        if observation.reporting_entity_id != request.reporting_entity_id:
            reasons.append(DecisionReason.INPUT_ENTITY_MISMATCH)
        scope_pair = AllowedScopeRelationship(
            observation.reporting_scope_id,
            request.reporting_scope_id,
        )
        scope_matches = (
            observation.reporting_scope_id == request.reporting_scope_id
            or scope_pair in requirement.allowed_scope_pairs
        )
        if not scope_matches:
            reasons.append(DecisionReason.INPUT_SCOPE_MISMATCH)
        if not _derivation_dimensions_match(
            observation,
            request,
            requirement,
            definition.derivation.formula,
            catalog,
        ):
            reasons.append(DecisionReason.INPUT_DIMENSION_MISMATCH)
        if observation.value_state is ValueState.DERIVED and not observation.formula_version:
            reasons.append(DecisionReason.INPUT_FORMULA_VERSION_MISSING)
        if not _period_relation_matches(observation, request, requirement.period_relation):
            reasons.append(DecisionReason.INPUT_PERIOD_MISMATCH)
    reasons.extend(_parameter_reasons(request, definition.derivation, supplied))
    return _unique_reasons(reasons), supplied


def _parameter_reasons(
    request: DerivationRequest,
    derivation: DerivationDefinition,
    supplied: Mapping[str, MetricInput],
) -> tuple[DecisionReason, ...]:
    reasons: list[DecisionReason] = []
    if derivation.averaging is AveragingMethod.ARITHMETIC_BEGIN_END:
        if request.averaging is None:
            reasons.append(DecisionReason.AVERAGING_PARAMETERS_MISSING)
        else:
            beginning = next(
                (
                    supplied[item.role]
                    for item in derivation.inputs
                    if item.period_relation is PeriodRelation.BEGINNING_INSTANT
                    and item.role in supplied
                ),
                None,
            )
            ending = next(
                (
                    supplied[item.role]
                    for item in derivation.inputs
                    if item.period_relation is PeriodRelation.ENDING_INSTANT
                    and item.role in supplied
                ),
                None,
            )
            if (
                beginning is None
                or ending is None
                or beginning.period_end != request.averaging.beginning_date
                or ending.period_end != request.averaging.ending_date
            ):
                reasons.append(DecisionReason.AVERAGING_PARAMETERS_MISMATCH)
    elif request.averaging is not None:
        reasons.append(DecisionReason.AVERAGING_PARAMETERS_MISMATCH)
    if derivation.annualization in {
        AnnualizationMethod.ACTUAL_DAYS,
        AnnualizationMethod.ACTUAL_OVER_BASIS,
    }:
        if request.annualization is None:
            reasons.append(DecisionReason.ANNUALIZATION_PARAMETERS_MISSING)
        elif (
            request.period_start is None
            or request.annualization.observed_days
            != (request.period_end - request.period_start).days + 1
        ):
            reasons.append(DecisionReason.ANNUALIZATION_PARAMETERS_MISMATCH)
    elif request.annualization is not None:
        reasons.append(DecisionReason.ANNUALIZATION_PARAMETERS_MISMATCH)
    return tuple(reasons)


def _calculate(
    request: DerivationRequest,
    derivation: DerivationDefinition,
    values: Mapping[str, Decimal],
    *,
    output_unit: MetricUnit,
) -> tuple[Decimal | None, DecisionReason | None]:
    with localcontext() as context:
        context.prec = 50
        if derivation.formula is FormulaKind.SUM_INPUTS:
            return sum(values.values(), start=Decimal(0)), None
        if derivation.formula in {
            FormulaKind.RATIO,
            FormulaKind.SUM_INPUTS_OVER_DENOMINATOR,
        }:
            denominator = values["denominator"]
            if denominator <= 0:
                return None, DecisionReason.DENOMINATOR_NOT_POSITIVE
            numerator = (
                values["numerator"]
                if derivation.formula is FormulaKind.RATIO
                else sum(
                    (value for role, value in values.items() if role != "denominator"),
                    start=Decimal(0),
                )
            )
            ratio = numerator / denominator
            return (ratio * _BPS if output_unit is MetricUnit.BASIS_POINTS else ratio), None
        if derivation.formula is FormulaKind.ANNUALIZED_EXPENSE_PER_AVERAGE_LOAN:
            denominator = (values["beginning_loan_count"] + values["ending_loan_count"]) / _TWO
            if denominator <= 0:
                return None, DecisionReason.DENOMINATOR_NOT_POSITIVE
            assert request.annualization is not None  # noqa: S101
            annualization = request.annualization.basis_days / Decimal(
                request.annualization.observed_days
            )
            return values["expense"] / denominator * annualization, None
        if derivation.formula is FormulaKind.ANNUALIZED_RATE_BPS:
            denominator = (values["beginning_upb"] + values["ending_upb"]) / _TWO
            if denominator <= 0:
                return None, DecisionReason.DENOMINATOR_NOT_POSITIVE
            assert request.annualization is not None  # noqa: S101
            annualization = request.annualization.basis_days / Decimal(
                request.annualization.observed_days
            )
            return values["fee_income"] / denominator * annualization * _BPS, None
        if values["related_upb"] <= 0:
            return None, DecisionReason.DENOMINATOR_NOT_POSITIVE
        multiple = values["fair_value"] / values["related_upb"]
        if derivation.formula is FormulaKind.FAIR_VALUE_TO_UPB_BPS:
            return multiple * _BPS, None
        return multiple, None


def _derivation_dimensions_match(
    observation: MetricInput,
    request: DerivationRequest,
    requirement: InputRequirement,
    formula: FormulaKind,
    catalog: MetricCatalog,
) -> bool:
    """Require exact context except for governed component formulas.

    Component observations intentionally carry narrower category or threshold
    dimensions than their aggregate/rate output.  For those formulas, every
    dimension shared by the input and output must agree, while the catalog's
    fixed dimensions retain authority for metric-specific component semantics.
    """
    if formula not in {
        FormulaKind.SUM_INPUTS,
        FormulaKind.RATIO,
        FormulaKind.SUM_INPUTS_OVER_DENOMINATOR,
    }:
        return observation.dimensions == request.dimensions
    definition = next(
        (
            catalog.definition(metric_id, observation.metric_version)
            for metric_id in requirement.metric_ids
            if metric_id == observation.metric_id
        ),
        None,
    )
    if definition is None:
        return False
    input_values = {item.name: item.value for item in observation.dimensions}
    output_values = {item.name: item.value for item in request.dimensions}
    shared = input_values.keys() & output_values.keys() & _DERIVATION_CONTEXT_DIMENSIONS
    if any(input_values[name] != output_values[name] for name in shared):
        return False
    required_input_dimensions = {item.taxonomy for item in definition.dimensions}
    return required_input_dimensions == input_values.keys()


def _period_relation_matches(
    observation: MetricInput,
    request: DerivationRequest,
    relation: PeriodRelation,
) -> bool:
    if relation is PeriodRelation.SAME_INSTANT:
        return (
            observation.period_type is PeriodType.INSTANT
            and observation.period_start is None
            and request.period_type is PeriodType.INSTANT
            and observation.period_end == request.period_end
        )
    if relation is PeriodRelation.SAME_DURATION:
        return (
            observation.period_type in {PeriodType.DURATION, PeriodType.DURATION_YTD}
            and request.period_type in {PeriodType.DURATION, PeriodType.DURATION_YTD}
            and observation.period_type is request.period_type
            and observation.period_start == request.period_start
            and observation.period_end == request.period_end
        )
    if relation is PeriodRelation.BEGINNING_INSTANT:
        return observation.period_type is PeriodType.INSTANT and (
            request.period_start is not None and observation.period_end < request.period_start
        )
    return (
        observation.period_type is PeriodType.INSTANT
        and observation.period_end == request.period_end
    )


def _dimension_reasons(
    dimensions: tuple[MetricDimension, ...],
    definition: MetricDefinition,
    catalog: MetricCatalog,
) -> tuple[DecisionReason, ...]:
    supplied = _dimension_map(dimensions)
    expected = {item.taxonomy: item for item in definition.dimensions}
    taxonomies = {item.taxonomy_id: item for item in catalog.dimension_taxonomies}
    reasons: list[DecisionReason] = []
    if set(expected) - set(supplied):
        reasons.append(DecisionReason.DIMENSION_MISSING)
    if set(supplied) - set(expected):
        reasons.append(DecisionReason.DIMENSION_UNEXPECTED)
    for name, value in supplied.items():
        taxonomy = taxonomies.get(name)
        if taxonomy is not None and value not in taxonomy.values:
            reasons.append(DecisionReason.DIMENSION_VALUE_INVALID)
        requirement = expected.get(name)
        if (
            requirement is not None
            and requirement.fixed_value is not None
            and value != requirement.fixed_value
        ):
            reasons.append(DecisionReason.FIXED_DIMENSION_MISMATCH)
    return _unique_reasons(reasons)


def _value_rule_reasons(
    value: Decimal,
    rules: tuple[ValidationRule, ...],
) -> tuple[DecisionReason, ...]:
    for rule in rules:
        if rule is ValidationRule.FINITE and not value.is_finite():
            return (DecisionReason.VALUE_RULE_FAILED,)
        if rule is ValidationRule.NONNEGATIVE and value < 0:
            return (DecisionReason.VALUE_RULE_FAILED,)
        if rule is ValidationRule.RATIO_0_1 and not Decimal(0) <= value <= Decimal(1):
            return (DecisionReason.VALUE_RULE_FAILED,)
        if rule is ValidationRule.BPS_0_10000 and not Decimal(0) <= value <= _BPS:
            return (DecisionReason.VALUE_RULE_FAILED,)
    return ()


def _orient_cross_source(
    left: MetricInput,
    right: MetricInput,
    rule: CrossSourceRule,
) -> tuple[MetricInput | None, MetricInput | None]:
    if (
        left.methodology in rule.sec_methodologies
        and right.methodology in rule.regulatory_methodologies
    ):
        return left, right
    if (
        right.methodology in rule.sec_methodologies
        and left.methodology in rule.regulatory_methodologies
    ):
        return right, left
    return None, None


def _cross_source_semantics_match(
    sec: MetricInput,
    regulatory: MetricInput,
    rule: CrossSourceRule,
) -> bool:
    scope_pair = ScopePair(sec.reporting_scope_id, regulatory.reporting_scope_id)
    values_valid = all(
        item.value is not None
        and item.value.is_finite()
        and item.publication_status is PublicationStatus.PUBLISHED
        and item.completeness is Completeness.COMPLETE
        and item.value_state is ValueState.REPORTED_ACTUAL
        for item in (sec, regulatory)
    )
    return (
        values_valid
        and sec.issuer_id == regulatory.issuer_id == rule.issuer_id
        and sec.metric_id == regulatory.metric_id == rule.metric_id
        and sec.metric_version == regulatory.metric_version
        and sec.unit is regulatory.unit is rule.unit
        and sec.scale == regulatory.scale
        and sec.period_type is regulatory.period_type
        and sec.period_type in rule.period_types
        and sec.period_start == regulatory.period_start
        and sec.period_end == regulatory.period_end
        and scope_pair in rule.allowed_scope_pairs
        and sec.dimensions == regulatory.dimensions
    )


def _validation_quarantine(reason: DecisionReason) -> ValidationDecision:
    return ValidationDecision(DecisionDisposition.QUARANTINED, (reason,))


def _derivation_quarantine(reason: DecisionReason) -> DerivationDecision:
    return DerivationDecision(DecisionDisposition.QUARANTINED, (reason,), None)


def _reconciliation_quarantine(
    rule_id: str,
    reason: DecisionReason,
) -> CrossSourceReconciliationDecision:
    return CrossSourceReconciliationDecision(
        disposition=DecisionDisposition.QUARANTINED,
        reasons=(reason,),
        rule_id=rule_id,
        absolute_difference=None,
    )


def _unique_reasons(reasons: Sequence[DecisionReason]) -> tuple[DecisionReason, ...]:
    return tuple(sorted(set(reasons), key=lambda item: item.value))


def _dimension_map(dimensions: tuple[MetricDimension, ...]) -> dict[str, str]:
    return {item.name: item.value for item in dimensions}


def _base_methodology(raw: str) -> MetricMethodology:
    mapping = {
        "reported": MetricMethodology.ISSUER_REPORTED,
        "deterministically derived": MetricMethodology.DETERMINISTIC_DERIVED,
    }
    try:
        return mapping[raw]
    except KeyError as exc:
        raise MetricEngineError(f"unknown base methodology: {raw}") from exc


def _scope_pair(raw: object) -> ScopePair:
    if not isinstance(raw, list) or len(raw) != 2 or not all(isinstance(item, str) for item in raw):
        raise MetricEngineError("scope pair must contain exactly two strings")
    return ScopePair(cast("str", raw[0]), cast("str", raw[1]))


def _derivation_scope_relationship(raw: object) -> AllowedScopeRelationship:
    if not isinstance(raw, list) or len(raw) != 2 or not all(isinstance(item, str) for item in raw):
        raise MetricEngineError("derivation scope pair must contain exactly two strings")
    return AllowedScopeRelationship(cast("str", raw[0]), cast("str", raw[1]))


def _duplicates(items: Sequence[ItemT]) -> tuple[ItemT, ...]:
    seen: set[ItemT] = set()
    duplicates: set[ItemT] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return tuple(sorted(duplicates, key=repr))


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")


def _decimal(raw: str, context: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise MetricEngineError(f"{context} must be an exact decimal string") from exc
    return value


def _enum(enum_type: type[EnumT], raw: str) -> EnumT:
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise MetricEngineError(f"unknown {enum_type.__name__}: {raw}") from exc


def _as_mapping(raw: object, context: str) -> YamlMapping:
    if not isinstance(raw, Mapping):
        raise MetricEngineError(f"{context} must be a mapping")
    return cast("YamlMapping", raw)


def _mapping(root: YamlMapping, key: str, *, context: str) -> YamlMapping:
    if key not in root:
        raise MetricEngineError(f"{context}.{key} is required")
    return _as_mapping(root[key], f"{context}.{key}")


def _mapping_sequence(root: YamlMapping, key: str, *, context: str) -> tuple[YamlMapping, ...]:
    return tuple(
        _as_mapping(item, f"{context}.{key}") for item in _raw_sequence(root, key, context)
    )


def _raw_sequence(root: YamlMapping, key: str, context: str) -> tuple[object, ...]:
    raw = root.get(key)
    if not isinstance(raw, list):
        raise MetricEngineError(f"{context}.{key} must be a sequence")
    return tuple(raw)


def _raw_sequence_optional(root: YamlMapping, key: str) -> tuple[object, ...]:
    raw = root.get(key, ())
    if not isinstance(raw, (list, tuple)):
        raise MetricEngineError(f"{key} must be a sequence")
    return tuple(raw)


def _text(root: YamlMapping, key: str, *, context: str) -> str:
    raw = root.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise MetricEngineError(f"{context}.{key} must be a nonblank string")
    return raw


def _optional_text(root: YamlMapping, key: str) -> str | None:
    raw = root.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise MetricEngineError(f"{key} must be null or a nonblank string")
    return raw


def _text_sequence(
    root: YamlMapping,
    key: str,
    *,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    raw = root.get(key)
    if raw is None and default is not None:
        return default
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise MetricEngineError(f"{key} must be a sequence of nonblank strings")
    return tuple(cast("list[str]", raw))
