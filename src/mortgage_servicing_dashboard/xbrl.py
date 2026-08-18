"""Deterministic SEC XBRL parsing, mapping, and reconciliation.

The adapters in this module consume already-acquired bytes. They deliberately
have no HTTP client and therefore cannot bypass the governed acquisition
boundary. Both SEC company-facts JSON and filing-level XBRL normalize to one
exact fact model before versioned issuer mappings are applied.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Final, TypeAlias, cast
from xml.etree import ElementTree as ET

import yaml

_XBRLI_NAMESPACE: Final = "http://www.xbrl.org/2003/instance"
_XBRLDI_NAMESPACE: Final = "http://xbrl.org/2006/xbrldi"
_XSI_NAMESPACE: Final = "http://www.w3.org/2001/XMLSchema-instance"
_INLINE_XBRL_NAMESPACES: Final = frozenset(
    {
        "http://www.xbrl.org/2008/inlineXBRL",
        "http://www.xbrl.org/2013/inlineXBRL",
    }
)
_INLINE_TRANSFORMATION_NAMESPACES: Final = frozenset(
    {
        "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26",
        "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
        "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16",
    }
)
_LEGACY_INLINE_TRANSFORMATION_NAMESPACE: Final = (
    "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
)
_RESERVED_FACT_NAMESPACES: Final = frozenset(
    {
        _XBRLI_NAMESPACE,
        _XBRLDI_NAMESPACE,
        *_INLINE_XBRL_NAMESPACES,
        "http://www.w3.org/1999/xhtml",
        "http://www.w3.org/2001/XMLSchema-instance",
    }
)
_MAX_CIK_DIGITS: Final = 10
_MAX_XML_BYTES: Final = 25_000_000
_NUM_DOT_DECIMAL_PATTERN: Final = re.compile(
    r"^(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$"
)

XbrlDecimals: TypeAlias = int | str | None


class XbrlSource(StrEnum):
    """Official structured representation from which a fact was parsed."""

    SEC_COMPANY_FACTS = "SEC_COMPANY_FACTS"
    SEC_FILING_XBRL = "SEC_FILING_XBRL"


class XbrlPeriodType(StrEnum):
    """XBRL instant or duration context semantics."""

    INSTANT = "instant"
    DURATION = "duration"


class XbrlMethodology(StrEnum):
    """Methodology labels that keep structured and exhibit facts distinct."""

    SEC_COMPANY_FACTS_XBRL = "SEC_COMPANY_FACTS_XBRL"
    SEC_FILING_XBRL = "SEC_FILING_XBRL"
    SEC_FILING_EXHIBIT = "SEC_FILING_EXHIBIT"


class DecisionDisposition(StrEnum):
    """Fail-closed disposition for mapping and reconciliation decisions."""

    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"


class PresentationSign(StrEnum):
    """Explicit normalized sign retained separately from the source attribute."""

    NEGATIVE = "NEGATIVE"
    ZERO = "ZERO"
    POSITIVE = "POSITIVE"


class XbrlDataError(ValueError):
    """Safe deterministic XBRL payload or mapping failure."""


@dataclass(frozen=True, order=True, slots=True)
class DimensionMember:
    """One exact dimension/member pair from an XBRL context."""

    dimension: str
    member: str

    def __post_init__(self) -> None:
        """Reject incomplete context semantics."""
        if not self.dimension.strip() or not self.member.strip():
            msg = "XBRL dimensions require nonblank dimension and member names"
            raise XbrlDataError(msg)


@dataclass(frozen=True, slots=True)
class XbrlFact:
    """One exact numeric fact parsed from retained official SEC bytes."""

    source: XbrlSource
    issuer_id: str
    cik: str
    taxonomy: str
    concept: str
    raw_value: str
    value: Decimal
    unit: str
    scale: Decimal
    decimals: XbrlDecimals
    period_type: XbrlPeriodType
    period_start: date | None
    period_end: date
    dimensions: tuple[DimensionMember, ...]
    entity_identifier: str
    context_id: str
    accession: str | None
    form: str | None
    filed: date | None
    evidence_id: str
    locator: str
    source_element_id: str | None = None
    source_sign: str | None = None
    source_precision: str | None = None
    presentation_sign: PresentationSign = PresentationSign.POSITIVE

    def __post_init__(self) -> None:
        """Enforce exact numeric and complete context invariants."""
        _require_decimal(self.value, field_name="XBRL fact value")
        _require_decimal(self.scale, field_name="XBRL fact scale")
        if self.scale <= 0:
            msg = "XBRL fact scale must be positive"
            raise XbrlDataError(msg)
        required = (
            self.issuer_id,
            self.cik,
            self.taxonomy,
            self.concept,
            self.raw_value,
            self.unit,
            self.entity_identifier,
            self.context_id,
            self.evidence_id,
            self.locator,
        )
        if any(not item.strip() for item in required):
            msg = "XBRL fact semantics must not be blank"
            raise XbrlDataError(msg)
        _validate_decimals(self.decimals)
        if self.source_sign not in {None, "-"}:
            msg = "XBRL source sign must be absent or the inline-XBRL minus marker"
            raise XbrlDataError(msg)
        if self.source_precision is not None and not self.source_precision.strip():
            msg = "XBRL source precision cannot be blank"
            raise XbrlDataError(msg)
        if self.period_type is XbrlPeriodType.INSTANT and self.period_start is not None:
            msg = "instant XBRL facts cannot have a period start"
            raise XbrlDataError(msg)
        if self.period_type is XbrlPeriodType.DURATION and (
            self.period_start is None or self.period_start > self.period_end
        ):
            msg = "duration XBRL facts require an ordered start and end"
            raise XbrlDataError(msg)
        if len(set(self.dimensions)) != len(self.dimensions):
            msg = "an XBRL context cannot repeat a dimension/member pair"
            raise XbrlDataError(msg)

    @property
    def qualified_concept(self) -> str:
        """Return the stable taxonomy-qualified concept name."""
        return f"{self.taxonomy}:{self.concept}"


@dataclass(frozen=True, slots=True)
class XbrlConceptMapping:
    """Versioned per-issuer mapping from one XBRL concept to one metric."""

    issuer_id: str
    cik: str
    metric_id: str
    metric_version: str
    taxonomy: str
    concept: str
    unit: str
    scale: Decimal
    decimals: XbrlDecimals
    period_type: XbrlPeriodType
    dimensions: tuple[DimensionMember, ...]
    reporting_entity_id: str
    reporting_scope_id: str
    eligible_sources: tuple[XbrlSource, ...]

    def __post_init__(self) -> None:
        """Validate mapping semantics before any fact can qualify."""
        _require_decimal(self.scale, field_name="XBRL mapping scale")
        if self.scale <= 0:
            msg = "XBRL mapping scale must be positive"
            raise XbrlDataError(msg)
        required = (
            self.issuer_id,
            self.cik,
            self.metric_id,
            self.metric_version,
            self.taxonomy,
            self.concept,
            self.unit,
            self.reporting_entity_id,
            self.reporting_scope_id,
        )
        if any(not item.strip() for item in required):
            msg = "XBRL concept mapping semantics must not be blank"
            raise XbrlDataError(msg)
        _validate_decimals(self.decimals)
        if not self.eligible_sources:
            msg = "XBRL concept mappings require at least one eligible source"
            raise XbrlDataError(msg)
        if len(set(self.dimensions)) != len(self.dimensions):
            msg = "XBRL concept mappings cannot repeat dimension/member pairs"
            raise XbrlDataError(msg)

    @property
    def qualified_concept(self) -> str:
        """Return the stable taxonomy-qualified concept name."""
        return f"{self.taxonomy}:{self.concept}"


@dataclass(frozen=True, slots=True)
class XbrlMappingRegistry:
    """Immutable versioned collection of issuer-specific concept mappings."""

    version: str
    mappings: tuple[XbrlConceptMapping, ...]

    def __post_init__(self) -> None:
        """Reject blank versions and duplicate mapping identities."""
        if not self.version.strip() or not self.mappings:
            msg = "XBRL mapping registry requires a version and mappings"
            raise XbrlDataError(msg)
        identities: set[tuple[str, str, str, str, tuple[XbrlSource, ...]]] = set()
        for mapping in self.mappings:
            identity = (
                mapping.issuer_id,
                mapping.metric_id,
                mapping.taxonomy,
                mapping.concept,
                mapping.eligible_sources,
            )
            if identity in identities:
                msg = f"duplicate XBRL concept mapping: {mapping.qualified_concept}"
                raise XbrlDataError(msg)
            identities.add(identity)

    @classmethod
    def from_yaml(cls, path: Path) -> XbrlMappingRegistry:
        """Load an exact, typed mapping registry from versioned YAML.

        Args:
            path: Versioned concept-mapping configuration file.

        Returns:
            Validated immutable mapping registry.

        Raises:
            XbrlDataError: If YAML or any semantic field is invalid.
        """
        try:
            with path.open(encoding="utf-8") as stream:
                loaded = yaml.safe_load(stream)
        except (OSError, yaml.YAMLError) as error:
            msg = f"XBRL mapping configuration is unavailable or invalid: {path.name}"
            raise XbrlDataError(msg) from error
        root = _as_mapping(loaded, location="root")
        version = _required_string(root, "mapping_version", location="root")
        raw_mappings = _as_sequence(root.get("mappings"), location="root.mappings")
        mappings = tuple(
            _mapping_from_payload(item, location=f"root.mappings[{index}]")
            for index, item in enumerate(raw_mappings)
        )
        return cls(version=version, mappings=mappings)

    def for_metric(self, *, issuer_id: str, metric_id: str) -> tuple[XbrlConceptMapping, ...]:
        """Return mappings for one issuer/metric pair in configured order."""
        return tuple(
            mapping
            for mapping in self.mappings
            if mapping.issuer_id == issuer_id and mapping.metric_id == metric_id
        )

    def for_fact(self, fact: XbrlFact) -> tuple[XbrlConceptMapping, ...]:
        """Return concept/source mappings potentially applicable to one fact."""
        return tuple(
            mapping
            for mapping in self.mappings
            if mapping.issuer_id == fact.issuer_id
            and mapping.taxonomy == fact.taxonomy
            and mapping.concept == fact.concept
            and fact.source in mapping.eligible_sources
        )


@dataclass(frozen=True, slots=True)
class MappedXbrlFact:
    """A structured fact whose complete semantics satisfy a mapping."""

    candidate_id: str
    mapping_version: str
    metric_id: str
    metric_version: str
    issuer_id: str
    reporting_entity_id: str
    reporting_scope_id: str
    normalized_value: Decimal
    unit: str
    reported_decimals: XbrlDecimals
    period_type: XbrlPeriodType
    period_start: date | None
    period_end: date
    dimensions: tuple[DimensionMember, ...]
    methodology: XbrlMethodology
    extraction_method: str
    evidence_id: str
    evidence_locator: str
    fact: XbrlFact

    def __post_init__(self) -> None:
        """Protect the mapped value from binary floating-point input."""
        _require_decimal(self.normalized_value, field_name="mapped XBRL value")


@dataclass(frozen=True, slots=True)
class MappingDecision:
    """Auditable outcome of applying one mapping to one XBRL fact."""

    disposition: DecisionDisposition
    fact: XbrlFact
    mapping: XbrlConceptMapping
    candidate: MappedXbrlFact | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        """Ensure validated and quarantined decisions cannot be confused."""
        if self.disposition is DecisionDisposition.VALIDATED:
            if self.candidate is None or self.reasons:
                msg = "validated XBRL mapping decisions require one clean candidate"
                raise XbrlDataError(msg)
        elif self.candidate is not None or not self.reasons:
            msg = "quarantined XBRL mapping decisions require reasons and no candidate"
            raise XbrlDataError(msg)


@dataclass(frozen=True, slots=True)
class ReconciliationValue:
    """One exact source value and its full reconciliation semantics."""

    issuer_id: str
    metric_id: str
    reporting_entity_id: str
    reporting_scope_id: str
    period_type: XbrlPeriodType
    period_start: date | None
    period_end: date
    unit: str
    value: Decimal
    methodology: XbrlMethodology
    evidence_id: str

    def __post_init__(self) -> None:
        """Require exact finite source values and nonblank semantics."""
        _require_decimal(self.value, field_name="reconciliation value")
        required = (
            self.issuer_id,
            self.metric_id,
            self.reporting_entity_id,
            self.reporting_scope_id,
            self.unit,
            self.evidence_id,
        )
        if any(not item.strip() for item in required):
            msg = "reconciliation semantics must not be blank"
            raise XbrlDataError(msg)
        if self.period_type is XbrlPeriodType.INSTANT and self.period_start is not None:
            msg = "instant reconciliation values cannot have a period start"
            raise XbrlDataError(msg)
        if self.period_type is XbrlPeriodType.DURATION and self.period_start is None:
            msg = "duration reconciliation values require a period start"
            raise XbrlDataError(msg)

    @classmethod
    def from_xbrl(cls, candidate: MappedXbrlFact) -> ReconciliationValue:
        """Build reconciliation input without losing mapped XBRL semantics."""
        return cls(
            issuer_id=candidate.issuer_id,
            metric_id=candidate.metric_id,
            reporting_entity_id=candidate.reporting_entity_id,
            reporting_scope_id=candidate.reporting_scope_id,
            period_type=candidate.period_type,
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            unit=candidate.unit,
            value=candidate.normalized_value,
            methodology=candidate.methodology,
            evidence_id=candidate.evidence_id,
        )


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """Exact two-source decision that never silently selects either value."""

    disposition: DecisionDisposition
    code: str
    reasons: tuple[str, ...]
    left: ReconciliationValue
    right: ReconciliationValue
    difference: Decimal | None

    def __post_init__(self) -> None:
        """Validate the audit shape and preserve Decimal-only differences."""
        if self.difference is not None:
            _require_decimal(self.difference, field_name="reconciliation difference")
        if not self.code.strip() or not self.reasons:
            msg = "reconciliation decisions require a stable code and reasons"
            raise XbrlDataError(msg)

    @property
    def quarantine_required(self) -> bool:
        """Return whether neither source may be silently preferred."""
        return self.disposition is DecisionDisposition.QUARANTINED


class SecCompanyFactsAdapter:
    """Parse retained SEC company-facts JSON without network access."""

    def parse(
        self,
        payload: bytes,
        *,
        issuer_id: str,
        evidence_id: str,
    ) -> tuple[XbrlFact, ...]:
        """Parse all numeric company facts into deterministic exact records.

        Args:
            payload: Exact bytes returned by the SEC company-facts endpoint.
            issuer_id: Stable configured company identifier.
            evidence_id: Immutable retained-evidence identifier.

        Returns:
            Facts ordered by taxonomy, concept, unit, and source occurrence.

        Raises:
            XbrlDataError: If the retained payload is malformed or inexact.
        """
        root = _load_exact_json(payload)
        cik = _normalize_cik(root.get("cik"), location="companyfacts.cik")
        facts = _as_mapping(root.get("facts"), location="companyfacts.facts")
        parsed: list[XbrlFact] = []
        for taxonomy in sorted(facts):
            concepts = _as_mapping(facts[taxonomy], location=f"facts.{taxonomy}")
            for concept in sorted(concepts):
                concept_payload = _as_mapping(
                    concepts[concept],
                    location=f"facts.{taxonomy}.{concept}",
                )
                units = _as_mapping(
                    concept_payload.get("units"),
                    location=f"facts.{taxonomy}.{concept}.units",
                )
                for unit in sorted(units):
                    occurrences = _as_sequence(
                        units[unit],
                        location=f"facts.{taxonomy}.{concept}.units.{unit}",
                    )
                    for index, occurrence in enumerate(occurrences):
                        location = f"facts.{taxonomy}.{concept}.units.{unit}[{index}]"
                        item = _as_mapping(occurrence, location=location)
                        parsed.append(
                            _company_fact_from_payload(
                                item,
                                issuer_id=issuer_id,
                                cik=cik,
                                taxonomy=taxonomy,
                                concept=concept,
                                unit=unit,
                                evidence_id=evidence_id,
                                occurrence=index,
                                location=location,
                            )
                        )
        return tuple(parsed)


class SecFilingXbrlAdapter:
    """Parse retained filing-level XBRL instance or inline-XBRL XML."""

    def parse(  # noqa: PLR0913 - filing identity is explicit evidence metadata.
        self,
        payload: bytes,
        *,
        issuer_id: str,
        evidence_id: str,
        accession: str,
        form: str,
        filed: date,
        qualified_concepts: frozenset[str] | None = None,
    ) -> tuple[XbrlFact, ...]:
        """Parse filing contexts, units, dimensions, and numeric facts.

        Args:
            payload: Retained XBRL instance or XML-compatible inline XBRL bytes.
            issuer_id: Stable configured company identifier.
            evidence_id: Immutable retained-evidence identifier.
            accession: SEC filing accession owning the document.
            form: Filing form such as ``10-Q`` or ``10-K``.
            filed: Official filing date.
            qualified_concepts: Optional exact taxonomy-qualified concept filter.
                When omitted, every numeric fact remains subject to fail-closed
                parsing. When supplied, unrelated facts are skipped before their
                numeric transformations are interpreted.

        Returns:
            Numeric facts in document order with exact context semantics.

        Raises:
            XbrlDataError: If XML, a context, a unit, or a number is ambiguous.
        """
        root, namespaces, fact_namespace_scopes = _load_xbrl_xml(payload)
        if qualified_concepts is not None and any(
            not concept.strip() or ":" not in concept for concept in qualified_concepts
        ):
            msg = "filing XBRL concept filters must be taxonomy-qualified"
            raise XbrlDataError(msg)
        numeric_elements = tuple(
            element
            for element in root.iter()
            if element.attrib.get("contextRef") is not None and _is_numeric_fact(element)
        )
        if qualified_concepts is None:
            selected_elements = numeric_elements
        else:
            selected_elements = tuple(
                element
                for element in numeric_elements
                if ":".join(_fact_concept(element, namespaces)) in qualified_concepts
            )
        selected_context_ids = frozenset(
            element.attrib["contextRef"] for element in selected_elements
        )
        selected_unit_ids = frozenset(
            element.attrib["unitRef"]
            for element in selected_elements
            if "unitRef" in element.attrib
        )
        contexts = _parse_contexts(
            root,
            selected_ids=None if qualified_concepts is None else selected_context_ids,
        )
        units = _parse_units(
            root,
            selected_ids=None if qualified_concepts is None else selected_unit_ids,
        )
        parsed: list[XbrlFact] = []
        for element in selected_elements:
            context_ref = element.attrib.get("contextRef")
            if context_ref is None:
                continue
            taxonomy, concept = _fact_concept(element, namespaces)
            if _is_explicitly_nil(element):
                continue
            context = contexts.get(context_ref)
            if context is None:
                msg = f"filing XBRL fact references an unknown context: {context_ref}"
                raise XbrlDataError(msg)
            unit_ref = element.attrib.get("unitRef")
            if unit_ref is None or unit_ref not in units:
                msg = f"filing XBRL numeric fact has an unresolved unit: {unit_ref}"
                raise XbrlDataError(msg)
            raw_value = " ".join("".join(element.itertext()).split())
            value = _parse_filing_numeric_value(
                element,
                raw_value=raw_value,
                namespace_scope=fact_namespace_scopes.get(id(element), {}),
            )
            if element.attrib.get("sign") == "-":
                value = -abs(value)
            scale_exponent = _optional_integer(element.attrib.get("scale"), field_name="scale")
            scale = Decimal(10) ** (scale_exponent or 0)
            value *= scale
            presentation_sign = (
                PresentationSign.NEGATIVE
                if value < 0
                else PresentationSign.ZERO
                if value == 0
                else PresentationSign.POSITIVE
            )
            decimals = _parse_decimals(element.attrib.get("decimals"))
            entity_identifier = context.entity_identifier
            cik = _normalize_cik(entity_identifier, location=f"context.{context_ref}.identifier")
            parsed.append(
                XbrlFact(
                    source=XbrlSource.SEC_FILING_XBRL,
                    issuer_id=issuer_id,
                    cik=cik,
                    taxonomy=taxonomy,
                    concept=concept,
                    raw_value=raw_value,
                    value=value,
                    unit=units[unit_ref],
                    scale=scale,
                    decimals=decimals,
                    period_type=context.period_type,
                    period_start=context.period_start,
                    period_end=context.period_end,
                    dimensions=context.dimensions,
                    entity_identifier=entity_identifier,
                    context_id=context_ref,
                    accession=accession,
                    form=form,
                    filed=filed,
                    evidence_id=evidence_id,
                    locator=(
                        f"xbrl:{taxonomy}:{concept};context={context_ref};"
                        f"unit={unit_ref};element_id={element.attrib.get('id', '')};"
                        f"occurrence={len(parsed)}"
                    ),
                    source_element_id=element.attrib.get("id"),
                    source_sign=element.attrib.get("sign"),
                    source_precision=element.attrib.get("precision"),
                    presentation_sign=presentation_sign,
                )
            )
        return tuple(parsed)


def apply_mapping(
    fact: XbrlFact,
    mapping: XbrlConceptMapping,
    *,
    mapping_version: str,
) -> MappingDecision:
    """Apply all concept, entity, unit, period, precision, and dimension rules.

    Args:
        fact: Exact parsed structured fact.
        mapping: Issuer-specific concept mapping.
        mapping_version: Version of the registry containing the mapping.

    Returns:
        Validated mapped candidate or a fail-closed quarantine decision.
    """
    reasons: list[str] = []
    if fact.issuer_id != mapping.issuer_id:
        reasons.append("ISSUER_MISMATCH")
    if fact.cik != mapping.cik:
        reasons.append("CIK_MISMATCH")
    if fact.taxonomy != mapping.taxonomy or fact.concept != mapping.concept:
        reasons.append("CONCEPT_MISMATCH")
    if fact.source not in mapping.eligible_sources:
        reasons.append("SOURCE_NOT_ELIGIBLE")
    if fact.unit != mapping.unit:
        reasons.append("UNIT_MISMATCH")
    if fact.period_type is not mapping.period_type:
        reasons.append("PERIOD_TYPE_MISMATCH")
    if tuple(sorted(fact.dimensions)) != tuple(sorted(mapping.dimensions)):
        reasons.append("DIMENSION_CONTEXT_MISMATCH")
    if fact.decimals is not None and fact.decimals != mapping.decimals:
        reasons.append("DECIMALS_MISMATCH")
    if reasons:
        return MappingDecision(
            disposition=DecisionDisposition.QUARANTINED,
            fact=fact,
            mapping=mapping,
            candidate=None,
            reasons=tuple(reasons),
        )
    methodology = _methodology_for_source(fact.source)
    candidate_material = "|".join(
        (
            mapping_version,
            fact.evidence_id,
            fact.locator,
            mapping.metric_id,
            str(fact.value),
        )
    )
    candidate = MappedXbrlFact(
        candidate_id=hashlib.sha256(candidate_material.encode()).hexdigest(),
        mapping_version=mapping_version,
        metric_id=mapping.metric_id,
        metric_version=mapping.metric_version,
        issuer_id=mapping.issuer_id,
        reporting_entity_id=mapping.reporting_entity_id,
        reporting_scope_id=mapping.reporting_scope_id,
        normalized_value=fact.value * mapping.scale,
        unit=mapping.unit,
        reported_decimals=fact.decimals if fact.decimals is not None else mapping.decimals,
        period_type=fact.period_type,
        period_start=fact.period_start,
        period_end=fact.period_end,
        dimensions=fact.dimensions,
        methodology=methodology,
        extraction_method=(
            "deterministic_sec_company_facts"
            if fact.source is XbrlSource.SEC_COMPANY_FACTS
            else "deterministic_sec_filing_xbrl"
        ),
        evidence_id=fact.evidence_id,
        evidence_locator=fact.locator,
        fact=fact,
    )
    return MappingDecision(
        disposition=DecisionDisposition.VALIDATED,
        fact=fact,
        mapping=mapping,
        candidate=candidate,
        reasons=(),
    )


def map_facts(
    facts: Sequence[XbrlFact],
    registry: XbrlMappingRegistry,
) -> tuple[MappingDecision, ...]:
    """Apply every exact concept/source mapping while ignoring unrelated facts.

    Args:
        facts: Parsed facts from one or more retained structured representations.
        registry: Versioned issuer-specific mapping registry.

    Returns:
        Decisions for facts whose issuer, concept, and source have a mapping.
    """
    return tuple(
        apply_mapping(fact, mapping, mapping_version=registry.version)
        for fact in facts
        for mapping in registry.for_fact(fact)
    )


def reconcile_values(
    left: ReconciliationValue,
    right: ReconciliationValue,
) -> ReconciliationDecision:
    """Reconcile two source values exactly without choosing a preferred source.

    Any semantic or numeric mismatch is quarantined. In particular, representation
    precedence is not a license to overwrite a conflicting exhibit or XBRL fact.

    Args:
        left: First exact source value.
        right: Second exact source value.

    Returns:
        Auditable exact-match validation or quarantine decision.
    """
    semantic_reasons: list[str] = []
    semantic_pairs = (
        (left.issuer_id, right.issuer_id, "ISSUER_MISMATCH"),
        (left.metric_id, right.metric_id, "METRIC_MISMATCH"),
        (left.reporting_entity_id, right.reporting_entity_id, "REPORTING_ENTITY_MISMATCH"),
        (left.reporting_scope_id, right.reporting_scope_id, "REPORTING_SCOPE_MISMATCH"),
        (left.period_type, right.period_type, "PERIOD_TYPE_MISMATCH"),
        (left.period_start, right.period_start, "PERIOD_START_MISMATCH"),
        (left.period_end, right.period_end, "PERIOD_END_MISMATCH"),
        (left.unit, right.unit, "UNIT_MISMATCH"),
    )
    semantic_reasons.extend(
        reason for left_value, right_value, reason in semantic_pairs if left_value != right_value
    )
    if semantic_reasons:
        return ReconciliationDecision(
            disposition=DecisionDisposition.QUARANTINED,
            code="RECONCILIATION_SEMANTICS_MISMATCH",
            reasons=tuple(semantic_reasons),
            left=left,
            right=right,
            difference=None,
        )
    difference = left.value - right.value
    if difference != Decimal(0):
        return ReconciliationDecision(
            disposition=DecisionDisposition.QUARANTINED,
            code="RECONCILIATION_VALUE_MISMATCH",
            reasons=("exact normalized source values differ",),
            left=left,
            right=right,
            difference=difference,
        )
    return ReconciliationDecision(
        disposition=DecisionDisposition.VALIDATED,
        code="RECONCILIATION_EXACT_MATCH",
        reasons=("exact normalized source values and semantics match",),
        left=left,
        right=right,
        difference=Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class _Context:
    entity_identifier: str
    period_type: XbrlPeriodType
    period_start: date | None
    period_end: date
    dimensions: tuple[DimensionMember, ...]


def _require_decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        msg = f"{field_name} must be a finite Decimal"
        raise TypeError(msg)
    return value


def _validate_decimals(value: XbrlDecimals) -> None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return
    if value == "INF":
        return
    msg = "XBRL decimals must be an integer, INF, or null"
    raise XbrlDataError(msg)


def _as_mapping(value: object, *, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        msg = f"{location} must be a string-keyed mapping"
        raise XbrlDataError(msg)
    return cast("Mapping[str, object]", value)


def _as_sequence(value: object, *, location: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        msg = f"{location} must be a sequence"
        raise XbrlDataError(msg)
    return cast("Sequence[object]", value)


def _required_string(payload: Mapping[str, object], key: str, *, location: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"{location}.{key} must be a nonblank string"
        raise XbrlDataError(msg)
    return value


def _parse_mapping_decimals(value: object, *, location: str) -> XbrlDecimals:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    if value == "INF":
        return "INF"
    msg = f"{location}.decimals must be an integer, INF, or null"
    raise XbrlDataError(msg)


def _mapping_from_payload(value: object, *, location: str) -> XbrlConceptMapping:
    payload = _as_mapping(value, location=location)
    scale_text = _required_string(payload, "scale", location=location)
    scale = _parse_decimal_text(scale_text, location=f"{location}.scale")
    raw_dimensions = _as_sequence(payload.get("dimensions"), location=f"{location}.dimensions")
    dimensions = tuple(
        DimensionMember(
            dimension=_required_string(
                _as_mapping(item, location=f"{location}.dimensions[{index}]"),
                "dimension",
                location=f"{location}.dimensions[{index}]",
            ),
            member=_required_string(
                _as_mapping(item, location=f"{location}.dimensions[{index}]"),
                "member",
                location=f"{location}.dimensions[{index}]",
            ),
        )
        for index, item in enumerate(raw_dimensions)
    )
    raw_sources = _as_sequence(
        payload.get("eligible_sources"),
        location=f"{location}.eligible_sources",
    )
    try:
        eligible_sources = tuple(XbrlSource(str(item)) for item in raw_sources)
        period_type = XbrlPeriodType(_required_string(payload, "period_type", location=location))
    except ValueError as error:
        msg = f"{location} contains an unsupported XBRL enum value"
        raise XbrlDataError(msg) from error
    return XbrlConceptMapping(
        issuer_id=_required_string(payload, "issuer_id", location=location),
        cik=_normalize_cik(payload.get("cik"), location=f"{location}.cik"),
        metric_id=_required_string(payload, "metric_id", location=location),
        metric_version=_required_string(payload, "metric_version", location=location),
        taxonomy=_required_string(payload, "taxonomy", location=location),
        concept=_required_string(payload, "concept", location=location),
        unit=_required_string(payload, "unit", location=location),
        scale=scale,
        decimals=_parse_mapping_decimals(payload.get("decimals"), location=location),
        period_type=period_type,
        dimensions=dimensions,
        reporting_entity_id=_required_string(
            payload,
            "reporting_entity_id",
            location=location,
        ),
        reporting_scope_id=_required_string(
            payload,
            "reporting_scope_id",
            location=location,
        ),
        eligible_sources=eligible_sources,
    )


def _load_exact_json(payload: bytes) -> Mapping[str, object]:
    try:
        decoded = payload.decode("utf-8")
        loaded = json.loads(decoded, parse_float=Decimal, parse_int=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "SEC company-facts payload is not valid UTF-8 JSON"
        raise XbrlDataError(msg) from error
    return _as_mapping(loaded, location="companyfacts")


def _normalize_cik(value: object, *, location: str) -> str:
    if isinstance(value, Decimal):
        if value != value.to_integral_value() or value < 0:
            msg = f"{location} must be a nonnegative integer CIK"
            raise XbrlDataError(msg)
        digits = str(value.to_integral_value())
    elif isinstance(value, str):
        digits = value.strip()
    else:
        msg = f"{location} must be a string or integer CIK"
        raise XbrlDataError(msg)
    if not digits.isdigit() or len(digits) > _MAX_CIK_DIGITS:
        msg = f"{location} is not a valid CIK"
        raise XbrlDataError(msg)
    return digits.zfill(10)


def _exact_decimal(value: object, *, location: str) -> tuple[str, Decimal]:
    if isinstance(value, Decimal):
        return str(value), _require_decimal(value, field_name=location)
    if isinstance(value, str):
        return value, _parse_decimal_text(value, location=location)
    msg = f"{location} must be an exact JSON number or numeric string"
    raise XbrlDataError(msg)


def _company_fact_from_payload(  # noqa: PLR0913 - preserves exact JSON-path provenance.
    payload: Mapping[str, object],
    *,
    issuer_id: str,
    cik: str,
    taxonomy: str,
    concept: str,
    unit: str,
    evidence_id: str,
    occurrence: int,
    location: str,
) -> XbrlFact:
    raw_value, value = _exact_decimal(payload.get("val"), location=f"{location}.val")
    end = _parse_date(payload.get("end"), location=f"{location}.end")
    start_value = payload.get("start")
    start = (
        _parse_date(start_value, location=f"{location}.start") if start_value is not None else None
    )
    period_type = XbrlPeriodType.DURATION if start is not None else XbrlPeriodType.INSTANT
    accession = _optional_string(payload.get("accn"))
    frame = _optional_string(payload.get("frame"))
    context_material = "|".join(
        (
            accession or "no-accession",
            start.isoformat() if start else "instant",
            end.isoformat(),
            frame or "no-frame",
            str(occurrence),
        )
    )
    context_id = f"companyfacts:{hashlib.sha256(context_material.encode()).hexdigest()[:24]}"
    return XbrlFact(
        source=XbrlSource.SEC_COMPANY_FACTS,
        issuer_id=issuer_id,
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        raw_value=raw_value,
        value=value,
        unit=unit,
        scale=Decimal(1),
        decimals=_parse_decimals(payload.get("decimals")),
        period_type=period_type,
        period_start=start,
        period_end=end,
        dimensions=(),
        entity_identifier=cik,
        context_id=context_id,
        accession=accession,
        form=_optional_string(payload.get("form")),
        filed=(
            _parse_date(payload.get("filed"), location=f"{location}.filed")
            if payload.get("filed") is not None
            else None
        ),
        evidence_id=evidence_id,
        locator=(
            f"companyfacts:{taxonomy}:{concept};unit={unit};"
            f"accession={accession or 'none'};occurrence={occurrence}"
        ),
    )


def _parse_date(value: object, *, location: str) -> date:
    if not isinstance(value, str):
        msg = f"{location} must be an ISO date"
        raise XbrlDataError(msg)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        msg = f"{location} must be an ISO date"
        raise XbrlDataError(msg) from error


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = "optional XBRL metadata must be text"
        raise XbrlDataError(msg)
    return value or None


def _parse_decimals(value: object) -> XbrlDecimals:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        msg = "XBRL decimals must be integral"
        raise XbrlDataError(msg)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.upper() == "INF":
            return "INF"
        try:
            return int(value)
        except ValueError as error:
            msg = "XBRL decimals must be an integer or INF"
            raise XbrlDataError(msg) from error
    msg = "XBRL decimals must be an integer, INF, or null"
    raise XbrlDataError(msg)


def _parse_decimal_text(value: str, *, location: str) -> Decimal:
    cleaned = value.strip().replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation as error:
        msg = f"{location} is not an exact decimal"
        raise XbrlDataError(msg) from error
    if not parsed.is_finite():
        msg = f"{location} must be finite"
        raise XbrlDataError(msg)
    return -parsed if negative else parsed


def _optional_integer(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"filing XBRL {field_name} must be an integer string"
        raise XbrlDataError(msg)
    try:
        return int(value)
    except ValueError as error:
        msg = f"filing XBRL {field_name} must be an integer string"
        raise XbrlDataError(msg) from error


def _load_xbrl_xml(
    payload: bytes,
) -> tuple[ET.Element, Mapping[str, str], Mapping[int, Mapping[str, str]]]:
    if len(payload) > _MAX_XML_BYTES:
        msg = "filing XBRL payload exceeds the deterministic parser size limit"
        raise XbrlDataError(msg)
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        msg = "filing XBRL must not contain a DTD or entity declaration"
        raise XbrlDataError(msg)
    try:
        root, namespaces, fact_namespace_scopes = _parse_xbrl_tree_and_namespaces(payload)
    except ET.ParseError as error:
        msg = "filing XBRL payload is not well-formed XML"
        raise XbrlDataError(msg) from error
    return root, namespaces, fact_namespace_scopes


def _parse_xbrl_tree_and_namespaces(
    payload: bytes,
) -> tuple[ET.Element, dict[str, str], dict[int, Mapping[str, str]]]:
    namespaces: dict[str, str] = {}
    fact_namespace_scopes: dict[int, Mapping[str, str]] = {}
    scope_stack: list[dict[str, str]] = []
    pending_namespaces: list[tuple[str, str]] = []
    root: ET.Element | None = None
    parser = ET.iterparse(  # noqa: S314 - DTD/entities rejected by caller.
        BytesIO(payload),
        events=("start", "end", "start-ns"),
    )
    for event, raw_item in parser:
        if event == "start-ns":
            prefix, uri = cast("tuple[str, str]", raw_item)
            pending_namespaces.append((prefix, uri))
            if uri not in namespaces or prefix:
                namespaces[uri] = prefix
        elif event == "start":
            element = raw_item
            root = element if root is None else root
            scope = dict(scope_stack[-1]) if scope_stack else {}
            scope.update(pending_namespaces)
            pending_namespaces.clear()
            scope_stack.append(scope)
            if "format" in element.attrib:
                fact_namespace_scopes[id(element)] = scope
        else:
            scope_stack.pop()
    if root is None:
        msg = "filing XBRL payload has no document element"
        raise XbrlDataError(msg)
    return root, namespaces, fact_namespace_scopes


def _expanded_name(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, _, local_name = tag[1:].partition("}")
        return namespace, local_name
    return "", tag


def _parse_contexts(
    root: ET.Element,
    *,
    selected_ids: frozenset[str] | None = None,
) -> dict[str, _Context]:
    contexts: dict[str, _Context] = {}
    context_tag = f"{{{_XBRLI_NAMESPACE}}}context"
    identifier_tag = f"{{{_XBRLI_NAMESPACE}}}identifier"
    instant_tag = f"{{{_XBRLI_NAMESPACE}}}instant"
    start_tag = f"{{{_XBRLI_NAMESPACE}}}startDate"
    end_tag = f"{{{_XBRLI_NAMESPACE}}}endDate"
    explicit_member_tag = f"{{{_XBRLDI_NAMESPACE}}}explicitMember"
    typed_member_tag = f"{{{_XBRLDI_NAMESPACE}}}typedMember"
    for element in root.iter(context_tag):
        context_id = element.attrib.get("id", "")
        if selected_ids is not None and context_id not in selected_ids:
            continue
        identifier = element.find(f".//{identifier_tag}")
        if not context_id or identifier is None or identifier.text is None:
            msg = "filing XBRL context is missing its ID or entity identifier"
            raise XbrlDataError(msg)
        instant = element.find(f".//{instant_tag}")
        if instant is not None and instant.text:
            period_type = XbrlPeriodType.INSTANT
            period_start = None
            period_end = _parse_date(instant.text.strip(), location=f"context.{context_id}.instant")
        else:
            start_element = element.find(f".//{start_tag}")
            end_element = element.find(f".//{end_tag}")
            if (
                start_element is None
                or start_element.text is None
                or end_element is None
                or end_element.text is None
            ):
                msg = f"filing XBRL context has an unresolved period: {context_id}"
                raise XbrlDataError(msg)
            period_type = XbrlPeriodType.DURATION
            period_start = _parse_date(
                start_element.text.strip(),
                location=f"context.{context_id}.startDate",
            )
            period_end = _parse_date(
                end_element.text.strip(),
                location=f"context.{context_id}.endDate",
            )
        dimensions: list[DimensionMember] = []
        if next(element.iter(typed_member_tag), None) is not None:
            msg = f"filing XBRL typed dimension is unsupported: {context_id}"
            raise XbrlDataError(msg)
        for member in element.iter(explicit_member_tag):
            if member.text is None:
                msg = f"filing XBRL context has a blank explicit member: {context_id}"
                raise XbrlDataError(msg)
            dimensions.append(
                DimensionMember(
                    dimension=member.attrib.get("dimension", ""),
                    member=member.text.strip(),
                )
            )
        if context_id in contexts:
            msg = f"filing XBRL repeats a context ID: {context_id}"
            raise XbrlDataError(msg)
        contexts[context_id] = _Context(
            entity_identifier=identifier.text.strip(),
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            dimensions=tuple(sorted(dimensions)),
        )
    return contexts


def _parse_units(
    root: ET.Element,
    *,
    selected_ids: frozenset[str] | None = None,
) -> dict[str, str]:
    units: dict[str, str] = {}
    unit_tag = f"{{{_XBRLI_NAMESPACE}}}unit"
    measure_tag = f"{{{_XBRLI_NAMESPACE}}}measure"
    divide_tag = f"{{{_XBRLI_NAMESPACE}}}divide"
    numerator_tag = f"{{{_XBRLI_NAMESPACE}}}unitNumerator"
    denominator_tag = f"{{{_XBRLI_NAMESPACE}}}unitDenominator"
    for element in root.iter(unit_tag):
        unit_id = element.attrib.get("id", "")
        if selected_ids is not None and unit_id not in selected_ids:
            continue
        divide = element.find(divide_tag)
        if divide is None:
            measures = [_unit_measure(item.text) for item in element.findall(measure_tag)]
            unit = "*".join(measures)
        else:
            numerator = divide.find(numerator_tag)
            denominator = divide.find(denominator_tag)
            if numerator is None or denominator is None:
                msg = f"filing XBRL divide unit is incomplete: {unit_id}"
                raise XbrlDataError(msg)
            numerator_measures = [
                _unit_measure(item.text) for item in numerator.findall(measure_tag)
            ]
            denominator_measures = [
                _unit_measure(item.text) for item in denominator.findall(measure_tag)
            ]
            unit = f"{'*'.join(numerator_measures)}/{'*'.join(denominator_measures)}"
        if not unit_id or not unit:
            msg = "filing XBRL unit is missing its ID or measure"
            raise XbrlDataError(msg)
        if unit_id in units:
            msg = f"filing XBRL repeats a unit ID: {unit_id}"
            raise XbrlDataError(msg)
        units[unit_id] = unit
    return units


def _unit_measure(value: str | None) -> str:
    if value is None or not value.strip():
        msg = "filing XBRL unit measure is blank"
        raise XbrlDataError(msg)
    qualified = value.strip()
    prefix, separator, local_name = qualified.partition(":")
    if separator and prefix in {"iso4217", "xbrli"}:
        return local_name
    return qualified


def _is_numeric_fact(element: ET.Element) -> bool:
    namespace, local_name = _expanded_name(element.tag)
    if namespace in _INLINE_XBRL_NAMESPACES:
        return local_name == "nonFraction"
    return namespace not in _RESERVED_FACT_NAMESPACES and "unitRef" in element.attrib


def _is_explicitly_nil(element: ET.Element) -> bool:
    nil_value = element.attrib.get(f"{{{_XSI_NAMESPACE}}}nil")
    if nil_value is None:
        return False
    if nil_value in {"true", "1"}:
        return True
    if nil_value in {"false", "0"}:
        return False
    msg = "filing XBRL xsi:nil must be true, false, 1, or 0"
    raise XbrlDataError(msg)


def _parse_filing_numeric_value(
    element: ET.Element,
    *,
    raw_value: str,
    namespace_scope: Mapping[str, str],
) -> Decimal:
    format_name = element.attrib.get("format")
    if format_name is None:
        return _parse_decimal_text(raw_value, location="filing XBRL fact")
    prefix, separator, local_name = format_name.partition(":")
    if not separator or not prefix or not local_name:
        msg = "inline XBRL numeric fact has an invalid transformation format"
        raise XbrlDataError(msg)
    transformation_namespace = namespace_scope.get(prefix)
    if transformation_namespace not in _INLINE_TRANSFORMATION_NAMESPACES:
        msg = f"inline XBRL numeric fact uses an unsupported transformation: {format_name}"
        raise XbrlDataError(msg)
    if local_name == "fixed-zero" and (
        transformation_namespace != _LEGACY_INLINE_TRANSFORMATION_NAMESPACE
    ):
        return Decimal(0)
    if (
        local_name == "num-dot-decimal"
        and transformation_namespace != _LEGACY_INLINE_TRANSFORMATION_NAMESPACE
    ) or (
        local_name == "numdotdecimal"
        and transformation_namespace == _LEGACY_INLINE_TRANSFORMATION_NAMESPACE
    ):
        normalized = raw_value.strip()
        if _NUM_DOT_DECIMAL_PATTERN.fullmatch(normalized) is None:
            msg = "filing XBRL fact is not a valid num-dot-decimal value"
            raise XbrlDataError(msg)
        return Decimal(normalized.replace(",", ""))
    msg = f"inline XBRL numeric fact uses an unsupported transformation: {format_name}"
    raise XbrlDataError(msg)


def _fact_concept(
    element: ET.Element,
    namespaces: Mapping[str, str],
) -> tuple[str, str]:
    namespace, local_name = _expanded_name(element.tag)
    if namespace in _INLINE_XBRL_NAMESPACES:
        name = element.attrib.get("name", "")
        taxonomy, separator, concept = name.partition(":")
        if not separator or not taxonomy or not concept:
            msg = "inline XBRL numeric fact has an invalid concept name"
            raise XbrlDataError(msg)
        return taxonomy, concept
    taxonomy = namespaces.get(namespace, "")
    if not taxonomy:
        msg = f"filing XBRL concept namespace has no prefix: {local_name}"
        raise XbrlDataError(msg)
    return taxonomy, local_name


def _methodology_for_source(source: XbrlSource) -> XbrlMethodology:
    if source is XbrlSource.SEC_COMPANY_FACTS:
        return XbrlMethodology.SEC_COMPANY_FACTS_XBRL
    return XbrlMethodology.SEC_FILING_XBRL
