"""Pure, non-persisting discovery for selected filing-specific financial facts.

The functions in this module consume typed values returned by the public
``edgartools_adapter`` or retained document bytes. They do not acquire data,
write evidence, persist an observation, or approve a golden expectation. The
raw-document route converts source display strings directly to exact ``Decimal``
values while retaining both representations and their exact source locators.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

from mortgage_servicing_dashboard.domain import PublicationState
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    XbrlDimension,
    XbrlFact,
    XbrlFiling,
)
from mortgage_servicing_dashboard.xbrl import (
    DimensionMember,
    PresentationSign,
    SecFilingXbrlAdapter,
    XbrlConceptMapping,
    XbrlMappingRegistry,
    XbrlPeriodType,
    XbrlSource,
)
from mortgage_servicing_dashboard.xbrl import (
    XbrlFact as ParsedXbrlFact,
)


class FinancialDiscoveryError(ValueError):
    """A selected-field config or discovery input is not deterministic."""


class FinancialClassification(StrEnum):
    """Allowed Phase 2 field classifications."""

    CORE_FINANCIAL = "CORE_FINANCIAL"
    OPTIONAL_SERVICING = "OPTIONAL_SERVICING"


class SelectionDecision(StrEnum):
    """Whether a reviewed mapping is part of the compact selected set."""

    SELECTED = "SELECTED"
    EXCLUDED = "EXCLUDED"


class ReviewStatus(StrEnum):
    """Independent status of a proposed golden expectation or mapping."""

    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INDEPENDENTLY_CROSS_CHECKED = "INDEPENDENTLY_CROSS_CHECKED"
    REVIEWER_APPROVED = "REVIEWER_APPROVED"


class SourceRoute(StrEnum):
    """Eligible raw publication-source routes represented in this discovery."""

    SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS = "SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS"


class ReportingScopeCategory(StrEnum):
    """Additive scope categories permitted by the compact financial mapping."""

    CONSOLIDATED_COMPANY = "CONSOLIDATED_COMPANY"


class Directness(StrEnum):
    """Whether a discovered field is reported or locally derived."""

    DIRECT_REPORTED = "DIRECT_REPORTED"


class AvailabilityStatus(StrEnum):
    """Non-publication status for one selected field in one filing."""

    AVAILABLE = "AVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class SelectedFieldMapping:
    """One issuer-specific selected field plus its XBRL matching contract."""

    xbrl: XbrlConceptMapping
    display_name: str
    classification: FinancialClassification
    selection_decision: SelectionDecision
    selection_reason: str
    source_route: SourceRoute
    currency: str
    reporting_scope_name: str
    reporting_scope_category: ReportingScopeCategory
    portfolio_population: str
    scope_methodology: str
    eligible_forms: tuple[str, ...]
    directness: Directness
    raw_string_to_decimal: str
    sign_convention: str
    validation_behavior: str
    amendment_behavior: str
    publication_state: PublicationState
    review_status: ReviewStatus

    def __post_init__(self) -> None:
        """Reject incomplete selection semantics and unsafe publication states."""
        required = (
            self.display_name,
            self.selection_reason,
            self.currency,
            self.reporting_scope_name,
            self.reporting_scope_category,
            self.portfolio_population,
            self.scope_methodology,
            self.raw_string_to_decimal,
            self.sign_convention,
            self.validation_behavior,
            self.amendment_behavior,
        )
        if any(not value.strip() for value in required):
            message = "selected financial-field semantics must not be blank"
            raise FinancialDiscoveryError(message)
        if not self.eligible_forms or any(not form.strip() for form in self.eligible_forms):
            message = "selected financial fields require explicit eligible forms"
            raise FinancialDiscoveryError(message)
        if self.portfolio_population != "consolidated_sec_registrant":
            message = "selected core financial scope must identify the consolidated SEC registrant"
            raise FinancialDiscoveryError(message)
        if self.selection_decision is SelectionDecision.SELECTED:
            if self.publication_state is not PublicationState.CANDIDATE:
                message = "selected discovery mappings must remain CANDIDATE"
                raise FinancialDiscoveryError(message)
            if self.review_status is not ReviewStatus.REVIEW_REQUIRED:
                message = "selected discovery mappings require independent review"
                raise FinancialDiscoveryError(message)

    @property
    def field_id(self) -> str:
        """Return the stable selected financial-field identifier."""
        return self.xbrl.metric_id

    @property
    def issuer_id(self) -> str:
        """Return the configured issuer identifier."""
        return self.xbrl.issuer_id


@dataclass(frozen=True, slots=True)
class FinancialFieldRegistry:
    """Immutable compact selected-field configuration."""

    version: str
    mappings: tuple[SelectedFieldMapping, ...]

    def __post_init__(self) -> None:
        """Reject an empty or multiply defined issuer/field selection."""
        if not self.version.strip() or not self.mappings:
            message = "financial-field registry requires a version and mappings"
            raise FinancialDiscoveryError(message)
        identities = {(item.issuer_id, item.field_id) for item in self.mappings}
        if len(identities) != len(self.mappings):
            message = "financial-field registry repeats an issuer/field mapping"
            raise FinancialDiscoveryError(message)

    @classmethod
    def from_yaml(cls, path: Path) -> FinancialFieldRegistry:
        """Load selection metadata while reusing the existing XBRL mapping parser.

        Args:
            path: Compact selected-field YAML file.

        Returns:
            Validated immutable financial-field registry.

        Raises:
            FinancialDiscoveryError: If selection metadata is malformed.
            XbrlDataError: If the existing XBRL mapping contract is malformed.
        """
        xbrl_registry = XbrlMappingRegistry.from_yaml(path)
        try:
            with path.open(encoding="utf-8") as stream:
                loaded = yaml.safe_load(stream)
        except (OSError, yaml.YAMLError) as error:
            message = f"financial-field configuration is unavailable or invalid: {path.name}"
            raise FinancialDiscoveryError(message) from error
        root = _as_mapping(loaded, location="root")
        raw_mappings = _as_sequence(root.get("mappings"), location="root.mappings")
        if len(raw_mappings) != len(xbrl_registry.mappings):
            message = "financial and XBRL mapping counts disagree"
            raise FinancialDiscoveryError(message)
        mappings = tuple(
            _selected_mapping_from_payload(
                value,
                xbrl=xbrl_registry.mappings[index],
                location=f"root.mappings[{index}]",
            )
            for index, value in enumerate(raw_mappings)
        )
        return cls(version=xbrl_registry.version, mappings=mappings)

    def for_filing(self, *, cik: str, form: str) -> tuple[SelectedFieldMapping, ...]:
        """Return selected mappings eligible for an exact filing identity."""
        return tuple(
            mapping
            for mapping in self.mappings
            if mapping.xbrl.cik == cik
            and form in mapping.eligible_forms
            and mapping.selection_decision is SelectionDecision.SELECTED
        )


@dataclass(frozen=True, slots=True)
class ProposedFactLocator:
    """Exact raw-fact locator proposed for independent golden review."""

    accession_number: str
    source_document: str
    source_url: str
    qualified_concept: str
    original_labels: tuple[str, ...]
    raw_value: str
    context_ref: str
    entity_identifier: str | None
    period_type: str | None
    period_start: str | None
    period_end: str | None
    period_instant: str | None
    dimensions: tuple[XbrlDimension, ...]
    unit_ref: str | None
    unit_measure: str | None
    decimals: str | None
    scale: str | None
    precision: str | None
    fact_ids: tuple[str, ...]
    instance_ids: tuple[str, ...]
    source_object_count: int
    review_status: ReviewStatus = ReviewStatus.REVIEW_REQUIRED

    def __post_init__(self) -> None:
        """Require complete raw lineage without approving its financial meaning."""
        required = (
            self.accession_number,
            self.source_document,
            self.source_url,
            self.qualified_concept,
            self.raw_value,
            self.context_ref,
        )
        if any(not value.strip() for value in required):
            message = "proposed fact locators require exact nonblank lineage"
            raise FinancialDiscoveryError(message)
        if self.source_object_count < 1:
            message = "proposed fact locators require at least one source object"
            raise FinancialDiscoveryError(message)
        if self.review_status is not ReviewStatus.REVIEW_REQUIRED:
            message = "generated fact locators cannot approve their own expectation"
            raise FinancialDiscoveryError(message)


@dataclass(frozen=True, slots=True)
class FieldDiscovery:
    """All proposed locators for one selected field in one filing."""

    mapping: SelectedFieldMapping
    accession_number: str
    form: str
    candidates: tuple[ProposedFactLocator, ...]
    status: AvailabilityStatus
    ambiguities: tuple[str, ...]

    def __post_init__(self) -> None:
        """Keep discovery status consistent with the candidate set."""
        if self.status is AvailabilityStatus.NOT_FOUND and self.candidates:
            message = "NOT_FOUND discovery cannot contain candidates"
            raise FinancialDiscoveryError(message)
        if self.status is AvailabilityStatus.AVAILABLE and (
            not self.candidates or self.ambiguities
        ):
            message = "AVAILABLE discovery requires candidates and no ambiguity"
            raise FinancialDiscoveryError(message)
        if self.status is AvailabilityStatus.AMBIGUOUS and (
            not self.candidates or not self.ambiguities
        ):
            message = "AMBIGUOUS discovery requires candidates and reasons"
            raise FinancialDiscoveryError(message)


@dataclass(frozen=True, slots=True)
class RawFilingFactLocator:
    """One coalesced exact fact parsed from retained inline-XBRL bytes."""

    accession_number: str
    source_document: str
    source_url: str
    evidence_id: str
    qualified_concept: str
    source_element_ids: tuple[str, ...]
    raw_value: str
    normalized_value: Decimal
    context_ref: str
    entity_identifier: str
    period_type: XbrlPeriodType
    period_start: date | None
    period_end: date
    dimensions: tuple[DimensionMember, ...]
    unit: str
    decimals: int | str | None
    scale: Decimal
    source_locators: tuple[str, ...]
    source_object_count: int
    source_sign: str | None = None
    source_precision: str | None = None
    presentation_sign: PresentationSign = PresentationSign.POSITIVE
    review_status: ReviewStatus = ReviewStatus.REVIEW_REQUIRED

    def __post_init__(self) -> None:
        """Require separate exact source and normalized numeric representations."""
        required = (
            self.accession_number,
            self.source_document,
            self.source_url,
            self.evidence_id,
            self.qualified_concept,
            self.raw_value,
            self.context_ref,
            self.entity_identifier,
            self.unit,
        )
        if any(not value.strip() for value in required):
            message = "raw filing fact locators require exact nonblank lineage"
            raise FinancialDiscoveryError(message)
        if not isinstance(self.normalized_value, Decimal) or not self.normalized_value.is_finite():
            message = "raw filing normalized values must be finite Decimal instances"
            raise FinancialDiscoveryError(message)
        if not isinstance(self.scale, Decimal) or not self.scale.is_finite() or self.scale <= 0:
            message = "raw filing fact scales must be positive finite Decimal instances"
            raise FinancialDiscoveryError(message)
        if self.source_object_count < 1 or self.source_object_count != len(self.source_locators):
            message = "raw filing fact locators require complete source-object lineage"
            raise FinancialDiscoveryError(message)
        if self.review_status is not ReviewStatus.REVIEW_REQUIRED:
            message = "generated raw filing locators cannot approve their own expectation"
            raise FinancialDiscoveryError(message)


@dataclass(frozen=True, slots=True)
class RawFieldDiscovery:
    """All deterministic raw-document candidates for one selected field."""

    mapping: SelectedFieldMapping
    accession_number: str
    form: str
    candidates: tuple[RawFilingFactLocator, ...]
    status: AvailabilityStatus
    ambiguities: tuple[str, ...]

    def __post_init__(self) -> None:
        """Keep raw-document discovery status consistent with its candidates."""
        if self.status is AvailabilityStatus.NOT_FOUND and self.candidates:
            message = "NOT_FOUND raw discovery cannot contain candidates"
            raise FinancialDiscoveryError(message)
        if self.status is AvailabilityStatus.AVAILABLE and (
            not self.candidates or self.ambiguities
        ):
            message = "AVAILABLE raw discovery requires candidates and no ambiguity"
            raise FinancialDiscoveryError(message)
        if self.status is AvailabilityStatus.AMBIGUOUS and (
            not self.candidates or not self.ambiguities
        ):
            message = "AMBIGUOUS raw discovery requires candidates and reasons"
            raise FinancialDiscoveryError(message)


def discover_retained_document_fields(  # noqa: PLR0913 - exact source identity is required.
    payload: bytes,
    *,
    issuer_id: str,
    cik: str,
    evidence_id: str,
    accession_number: str,
    source_document: str,
    source_url: str,
    form: str,
    filed: date,
    registry: FinancialFieldRegistry,
) -> tuple[RawFieldDiscovery, ...]:
    """Parse only mapped concepts from exact retained inline-XBRL document bytes.

    This pure seam performs no acquisition, retention, persistence, publication,
    or fallback. Unsupported numeric transformations on a selected concept fail
    closed; transformations on unrelated concepts are outside the requested parse.
    """
    mappings = registry.for_filing(cik=cik, form=form)
    if any(mapping.issuer_id != issuer_id for mapping in mappings):
        message = "raw filing discovery issuer does not match the selected mappings"
        raise FinancialDiscoveryError(message)
    qualified_concepts = frozenset(mapping.xbrl.qualified_concept for mapping in mappings)
    facts = SecFilingXbrlAdapter().parse(
        payload,
        issuer_id=issuer_id,
        evidence_id=evidence_id,
        accession=accession_number,
        form=form,
        filed=filed,
        qualified_concepts=qualified_concepts,
    )
    return discover_parsed_filing_fields(
        facts,
        cik=cik,
        evidence_id=evidence_id,
        accession_number=accession_number,
        source_document=source_document,
        source_url=source_url,
        form=form,
        registry=registry,
    )


def discover_parsed_filing_fields(  # noqa: PLR0913 - exact source identity is required.
    facts: Sequence[ParsedXbrlFact],
    *,
    cik: str,
    evidence_id: str,
    accession_number: str,
    source_document: str,
    source_url: str,
    form: str,
    registry: FinancialFieldRegistry,
) -> tuple[RawFieldDiscovery, ...]:
    """Discover fields from existing exact parser facts without any side effect."""
    results: list[RawFieldDiscovery] = []
    for mapping in registry.for_filing(cik=cik, form=form):
        matching = tuple(fact for fact in facts if _parsed_fact_matches(fact, mapping))
        candidates = _coalesce_parsed_facts(
            matching,
            evidence_id=evidence_id,
            accession_number=accession_number,
            source_document=source_document,
            source_url=source_url,
        )
        ambiguities = _detect_raw_ambiguities(candidates)
        if not candidates:
            status = AvailabilityStatus.NOT_FOUND
        elif ambiguities:
            status = AvailabilityStatus.AMBIGUOUS
        else:
            status = AvailabilityStatus.AVAILABLE
        results.append(
            RawFieldDiscovery(
                mapping=mapping,
                accession_number=accession_number,
                form=form,
                candidates=candidates,
                status=status,
                ambiguities=ambiguities,
            )
        )
    return tuple(results)


def _parsed_fact_matches(fact: ParsedXbrlFact, mapping: SelectedFieldMapping) -> bool:
    expected = mapping.xbrl
    return (
        fact.source is XbrlSource.SEC_FILING_XBRL
        and fact.issuer_id == mapping.issuer_id
        and fact.cik == expected.cik
        and fact.taxonomy == expected.taxonomy
        and fact.concept == expected.concept
        and fact.unit == expected.unit
        and fact.decimals == expected.decimals
        and fact.period_type is expected.period_type
        and tuple(sorted(fact.dimensions)) == tuple(sorted(expected.dimensions))
    )


def _coalesce_parsed_facts(
    facts: tuple[ParsedXbrlFact, ...],
    *,
    evidence_id: str,
    accession_number: str,
    source_document: str,
    source_url: str,
) -> tuple[RawFilingFactLocator, ...]:
    grouped: dict[tuple[object, ...], list[ParsedXbrlFact]] = {}
    for fact in facts:
        key: tuple[object, ...] = (
            fact.qualified_concept,
            fact.raw_value,
            fact.value,
            fact.context_id,
            fact.entity_identifier,
            fact.period_type,
            fact.period_start,
            fact.period_end,
            fact.dimensions,
            fact.unit,
            fact.decimals,
            fact.scale,
            fact.source_sign,
            fact.source_precision,
            fact.presentation_sign,
        )
        grouped.setdefault(key, []).append(fact)
    candidates: list[RawFilingFactLocator] = []
    for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
        source_facts = grouped[key]
        first = source_facts[0]
        candidates.append(
            RawFilingFactLocator(
                accession_number=accession_number,
                source_document=source_document,
                source_url=source_url,
                evidence_id=evidence_id,
                qualified_concept=first.qualified_concept,
                source_element_ids=tuple(
                    sorted(
                        {
                            source_fact.source_element_id
                            for source_fact in source_facts
                            if source_fact.source_element_id is not None
                        }
                    )
                ),
                raw_value=first.raw_value,
                normalized_value=first.value,
                context_ref=first.context_id,
                entity_identifier=first.entity_identifier,
                period_type=first.period_type,
                period_start=first.period_start,
                period_end=first.period_end,
                dimensions=first.dimensions,
                unit=first.unit,
                decimals=first.decimals,
                scale=first.scale,
                source_locators=tuple(source_fact.locator for source_fact in source_facts),
                source_object_count=len(source_facts),
                source_sign=first.source_sign,
                source_precision=first.source_precision,
                presentation_sign=first.presentation_sign,
            )
        )
    return tuple(candidates)


def _detect_raw_ambiguities(
    candidates: tuple[RawFilingFactLocator, ...],
) -> tuple[str, ...]:
    values_by_period: dict[tuple[object, ...], set[tuple[object, ...]]] = {}
    for candidate in candidates:
        semantic_period = (
            candidate.entity_identifier,
            candidate.period_type,
            candidate.period_start,
            candidate.period_end,
            candidate.dimensions,
            candidate.unit,
        )
        values_by_period.setdefault(semantic_period, set()).add(
            (
                candidate.raw_value,
                candidate.normalized_value,
                candidate.scale,
                candidate.decimals,
            )
        )
    if any(len(values) > 1 for values in values_by_period.values()):
        return ("CONFLICTING_RAW_VALUES_FOR_EXACT_SEMANTIC_PERIOD",)
    return ()


def discover_filing_fields(
    filing: XbrlFiling,
    *,
    form: str,
    registry: FinancialFieldRegistry,
) -> tuple[FieldDiscovery, ...]:
    """Propose exact raw-fact locators without selecting or normalizing a value.

    Args:
        filing: Filing-specific raw XBRL returned by the public adapter.
        form: Exact SEC form associated with ``filing``.
        registry: Compact selected-field mappings.

    Returns:
        One deterministic result for every selected mapping eligible for the filing.
    """
    if not form.strip():
        message = "financial discovery requires an exact filing form"
        raise FinancialDiscoveryError(message)
    results: list[FieldDiscovery] = []
    for mapping in registry.for_filing(cik=filing.cik, form=form):
        facts = tuple(fact for fact in filing.facts if _fact_matches(fact, mapping))
        candidates = _coalesce_source_objects(filing, facts)
        ambiguities = _detect_ambiguities(candidates)
        if not candidates:
            status = AvailabilityStatus.NOT_FOUND
        elif ambiguities:
            status = AvailabilityStatus.AMBIGUOUS
        else:
            status = AvailabilityStatus.AVAILABLE
        results.append(
            FieldDiscovery(
                mapping=mapping,
                accession_number=filing.accession_number,
                form=form,
                candidates=candidates,
                status=status,
                ambiguities=ambiguities,
            )
        )
    return tuple(results)


def _fact_matches(fact: XbrlFact, mapping: SelectedFieldMapping) -> bool:
    expected = mapping.xbrl
    fact_dimensions = tuple(
        DimensionMember(dimension=item.axis, member=item.member) for item in fact.dimensions
    )
    decimals = None if expected.decimals is None else str(expected.decimals)
    return (
        fact.taxonomy == expected.taxonomy
        and fact.concept == expected.concept
        and fact.context.period_type == expected.period_type.value
        and tuple(sorted(fact_dimensions)) == tuple(sorted(expected.dimensions))
        and fact.unit is not None
        and _canonical_unit_measure(fact.unit.measure) == expected.unit
        and fact.decimals == decimals
    )


def _canonical_unit_measure(value: str | None) -> str | None:
    if value is None:
        return None
    prefix, separator, local_name = value.partition(":")
    if separator and prefix in {"iso4217", "xbrli"}:
        return local_name
    return value


def _coalesce_source_objects(
    filing: XbrlFiling,
    facts: tuple[XbrlFact, ...],
) -> tuple[ProposedFactLocator, ...]:
    grouped: dict[tuple[object, ...], list[XbrlFact]] = {}
    for fact in facts:
        grouped.setdefault(_semantic_fact_key(fact), []).append(fact)
    return tuple(
        _proposed_locator(filing, grouped[key])
        for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item))
    )


def _semantic_fact_key(fact: XbrlFact) -> tuple[object, ...]:
    context = fact.context
    unit = fact.unit
    return (
        fact.taxonomy,
        fact.concept,
        fact.raw_value,
        fact.context_ref,
        context.entity_identifier,
        context.entity_scheme,
        context.period_type,
        context.period_start,
        context.period_end,
        context.period_instant,
        tuple((item.axis, item.member) for item in context.dimensions),
        fact.unit_ref,
        None if unit is None else unit.unit_type,
        None if unit is None else unit.measure,
        () if unit is None else unit.numerator,
        () if unit is None else unit.denominator,
        fact.decimals,
        fact.scale,
        fact.precision,
    )


def _proposed_locator(filing: XbrlFiling, facts: list[XbrlFact]) -> ProposedFactLocator:
    first = facts[0]
    unit = first.unit
    return ProposedFactLocator(
        accession_number=filing.accession_number,
        source_document=filing.source_document,
        source_url=filing.source_url,
        qualified_concept=first.element_id,
        original_labels=tuple(
            sorted({label for fact in facts if (label := fact.original_label) is not None})
        ),
        raw_value=first.raw_value,
        context_ref=first.context_ref,
        entity_identifier=first.context.entity_identifier,
        period_type=first.context.period_type,
        period_start=first.context.period_start,
        period_end=first.context.period_end,
        period_instant=first.context.period_instant,
        dimensions=first.context.dimensions,
        unit_ref=first.unit_ref,
        unit_measure=None if unit is None else unit.measure,
        decimals=first.decimals,
        scale=first.scale,
        precision=first.precision,
        fact_ids=tuple(sorted({item.fact_id for item in facts if item.fact_id is not None})),
        instance_ids=tuple(
            sorted({item.instance_id for item in facts if item.instance_id is not None})
        ),
        source_object_count=len(facts),
    )


def _detect_ambiguities(candidates: tuple[ProposedFactLocator, ...]) -> tuple[str, ...]:
    semantic_periods: dict[tuple[object, ...], set[str]] = {}
    for candidate in candidates:
        key = (
            candidate.entity_identifier,
            candidate.period_type,
            candidate.period_start,
            candidate.period_end,
            candidate.period_instant,
            tuple((item.axis, item.member) for item in candidate.dimensions),
            candidate.unit_measure,
        )
        semantic_periods.setdefault(key, set()).add(candidate.raw_value)
    if any(len(values) > 1 for values in semantic_periods.values()):
        return ("CONFLICTING_RAW_VALUES_FOR_EXACT_SEMANTIC_PERIOD",)
    return ()


def _selected_mapping_from_payload(
    value: object,
    *,
    xbrl: XbrlConceptMapping,
    location: str,
) -> SelectedFieldMapping:
    payload = _as_mapping(value, location=location)
    try:
        return SelectedFieldMapping(
            xbrl=xbrl,
            display_name=_required_string(payload, "display_name", location=location),
            classification=FinancialClassification(
                _required_string(payload, "classification", location=location)
            ),
            selection_decision=SelectionDecision(
                _required_string(payload, "selection_decision", location=location)
            ),
            selection_reason=_required_string(payload, "selection_reason", location=location),
            source_route=SourceRoute(_required_string(payload, "source_route", location=location)),
            currency=_required_string(payload, "currency", location=location),
            reporting_scope_name=_required_string(
                payload,
                "reporting_scope_name",
                location=location,
            ),
            reporting_scope_category=ReportingScopeCategory(
                _required_string(
                    payload,
                    "reporting_scope_category",
                    location=location,
                )
            ),
            portfolio_population=_required_string(
                payload,
                "portfolio_population",
                location=location,
            ),
            scope_methodology=_required_string(
                payload,
                "scope_methodology",
                location=location,
            ),
            eligible_forms=tuple(
                _string_sequence(
                    payload.get("eligible_forms"), location=f"{location}.eligible_forms"
                )
            ),
            directness=Directness(_required_string(payload, "directness", location=location)),
            raw_string_to_decimal=_required_string(
                payload,
                "raw_string_to_decimal",
                location=location,
            ),
            sign_convention=_required_string(payload, "sign_convention", location=location),
            validation_behavior=_required_string(
                payload,
                "validation_behavior",
                location=location,
            ),
            amendment_behavior=_required_string(
                payload,
                "amendment_behavior",
                location=location,
            ),
            publication_state=PublicationState(
                _required_string(payload, "publication_state", location=location)
            ),
            review_status=ReviewStatus(
                _required_string(payload, "review_status", location=location)
            ),
        )
    except ValueError as error:
        message = f"{location} contains an unsupported financial-field enum value"
        raise FinancialDiscoveryError(message) from error


def _as_mapping(value: object, *, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        message = f"{location} must be a string-keyed mapping"
        raise FinancialDiscoveryError(message)
    return cast("Mapping[str, object]", value)


def _as_sequence(value: object, *, location: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{location} must be a sequence"
        raise FinancialDiscoveryError(message)
    return cast("Sequence[object]", value)


def _string_sequence(value: object, *, location: str) -> tuple[str, ...]:
    sequence = _as_sequence(value, location=location)
    if not all(isinstance(item, str) and item.strip() for item in sequence):
        message = f"{location} must contain nonblank strings"
        raise FinancialDiscoveryError(message)
    return tuple(cast("str", item) for item in sequence)


def _required_string(payload: Mapping[str, object], key: str, *, location: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"{location}.{key} must be a nonblank string"
        raise FinancialDiscoveryError(message)
    return value
