"""Deterministic financial-domain primitives with no agent dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class ObservationState(StrEnum):
    """Publication state of a metric observation."""

    REPORTED_ACTUAL = "REPORTED_ACTUAL"
    PRELIMINARY_REPORTED = "PRELIMINARY_REPORTED"
    PRO_FORMA = "PRO_FORMA"
    ANNOUNCED_IMPACT = "ANNOUNCED_IMPACT"
    DERIVED = "DERIVED"
    NOT_DISCLOSED = "NOT_DISCLOSED"


class QualityState(StrEnum):
    """Deterministic quality disposition."""

    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class PublicationState(StrEnum):
    """Repository publication disposition, separate from financial state."""

    CANDIDATE = "CANDIDATE"
    QUARANTINED = "QUARANTINED"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class TerminalOutcome(StrEnum):
    """Explicit cell-level pipeline terminal outcome."""

    PUBLISHED = "PUBLISHED"
    NOT_DISCLOSED = "NOT_DISCLOSED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class ComparabilityStatus(StrEnum):
    """Pairwise comparability outcome."""

    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_INFORMATION = "insufficient_information"


_SCALES = {
    "ones": Decimal(1),
    "thousands": Decimal(1_000),
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
    "percent": Decimal("0.01"),
    "basis_points": Decimal("0.0001"),
}


def parse_decimal(raw_value: object, *, scale: str = "ones") -> Decimal:
    """Parse a reported number exactly, including commas and parentheses.

    Args:
        raw_value: Issuer-reported numeric text.
        scale: Named multiplier applied to the reported number.

    Returns:
        Exact normalized `Decimal` value.

    Raises:
        ValueError: If the number or scale is unsupported.
    """
    if scale not in _SCALES:
        msg = f"unsupported scale: {scale}"
        raise ValueError(msg)
    if not isinstance(raw_value, str):
        msg = "authoritative numeric input must be source text, never float"
        raise TypeError(msg)
    cleaned = raw_value.strip().replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as error:
        msg = "reported value is not an exact decimal"
        raise ValueError(msg) from error
    if not value.is_finite():
        msg = "reported value must be a finite exact decimal"
        raise ValueError(msg)
    return (-value if negative else value) * _SCALES[scale]


def normalize_reported_value(raw_value: str, *, rule: str) -> Decimal:
    """Normalize source text to the metric's canonical exact unit.

    Args:
        raw_value: Numeric text taken directly from retained evidence.
        rule: Versioned deterministic normalization recipe.

    Returns:
        Exact canonical value without a binary-float conversion.

    Raises:
        ValueError: If the recipe is not allow-listed.
        TypeError: If the authoritative input is not text.
    """
    scale_by_rule = {
        "identity": "ones",
        "usd_from_millions": "millions",
        "usd_from_billions": "billions",
        "percent_to_ratio": "percent",
    }
    if rule in scale_by_rule:
        return parse_decimal(raw_value, scale=scale_by_rule[rule])
    if rule == "percent_to_basis_points":
        return parse_decimal(raw_value) * Decimal(100)
    msg = f"unsupported normalization rule: {rule}"
    raise ValueError(msg)


def decimal_places(raw_value: str) -> int:
    """Return source-displayed decimal places without parsing through float.

    Args:
        raw_value: Exact source numeric text.

    Returns:
        Count of digits displayed after the decimal point.
    """
    cleaned = raw_value.strip().strip("()").replace(",", "").replace("$", "")
    _, dot, fractional = cleaned.partition(".")
    return len(fractional) if dot else 0


@dataclass(frozen=True, slots=True)
class ParsedObservationCandidate:
    """Deterministic candidate extracted from one retained evidence row."""

    candidate_id: str
    company_id: str
    metric_id: str
    metric_version: str
    period_start: date | None
    period_end: date
    fiscal_year: int
    fiscal_quarter: int
    period_type: str
    raw_label: str
    raw_value: str
    normalized_value: Decimal
    currency: str | None
    unit: str
    reported_scale: str
    reported_decimals: int
    observation_state: ObservationState
    methodology: str
    reporting_entity_id: str
    reporting_scope_id: str
    evidence_id: str
    evidence_locator: str
    extraction_method: str
    parser_name: str
    parser_version: str

    @property
    def canonical_scale(self) -> str:
        """Return the scale of ``normalized_value`` rather than source display scale.

        ``reported_scale`` records how the issuer printed a value (for example,
        millions).  The stored Decimal has already been normalized to canonical
        units, so strict comparisons and metric-engine inputs use scale ``1``.
        """
        return "1"

    @property
    def semantic_key_digest(self) -> str:
        """Return a stable semantic identity independent of acquisition time."""
        identity = {
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "reporting_entity_id": self.reporting_entity_id,
            "reporting_scope_id": self.reporting_scope_id,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat(),
            "period_type": self.period_type,
            "observation_state": self.observation_state.value,
            "methodology": self.methodology,
            "currency": self.currency,
            "unit": self.unit,
            "scale": self.canonical_scale,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Fail-closed deterministic validation result."""

    valid: bool
    code: str
    summary: str


