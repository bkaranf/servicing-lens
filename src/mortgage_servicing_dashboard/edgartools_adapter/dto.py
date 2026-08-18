"""Typed, float-free values crossing the public-edgartools boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ContentRepresentation(StrEnum):
    """Honest descriptions of live-library content and bounded offline replay bytes."""

    LIBRARY_BINARY = "EDGARTOOLS_LIBRARY_BINARY"
    LIBRARY_TEXT_UTF8 = "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
    BOUNDED_REPLAY_EXCERPT = "BOUNDED_DERIVED_REPLAY_EXCERPT"


@dataclass(frozen=True, slots=True)
class Company:
    """Exact SEC company identity with CIK as the stable key."""

    cik: str
    name: str
    tickers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Filing:
    """Exact filing metadata selected by accession number."""

    cik: str
    accession_number: str
    company_name: str
    form: str
    filing_date: date
    acceptance_timestamp: datetime | None
    report_period: date | None
    primary_document: str
    amendment: bool
    is_xbrl: bool
    is_inline_xbrl: bool
    size: int | None
    homepage_url: str
    text_url: str

    @property
    def is_amendment(self) -> bool:
        """Return the explicitly captured amendment classification."""
        return self.amendment

    @property
    def period_of_report(self) -> date | None:
        """Provide the established alias for ``report_period``."""
        return self.report_period


@dataclass(frozen=True, slots=True)
class Attachment:
    """One filing attachment, without downloading its content."""

    cik: str
    accession_number: str
    document: str
    sequence: str
    description: str
    attachment_type: str
    size: int | None
    source_url: str
    is_primary: bool
    is_binary: bool

    @property
    def sequence_number(self) -> str:
        """Return the SEC attachment sequence as text."""
        return self.sequence

    @property
    def document_type(self) -> str:
        """Return the SEC attachment type."""
        return self.attachment_type

    @property
    def url(self) -> str:
        """Return the SEC source URL."""
        return self.source_url


@dataclass(frozen=True, slots=True)
class AcquiredContent:
    """Canonical bytes plus their acquisition and source lineage."""

    cik: str
    accession_number: str
    document: str
    source_url: str
    content: bytes
    media_type: str
    representation: ContentRepresentation
    capture_method: str
    sha256: str
    retrieved_at: datetime

    @property
    def byte_length(self) -> int:
        """Return the represented payload length."""
        return len(self.content)


@dataclass(frozen=True, slots=True)
class RetainedContent:
    """Content-addressed retention result with representation lineage."""

    content_sha256: str
    byte_length: int
    retention_location: str
    retained_at: datetime
    representation: ContentRepresentation
    capture_method: str
    media_type: str
    source_url: str


@dataclass(frozen=True, slots=True)
class AttachmentAcquisition:
    """One acquired attachment and its optional retained object."""

    attachment: Attachment
    content: AcquiredContent
    retained: RetainedContent | None


@dataclass(frozen=True, slots=True)
class XbrlDimension:
    """Raw axis/member pair from an XBRL context."""

    axis: str
    member: str


@dataclass(frozen=True, slots=True)
class XbrlContext:
    """Resolved XBRL context without inferred numeric values."""

    context_id: str
    entity_identifier: str | None
    entity_scheme: str | None
    period_type: str | None
    period_start: str | None
    period_end: str | None
    period_instant: str | None
    dimensions: tuple[XbrlDimension, ...]


@dataclass(frozen=True, slots=True)
class XbrlUnit:
    """Resolved structural unit keyed by the filing's raw unit reference."""

    unit_ref: str
    unit_type: str | None
    measure: str | None
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XbrlFact:
    """Filing fact with source strings and resolved context; never a float."""

    taxonomy: str
    concept: str
    original_label: str | None
    raw_value: str
    context_ref: str
    context: XbrlContext
    unit_ref: str | None
    unit: XbrlUnit | None
    decimals: str | None
    scale: str | None
    precision: str | None
    fact_id: str | None
    instance_id: str | None

    @property
    def element_id(self) -> str:
        """Return the conventional qualified concept identifier."""
        return f"{self.taxonomy}:{self.concept}" if self.taxonomy else self.concept

    @property
    def dimensions(self) -> tuple[XbrlDimension, ...]:
        """Return dimensions resolved through the exact fact context."""
        return self.context.dimensions


@dataclass(frozen=True, slots=True)
class XbrlFootnote:
    """Raw footnote text and its exact fact relationship."""

    fact_id: str
    footnote_id: str | None
    raw_text: str
    language: str | None
    role: str | None


@dataclass(frozen=True, slots=True)
class XbrlFiling:
    """Raw facts and exact context/unit registries for one filing."""

    cik: str
    accession_number: str
    source_document: str
    source_url: str
    facts: tuple[XbrlFact, ...]
    contexts: tuple[XbrlContext, ...]
    units: tuple[XbrlUnit, ...]


