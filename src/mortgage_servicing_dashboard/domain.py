"""Deterministic financial-domain primitives with no agent dependencies."""

from __future__ import annotations

from dataclasses import dataclass
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


def parse_decimal(raw_value: str, *, scale: str = "ones") -> Decimal:
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
    cleaned = raw_value.strip().replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as error:
        msg = "reported value is not an exact decimal"
        raise ValueError(msg) from error
    return (-value if negative else value) * _SCALES[scale]


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


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Deterministic pairwise comparability result."""

    status: ComparabilityStatus
    reasons: tuple[str, ...]


def assess_comparability(  # noqa: C901
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
    if left.reporting_scope != right.reporting_scope:
        hard_mismatches.append("reporting scopes differ")
    if hard_mismatches:
        return ComparisonResult(ComparabilityStatus.NOT_COMPARABLE, tuple(hard_mismatches))
    caveats: list[str] = []
    if left.period_days != right.period_days:
        caveats.append("period lengths differ")
    if left.methodology != right.methodology:
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