def validate_candidate(candidate: ParsedObservationCandidate) -> ValidationResult:
    """Validate required candidate semantics before repository publication.

    Args:
        candidate: Fully resolved deterministic extraction candidate.

    Returns:
        Stable validation result suitable for persistence and audit.
    """
    required_text = (
        candidate.company_id,
        candidate.metric_id,
        candidate.metric_version,
        candidate.raw_label,
        candidate.raw_value,
        candidate.reporting_entity_id,
        candidate.reporting_scope_id,
        candidate.evidence_id,
        candidate.evidence_locator,
    )
    if any(not value.strip() for value in required_text):
        return ValidationResult(
            valid=False,
            code="REQUIRED_SEMANTIC_MISSING",
            summary="required semantic is blank",
        )
    if not candidate.normalized_value.is_finite():
        return ValidationResult(
            valid=False,
            code="NON_FINITE_VALUE",
            summary="normalized value is not finite",
        )
    if candidate.observation_state is ObservationState.NOT_DISCLOSED:
        return ValidationResult(
            valid=False,
            code="MEASURED_NOT_DISCLOSED_CONFLICT",
            summary="a measured candidate cannot carry NOT_DISCLOSED state",
        )
    return ValidationResult(
        valid=True,
        code="VALIDATED",
        summary="retained bytes, locator, semantics, and exact normalization validated",
    )


@dataclass(frozen=True, slots=True)
class ComparisonInput:
    """Semantic attributes used in a pairwise comparison."""

    metric_id: str
    metric_version: str
    reporting_scope: str
    period_days: int | None
    currency: str | None
    unit: str
    methodology: str
    observation_state: ObservationState
    portfolio_population: str
    dimensions: tuple[tuple[str, str], ...] = ()
    # The legacy fields above remain accepted for archived callers. New
    # repository comparisons populate the complete identity below.
    period_kind: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    scale: str | None = None
    reporting_entity: str | None = None
    fiscal_calendar_regime: str | None = None
    accounting_policy_regime: str | None = None
    cross_company_comparison: bool = False


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Deterministic pairwise comparability result."""

    status: ComparabilityStatus
    reasons: tuple[str, ...]


def assess_comparability(  # noqa: C901, PLR0912
    left: ComparisonInput,
    right: ComparisonInput,
) -> ComparisonResult:
    """Assess two observations without assigning a universal observation flag.

    Args:
        left: First observation semantics.
        right: Second observation semantics.

    Returns:
        Pairwise status and explicit reasons.
    """
    if ObservationState.NOT_DISCLOSED in {left.observation_state, right.observation_state}:
        return ComparisonResult(
            ComparabilityStatus.INSUFFICIENT_INFORMATION,
            ("one or both issuers did not disclose the metric",),
        )
    hard_mismatches: list[str] = []
    if left.metric_id != right.metric_id or left.metric_version != right.metric_version:
        hard_mismatches.append("metric definition or semantic version differs")
    if left.currency != right.currency or left.unit != right.unit:
        hard_mismatches.append("currency or unit differs")
    if left.portfolio_population != right.portfolio_population:
        hard_mismatches.append("portfolio populations differ")
    cross_company = left.cross_company_comparison and right.cross_company_comparison
    if left.reporting_scope != right.reporting_scope and (
        not cross_company or left.portfolio_population != right.portfolio_population
    ):
        hard_mismatches.append("reporting scopes differ")
    strict_period_identity = any(
        value is not None
        for value in (
            left.period_kind,
            right.period_kind,
            left.period_start,
            right.period_start,
            left.period_end,
            right.period_end,
        )
    )
    if strict_period_identity and (
        left.period_kind != right.period_kind
        or left.period_start != right.period_start
        or left.period_end != right.period_end
    ):
        hard_mismatches.append("period identity differs")
    if (left.scale is not None or right.scale is not None) and left.scale != right.scale:
        hard_mismatches.append("scales differ")
    if (
        not cross_company
        and (left.reporting_entity is not None or right.reporting_entity is not None)
        and left.reporting_entity != right.reporting_entity
    ):
        hard_mismatches.append("reporting entities differ")
    if (
        left.fiscal_calendar_regime is not None or right.fiscal_calendar_regime is not None
    ) and left.fiscal_calendar_regime != right.fiscal_calendar_regime:
        hard_mismatches.append("fiscal calendar regimes differ")
    if (
        left.accounting_policy_regime is not None or right.accounting_policy_regime is not None
    ) and left.accounting_policy_regime != right.accounting_policy_regime:
        hard_mismatches.append("accounting policy regimes differ")
    if left.dimensions != right.dimensions:
        hard_mismatches.append("controlled metric dimensions differ")
    methodology_mismatch = left.methodology != right.methodology
    if methodology_mismatch and strict_period_identity:
        hard_mismatches.append("methodologies differ")
    if hard_mismatches:
        return ComparisonResult(ComparabilityStatus.NOT_COMPARABLE, tuple(hard_mismatches))
    caveats: list[str] = []
    if not strict_period_identity and left.period_days != right.period_days:
        caveats.append("period lengths differ")
    if methodology_mismatch:
        caveats.append("methodologies differ")
    if left.observation_state != right.observation_state:
        caveats.append("observation states differ")
    if caveats:
        return ComparisonResult(ComparabilityStatus.COMPARABLE_WITH_CAVEATS, tuple(caveats))
    return ComparisonResult(ComparabilityStatus.COMPARABLE, ())


def reconcile_rollforward(
    *,
    beginning: Decimal,
    additions: tuple[Decimal, ...],
    reductions: tuple[Decimal, ...],
    ending: Decimal,
    tolerance: Decimal,
) -> bool:
    """Reconcile an MSR roll-forward using exact arithmetic.

    Args:
        beginning: Beginning balance.
        additions: Positive roll-forward components.
        reductions: Components subtracted from the balance.
        ending: Reported ending balance.
        tolerance: Maximum absolute accepted difference.

    Returns:
        Whether the roll-forward reconciles within tolerance.
    """
    calculated = beginning + sum(additions, Decimal(0)) - sum(reductions, Decimal(0))
    return abs(calculated - ending) <= tolerance