@dataclass(frozen=True, slots=True)
class CompanyFactCandidate:
    """Company Facts discovery candidate; never publication authority."""

    concept: str
    taxonomy: str
    raw_value: str
    unit: str
    period_start: date | None
    period_end: date | None
    filing_date: date | None
    form: str
    accession_number: str
    fiscal_year: str | None
    fiscal_period: str | None
    dimensions: tuple[XbrlDimension, ...]


@dataclass(frozen=True, slots=True)
class CompanyFactsDiscovery:
    """Non-authoritative discovery view from the SEC Company Facts dataset."""

    cik: str
    company_name: str
    facts: tuple[CompanyFactCandidate, ...]
    publication_authority: bool = False


@dataclass(frozen=True, slots=True)
class PresentationArc:
    """One presentation relationship used only for discovery and validation."""

    role_uri: str
    parent_element_id: str
    child_element_id: str
    order: str | None
    preferred_label: str | None


@dataclass(frozen=True, slots=True)
class CalculationArc:
    """One calculation relationship with its signed weight preserved as text."""

    role_uri: str
    parent_element_id: str
    child_element_id: str
    order: str | None
    weight: str


@dataclass(frozen=True, slots=True)
class DefinitionArc:
    """One definition/dimensional relationship used only for validation."""

    role_uri: str
    arcrole: str
    source_element_id: str
    target_element_id: str
    order: str | None
    context_element: str | None
    closed: str | None
    usable: str | None


@dataclass(frozen=True, slots=True)
class LinkbaseArc:
    """Compatibility view of a structural relationship."""

    kind: str
    role_uri: str
    parent_element_id: str
    child_element_id: str
    order: str | None
    weight: str | None
    preferred_label: str | None


@dataclass(frozen=True, slots=True)
class ViewerReport:
    """SEC viewer report metadata; numeric viewer values are omitted."""

    short_name: str
    long_name: str
    category: str
    role: str
    html_file_name: str
    position: str | None
    group_type: str
    concepts: tuple[str, ...]
    period_headers: tuple[str, ...]


class ViewerIssueClassification(StrEnum):
    """Allowed classifications for a viewer validation mismatch."""

    SOURCE_FILING_INCONSISTENCY = "SOURCE_FILING_INCONSISTENCY"
    EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY = "EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY"
    SELECTED_FIELD_MAPPING_ERROR = "SELECTED_FIELD_MAPPING_ERROR"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True, slots=True)
class RawMetadata:
    """One viewer metadata entry preserved as its raw textual representation."""

    key: str
    raw_value: str


@dataclass(frozen=True, slots=True)
class ViewerValidationIssue:
    """Validation-only viewer issue with no floating-point value fields."""

    classification: ViewerIssueClassification
    severity: str
    code: str | None
    message: str
    raw_metadata: tuple[RawMetadata, ...]


@dataclass(frozen=True, slots=True)
class FilingStructure:
    """Linkbases, footnotes, and viewer output used only for validation."""

    cik: str
    accession_number: str
    presentation_arcs: tuple[PresentationArc, ...]
    calculation_arcs: tuple[CalculationArc, ...]
    definition_arcs: tuple[DefinitionArc, ...]
    footnotes: tuple[XbrlFootnote, ...]
    viewer_reports: tuple[ViewerReport, ...]
    viewer_issues: tuple[ViewerValidationIssue, ...]
    publication_authority: bool = False

    @property
    def linkbase_arcs(self) -> tuple[LinkbaseArc, ...]:
        """Return a compatibility projection of all structural arcs."""
        presentation = tuple(
            LinkbaseArc(
                kind="presentation",
                role_uri=arc.role_uri,
                parent_element_id=arc.parent_element_id,
                child_element_id=arc.child_element_id,
                order=arc.order,
                weight=None,
                preferred_label=arc.preferred_label,
            )
            for arc in self.presentation_arcs
        )
        calculation = tuple(
            LinkbaseArc(
                kind="calculation",
                role_uri=arc.role_uri,
                parent_element_id=arc.parent_element_id,
                child_element_id=arc.child_element_id,
                order=arc.order,
                weight=arc.weight,
                preferred_label=None,
            )
            for arc in self.calculation_arcs
        )
        definition = tuple(
            LinkbaseArc(
                kind="definition",
                role_uri=arc.role_uri,
                parent_element_id=arc.source_element_id,
                child_element_id=arc.target_element_id,
                order=arc.order,
                weight=None,
                preferred_label=None,
            )
            for arc in self.definition_arcs
        )
        return presentation + calculation + definition
