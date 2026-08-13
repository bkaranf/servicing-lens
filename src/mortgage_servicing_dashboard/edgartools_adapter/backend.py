"""Typed, lazy boundary around the public :mod:`edgar` package."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Protocol, TypeAlias

from mortgage_servicing_dashboard.edgartools_adapter.bootstrap import EdgarBootstrap
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    AcquiredContent,
    Attachment,
    AttachmentAcquisition,
    CalculationArc,
    Company,
    CompanyFactCandidate,
    CompanyFactsDiscovery,
    ContentRepresentation,
    DefinitionArc,
    Filing,
    FilingStructure,
    PresentationArc,
    RawMetadata,
    ViewerIssueClassification,
    ViewerReport,
    ViewerValidationIssue,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlFiling,
    XbrlFootnote,
    XbrlUnit,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterConfigurationError,
    AdapterNotFoundError,
    AdapterParsingError,
    AdapterSelectionError,
    AdapterState,
    AdapterValidationError,
    EdgarToolsAdapterError,
    map_edgar_exception,
)

_ACCESSION_LENGTH = 20
_ASCII_CONTROL_LIMIT = 32
_CIK_LENGTH = 10
_DATE_RANGE_PARTS = 2
_TICKER_PATTERN = re.compile(r"[A-Z][A-Z0-9.-]{0,14}\Z")

FilingDateFilter: TypeAlias = date | tuple[date, date] | None


def _utc_now() -> datetime:
    """Return the current UTC acquisition time."""
    return datetime.now(UTC)


class EdgarToolsBackend(Protocol):
    """Injectable acquisition seam; test doubles need no identity or network."""

    def resolve_company(self, cik_or_ticker: str) -> Company:
        """Resolve an exact CIK or ticker and return the stable SEC CIK."""
        ...

    def list_filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: FilingDateFilter = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        """Return explicitly filtered filing metadata for one exact CIK."""
        ...

    def get_filing(self, accession_number: str, *, expected_cik: str | None = None) -> Filing:
        """Return exactly one filing by its validated accession number."""
        ...

    def list_attachments(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        """Enumerate primary documents, attachments, and exhibits."""
        ...

    def acquire_attachment(
        self,
        accession_number: str,
        document: str,
        *,
        expected_cik: str | None = None,
    ) -> AttachmentAcquisition:
        """Acquire one exact attachment through edgartools."""
        ...

    def get_filing_xbrl(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> XbrlFiling | None:
        """Return filing-specific raw XBRL, or ``None`` when genuinely absent."""
        ...

    def get_company_facts(self, cik: str) -> CompanyFactsDiscovery | None:
        """Return Company Facts for discovery only, or genuine absence."""
        ...

    def get_filing_structure(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> FilingStructure | None:
        """Return validation-only filing structure, or genuine XBRL absence."""
        ...


class PublicEdgarToolsBackend:
    """Production backend using only public edgartools entry points."""

    def __init__(
        self,
        bootstrap: EdgarBootstrap,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Retain the lazy bootstrap without importing edgartools."""
        self._bootstrap = bootstrap
        self._clock = clock or _utc_now

    def resolve_company(self, cik_or_ticker: str) -> Company:
        """Resolve an exact CIK or ticker and verify the returned stable CIK."""
        selector, expected_cik, expected_ticker = _normalize_company_selector(cik_or_ticker)
        edgar = self._bootstrap.load()
        try:
            library_company = edgar.Company(selector)
            not_found = getattr(library_company, "not_found", False)
            returned_cik = normalize_returned_cik(
                getattr(library_company, "cik", None),
                operation="resolve_company",
            )
            name = _required_text(
                getattr(library_company, "name", None),
                field="company name",
                operation="resolve_company",
            )
            tickers = _map_tickers(getattr(library_company, "tickers", None))
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="resolve_company")

        if not_found:
            message = "company was not found for the exact selector"
            raise AdapterNotFoundError(
                message,
                state=AdapterState.NOT_FOUND,
                operation="resolve_company",
            )
        if expected_cik is not None and returned_cik != expected_cik:
            message = "edgartools returned a different CIK"
            raise AdapterSelectionError(
                message,
                state=AdapterState.SELECTION_MISMATCH,
                operation="resolve_company",
            )
        if expected_ticker is not None and expected_ticker not in tickers:
            message = "edgartools did not return the exact requested ticker"
            raise AdapterSelectionError(
                message,
                state=AdapterState.SELECTION_MISMATCH,
                operation="resolve_company",
            )
        return Company(cik=returned_cik, name=name, tickers=tickers)

    def list_filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: FilingDateFilter = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        """Return explicit, identity-checked filing metadata for one company."""
        normalized_cik = validate_cik(cik)
        normalized_forms = _validate_forms(forms)
        normalized_date = _format_filing_date_filter(filing_date)
        normalized_amendments = _validate_include_amendments(include_amendments)

        edgar = self._bootstrap.load()
        try:
            company = edgar.Company(normalized_cik)
            library_filings = company.get_filings(
                form=list(normalized_forms) if normalized_forms else None,
                filing_date=normalized_date,
                amendments=normalized_amendments,
                trigger_full_load=True,
            )
            count = len(library_filings)
            results = tuple(
                _map_filing_from_result(library_filings[index], expected_cik=normalized_cik)
                for index in range(count)
            )
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="list_filings")
        return results

    def get_filing(self, accession_number: str, *, expected_cik: str | None = None) -> Filing:
        """Select an exact accession and optionally constrain it to one CIK.

        The CIK-constrained path first checks the company's recent submission
        metadata.  edgartools 5.48 does not automatically search older submission
        files when ``trigger_full_load`` is false, so a genuine empty result receives
        one completeness query with full loading enabled.  An exception never reaches
        that second query, and the unconstrained global lookup is never a fallback.

        Args:
            accession_number: Canonical SEC accession number.
            expected_cik: Optional one-to-ten digit SEC CIK.

        Returns:
            Exact, identity-checked filing metadata.

        Raises:
            EdgarToolsAdapterError: If acquisition or metadata conversion fails.
        """
        accession = validate_accession(accession_number)
        cik = validate_cik(expected_cik) if expected_cik is not None else None
        _, library_filing = self._select_library_filing(
            accession,
            expected_cik=cik,
            operation="get_filing",
        )
        return _map_filing(library_filing, accession=accession, expected_cik=cik)

    def list_attachments(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        """Enumerate every attachment and identify primary documents explicitly."""
        accession = validate_accession(accession_number)
        cik = validate_cik(expected_cik) if expected_cik is not None else None
        edgar, library_filing = self._select_library_filing(
            accession,
            expected_cik=cik,
            operation="list_attachments",
        )
        filing = _map_filing(library_filing, accession=accession, expected_cik=cik)
        try:
            library_attachments = library_filing.attachments
            primary_documents = _primary_document_names(library_attachments)
            results = tuple(
                _map_attachment(
                    item,
                    filing=filing,
                    primary_documents=primary_documents,
                    operation="list_attachments",
                )
                for item in library_attachments
            )
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="list_attachments")
        return results

    def acquire_attachment(
        self,
        accession_number: str,
        document: str,
        *,
        expected_cik: str | None = None,
    ) -> AttachmentAcquisition:
        """Acquire one exact attachment as binary or canonical UTF-8 bytes."""
        accession = validate_accession(accession_number)
        cik = validate_cik(expected_cik) if expected_cik is not None else None
        normalized_document = _validate_document(document)
        edgar, library_filing = self._select_library_filing(
            accession,
            expected_cik=cik,
            operation="acquire_attachment",
        )
        filing = _map_filing(library_filing, accession=accession, expected_cik=cik)
        try:
            library_attachments = library_filing.attachments
            primary_documents = _primary_document_names(library_attachments)
            matches = tuple(
                item
                for item in library_attachments
                if getattr(item, "document", None) == normalized_document
            )
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="acquire_attachment")
        if not matches:
            message = "attachment was not found for the exact document name"
            raise AdapterNotFoundError(
                message,
                state=AdapterState.NOT_FOUND,
                operation="acquire_attachment",
            )
        if len(matches) != 1:
            message = "edgartools returned duplicate exact attachment names"
            raise AdapterSelectionError(
                message,
                state=AdapterState.SELECTION_MISMATCH,
                operation="acquire_attachment",
            )
        library_attachment = matches[0]
        try:
            attachment = _map_attachment(
                library_attachment,
                filing=filing,
                primary_documents=primary_documents,
                operation="acquire_attachment",
            )
            library_content = library_attachment.download()
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="acquire_attachment")

        content = _map_acquired_content(
            library_content,
            attachment=attachment,
            retrieved_at=self._clock(),
        )
        return AttachmentAcquisition(attachment=attachment, content=content, retained=None)

    def get_filing_xbrl(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> XbrlFiling | None:
        """Map public filing-specific XBRL facts without using numeric conveniences."""
        accession = validate_accession(accession_number)
        cik = validate_cik(expected_cik) if expected_cik is not None else None
        edgar, library_filing = self._select_library_filing(
            accession,
            expected_cik=cik,
            operation="get_filing_xbrl",
        )
        filing = _map_filing(library_filing, accession=accession, expected_cik=cik)
        try:
            library_xbrl = library_filing.xbrl()
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="get_filing_xbrl")
        if library_xbrl is None:
            return None
        return _map_xbrl_filing(library_xbrl, filing=filing, library_filing=library_filing)

    def get_company_facts(self, cik: str) -> CompanyFactsDiscovery | None:
        """Map public Company Facts into an explicitly discovery-only result."""
        normalized_cik = validate_cik(cik)
        edgar = self._bootstrap.load()
        try:
            company = edgar.Company(normalized_cik)
            library_facts = company.get_facts()
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="get_company_facts")
        if library_facts is None:
            return None
        return _map_company_facts(library_facts, expected_cik=normalized_cik)

    def get_filing_structure(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> FilingStructure | None:
        """Map linkbases, footnotes, and SEC viewer validation metadata."""
        accession = validate_accession(accession_number)
        cik = validate_cik(expected_cik) if expected_cik is not None else None
        edgar, library_filing = self._select_library_filing(
            accession,
            expected_cik=cik,
            operation="get_filing_structure",
        )
        filing = _map_filing(library_filing, accession=accession, expected_cik=cik)
        try:
            library_xbrl = library_filing.xbrl()
            if library_xbrl is None:
                return None
            library_viewer = library_filing.viewer
            structure = _map_filing_structure(
                library_xbrl,
                library_viewer,
                filing=filing,
            )
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation="get_filing_structure")
        return structure

    def _select_library_filing(
        self,
        accession: str,
        *,
        expected_cik: str | None,
        operation: str,
    ) -> tuple[Any, Any]:
        """Select one library filing without an unconstrained identity fallback."""
        edgar = self._bootstrap.load()
        try:
            if expected_cik is None:
                library_filing = edgar.get_by_accession_number(accession)
            else:
                company = edgar.Company(expected_cik)
                library_filing = _get_from_company_metadata(
                    company,
                    accession=accession,
                    trigger_full_load=False,
                )
                if library_filing is None:
                    library_filing = _get_from_company_metadata(
                        company,
                        accession=accession,
                        trigger_full_load=True,
                    )
        except EdgarToolsAdapterError:
            raise
        except Exception as error:  # noqa: BLE001 - narrowed against lazy EdgarError below
            _raise_mapped_edgar_error(error, edgar=edgar, operation=operation)
        if library_filing is None:
            message = "filing was not found for the exact accession number"
            raise AdapterNotFoundError(
                message,
                state=AdapterState.NOT_FOUND,
                operation=operation,
            )
        return edgar, library_filing


def validate_cik(value: str) -> str:
    """Validate and zero-pad an ASCII CIK without ticker or fuzzy lookup."""
    if not value or len(value) > _CIK_LENGTH or not value.isascii() or not value.isdecimal():
        message = "CIK must contain one to ten ASCII digits"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="validate_cik",
        )
    return value.zfill(_CIK_LENGTH)


def validate_accession(value: str) -> str:
    """Require the canonical SEC accession format ``##########-##-######``."""
    valid = (
        len(value) == _ACCESSION_LENGTH
        and value.isascii()
        and value[:10].isdecimal()
        and value[10] == "-"
        and value[11:13].isdecimal()
        and value[13] == "-"
        and value[14:].isdecimal()
    )
    if not valid:
        message = "accession number must match ##########-##-###### exactly"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="validate_accession",
        )
    return value


def normalize_returned_cik(value: object, *, operation: str = "get_filing") -> str:
    """Normalize an edgartools integer/string CIK and reject impossible results."""
    if isinstance(value, bool):
        raw = ""
    elif isinstance(value, int):
        raw = str(value)
    elif isinstance(value, str):
        raw = value
    else:
        raw = ""
    try:
        return validate_cik(raw)
    except AdapterValidationError as error:
        message = "edgartools returned an invalid CIK"
        raise AdapterSelectionError(
            message,
            state=AdapterState.SELECTION_MISMATCH,
            operation=operation,
        ) from error


def _normalize_company_selector(value: str) -> tuple[str, str | None, str | None]:
    if not isinstance(value, str) or not value:
        message = "company selector must be an exact CIK or ticker"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="resolve_company",
        )
    if value.isascii() and value.isdecimal():
        cik = validate_cik(value)
        return cik, cik, None
    ticker = value.upper()
    if value != value.strip() or _TICKER_PATTERN.fullmatch(ticker) is None:
        message = "ticker must contain only ASCII letters, digits, period, or hyphen"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="resolve_company",
        )
    return ticker, None, ticker


def _map_tickers(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Iterable):
        message = "edgartools returned invalid company tickers"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation="resolve_company",
        )
    tickers: list[str] = []
    for item in value:
        if not isinstance(item, str) or _TICKER_PATTERN.fullmatch(item.upper()) is None:
            message = "edgartools returned an invalid company ticker"
            raise AdapterParsingError(
                message,
                state=AdapterState.PARSING_ERROR,
                operation="resolve_company",
            )
        normalized = item.upper()
        if normalized not in tickers:
            tickers.append(normalized)
    return tuple(tickers)


def _validate_forms(forms: object) -> tuple[str, ...]:
    if not isinstance(forms, tuple):
        message = "forms must be a tuple of exact SEC form strings"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="list_filings",
        )
    normalized: list[str] = []
    for form in forms:
        if (
            not isinstance(form, str)
            or not form
            or not form.isascii()
            or form != form.strip()
            or any(character.isspace() for character in form)
        ):
            message = "each form must be a non-empty exact ASCII SEC form string"
            raise AdapterValidationError(
                message,
                state=AdapterState.INVALID_REQUEST,
                operation="list_filings",
            )
        if form not in normalized:
            normalized.append(form)
    return tuple(normalized)


def _validate_include_amendments(value: object) -> bool:
    if not isinstance(value, bool):
        message = "include_amendments must be a boolean"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="list_filings",
        )
    return value


def _format_filing_date_filter(value: FilingDateFilter) -> str | tuple[str, str] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        message = "filing_date must use date values without a time component"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="list_filings",
        )
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, tuple) or len(value) != _DATE_RANGE_PARTS:
        message = "filing_date must be a date or inclusive date pair"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="list_filings",
        )
    start, end = value
    if (
        isinstance(start, datetime)
        or isinstance(end, datetime)
        or not isinstance(start, date)
        or not isinstance(end, date)
        or start > end
    ):
        message = "filing_date range must contain ordered date values"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="list_filings",
        )
    return start.isoformat(), end.isoformat()


def _validate_document(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "/" in value
        or "\\" in value
        or any(ord(character) < _ASCII_CONTROL_LIMIT for character in value)
    ):
        message = "document must be one exact attachment filename"
        raise AdapterValidationError(
            message,
            state=AdapterState.INVALID_REQUEST,
            operation="acquire_attachment",
        )
    return value


def _get_from_company_metadata(
    company: Any,
    *,
    accession: str,
    trigger_full_load: bool,
) -> object | None:
    filings = company.get_filings(
        accession_number=accession,
        trigger_full_load=trigger_full_load,
    )
    if filings is None:
        return None
    filing: object | None = filings.get(accession)
    return filing


def _raise_mapped_edgar_error(error: Exception, *, edgar: Any, operation: str) -> None:
    """Raise one secret-safe mapping for an actual edgartools domain error."""
    edgar_error = getattr(edgar, "EdgarError", None)
    if not isinstance(edgar_error, type) or not isinstance(error, edgar_error):
        raise error
    mapped = map_edgar_exception(error, operation=operation)
    raise mapped from error


def _map_filing(
    value: Any,
    *,
    accession: str,
    expected_cik: str | None,
    operation: str = "get_filing",
) -> Filing:
    returned_accession = _required_text(
        getattr(value, "accession_number", None),
        field="accession_number",
        operation=operation,
    )
    if returned_accession != accession:
        message = "edgartools returned a different accession number"
        raise AdapterSelectionError(
            message,
            state=AdapterState.SELECTION_MISMATCH,
            operation=operation,
        )

    returned_cik = normalize_returned_cik(getattr(value, "cik", None), operation=operation)
    if expected_cik is not None and returned_cik != expected_cik:
        message = "filing CIK did not match the exact expected CIK"
        raise AdapterSelectionError(
            message,
            state=AdapterState.SELECTION_MISMATCH,
            operation=operation,
        )

    form = _required_text(getattr(value, "form", None), field="form", operation=operation)
    return Filing(
        cik=returned_cik,
        accession_number=returned_accession,
        company_name=_required_text(
            getattr(value, "company", None),
            field="company",
            operation=operation,
        ),
        form=form,
        filing_date=_coerce_date(
            getattr(value, "filing_date", None),
            field="filing_date",
            operation=operation,
        ),
        acceptance_timestamp=_optional_datetime(
            getattr(value, "acceptance_datetime", None),
            field="acceptance_datetime",
            operation=operation,
        ),
        report_period=_optional_date(
            getattr(value, "report_date", None),
            field="report_date",
            operation=operation,
        ),
        primary_document=_optional_text(
            getattr(value, "primary_document", None),
            operation=operation,
        ),
        amendment=form.endswith("/A"),
        is_xbrl=_optional_bool(
            getattr(value, "is_xbrl", None),
            field="is_xbrl",
            operation=operation,
        ),
        is_inline_xbrl=_optional_bool(
            getattr(value, "is_inline_xbrl", None),
            field="is_inline_xbrl",
            operation=operation,
        ),
        size=_optional_nonnegative_int(
            getattr(value, "size", None),
            field="size",
            operation=operation,
        ),
        homepage_url=_required_text(
            getattr(value, "homepage_url", None),
            field="homepage_url",
            operation=operation,
        ),
        text_url=_required_text(
            getattr(value, "text_url", None),
            field="text_url",
            operation=operation,
        ),
    )


def _map_filing_from_result(value: Any, *, expected_cik: str) -> Filing:
    accession = _required_text(
        getattr(value, "accession_number", None),
        field="accession_number",
        operation="list_filings",
    )
    try:
        validate_accession(accession)
    except AdapterValidationError as error:
        message = "edgartools returned an invalid accession_number"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation="list_filings",
        ) from error
    return _map_filing(
        value,
        accession=accession,
        expected_cik=expected_cik,
        operation="list_filings",
    )


def _primary_document_names(value: Any) -> frozenset[str]:
    primary_documents = getattr(value, "primary_documents", None)
    if primary_documents is None:
        return frozenset()
    names: set[str] = set()
    for item in primary_documents:
        name = getattr(item, "document", None)
        if isinstance(name, str) and name:
            names.add(name)
    return frozenset(names)


def _map_attachment(
    value: Any,
    *,
    filing: Filing,
    primary_documents: frozenset[str],
    operation: str,
) -> Attachment:
    document = _required_text(
        getattr(value, "document", None),
        field="attachment document",
        operation=operation,
    )
    sequence = _text_or_empty(
        getattr(value, "sequence_number", None),
        field="attachment sequence",
        operation=operation,
    )
    description = _text_or_empty(
        getattr(value, "description", None),
        field="attachment description",
        operation=operation,
    )
    attachment_type = _text_or_empty(
        getattr(value, "document_type", None),
        field="attachment type",
        operation=operation,
    )
    source_url = _required_text(
        getattr(value, "url", None),
        field="attachment URL",
        operation=operation,
    )
    try:
        binary = value.is_binary()
    except (AttributeError, TypeError) as error:
        message = "edgartools returned an invalid attachment binary classifier"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        ) from error
    if not isinstance(binary, bool):
        message = "edgartools returned an invalid attachment binary classifier"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return Attachment(
        cik=filing.cik,
        accession_number=filing.accession_number,
        document=document,
        sequence=sequence,
        description=description,
        attachment_type=attachment_type,
        size=_optional_nonnegative_int(
            getattr(value, "size", None),
            field="attachment size",
            operation=operation,
        ),
        source_url=source_url,
        is_primary=document in primary_documents,
        is_binary=binary,
    )


def _map_acquired_content(
    value: object,
    *,
    attachment: Attachment,
    retrieved_at: datetime,
) -> AcquiredContent:
    if isinstance(value, bytes):
        content = value
        representation = ContentRepresentation.LIBRARY_BINARY
        capture_method = "edgartools_attachment_binary"
    elif isinstance(value, str):
        content = value.encode("utf-8")
        representation = ContentRepresentation.LIBRARY_TEXT_UTF8
        capture_method = "edgartools_attachment_text_utf8"
    else:
        message = "edgartools returned neither bytes nor text for the attachment"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation="acquire_attachment",
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        message = "attachment acquisition clock must return a timezone-aware timestamp"
        raise AdapterConfigurationError(
            message,
            state=AdapterState.CONFIGURATION_ERROR,
            operation="acquire_attachment",
        )
    return AcquiredContent(
        cik=attachment.cik,
        accession_number=attachment.accession_number,
        document=attachment.document,
        source_url=attachment.source_url,
        content=content,
        media_type=_media_type(attachment.document, binary=attachment.is_binary),
        representation=representation,
        capture_method=capture_method,
        sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at=retrieved_at.astimezone(UTC),
    )


def _media_type(document: str, *, binary: bool) -> str:
    suffix = document.rsplit(".", 1)[-1].lower() if "." in document else ""
    known = {
        "csv": "text/csv; charset=utf-8",
        "htm": "text/html; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "json": "application/json",
        "pdf": "application/pdf",
        "txt": "text/plain; charset=utf-8",
        "xbrl": "application/xml",
        "xml": "application/xml",
        "xsd": "application/xml",
        "zip": "application/zip",
    }
    if suffix in known:
        return known[suffix]
    return "application/octet-stream" if binary else "text/plain; charset=utf-8"


def _map_xbrl_filing(value: Any, *, filing: Filing, library_filing: Any) -> XbrlFiling:
    operation = "get_filing_xbrl"
    raw_contexts = _required_mapping(
        getattr(value, "contexts", None),
        field="XBRL contexts",
        operation=operation,
    )
    contexts = tuple(
        _map_xbrl_context(context_id, raw_contexts[context_id], operation=operation)
        for context_id in sorted(raw_contexts, key=str)
    )
    context_by_id = {context.context_id: context for context in contexts}

    raw_units = _required_mapping(
        getattr(value, "units", None),
        field="XBRL units",
        operation=operation,
    )
    units = tuple(
        _map_xbrl_unit(unit_ref, raw_units[unit_ref], operation=operation)
        for unit_ref in sorted(raw_units, key=str)
    )
    unit_by_ref = {unit.unit_ref: unit for unit in units}

    parser = getattr(value, "parser", None)
    raw_facts = _required_mapping(
        getattr(parser, "facts", None),
        field="XBRL raw facts",
        operation=operation,
    )
    facts = tuple(
        _map_xbrl_fact(
            raw_facts[fact_key],
            context_by_id=context_by_id,
            unit_by_ref=unit_by_ref,
            element_catalog=getattr(value, "element_catalog", None),
            operation=operation,
        )
        for fact_key in sorted(raw_facts, key=str)
    )
    source_document = _required_text(
        filing.primary_document,
        field="primary XBRL source document",
        operation=operation,
    )
    source_url_value = getattr(library_filing, "filing_url", None)
    source_url = _required_text(
        source_url_value,
        field="primary XBRL source URL",
        operation=operation,
    )
    return XbrlFiling(
        cik=filing.cik,
        accession_number=filing.accession_number,
        source_document=source_document,
        source_url=source_url,
        facts=facts,
        contexts=contexts,
        units=units,
    )


def _map_xbrl_context(key: object, value: Any, *, operation: str) -> XbrlContext:
    context_id = _mapping_key_text(key, field="XBRL context ID", operation=operation)
    returned_id = _required_text(
        getattr(value, "context_id", None),
        field="XBRL context ID",
        operation=operation,
    )
    if returned_id != context_id:
        message = "edgartools returned a mismatched XBRL context ID"
        raise AdapterSelectionError(
            message,
            state=AdapterState.SELECTION_MISMATCH,
            operation=operation,
        )
    entity = _optional_mapping(
        getattr(value, "entity", None),
        field="XBRL context entity",
        operation=operation,
    )
    period = _optional_mapping(
        getattr(value, "period", None),
        field="XBRL context period",
        operation=operation,
    )
    dimensions_mapping = _optional_mapping(
        getattr(value, "dimensions", None),
        field="XBRL context dimensions",
        operation=operation,
    )
    dimensions = tuple(
        XbrlDimension(
            axis=_mapping_key_text(axis, field="XBRL dimension axis", operation=operation),
            member=_required_text(
                member,
                field="XBRL dimension member",
                operation=operation,
            ),
        )
        for axis, member in sorted(dimensions_mapping.items(), key=lambda item: str(item[0]))
    )
    return XbrlContext(
        context_id=context_id,
        entity_identifier=_optional_mapping_text(
            entity,
            "identifier",
            field="XBRL entity identifier",
            operation=operation,
        ),
        entity_scheme=_optional_mapping_text(
            entity,
            "scheme",
            field="XBRL entity scheme",
            operation=operation,
        ),
        period_type=_optional_mapping_text(
            period,
            "type",
            field="XBRL period type",
            operation=operation,
        ),
        period_start=_optional_mapping_text(
            period,
            "startDate",
            field="XBRL period start",
            operation=operation,
        ),
        period_end=_optional_mapping_text(
            period,
            "endDate",
            field="XBRL period end",
            operation=operation,
        ),
        period_instant=_optional_mapping_text(
            period,
            "instant",
            field="XBRL period instant",
            operation=operation,
        ),
        dimensions=dimensions,
    )


def _map_xbrl_unit(key: object, value: object, *, operation: str) -> XbrlUnit:
    unit_ref = _mapping_key_text(key, field="XBRL unit reference", operation=operation)
    mapping = _required_mapping(value, field="XBRL unit", operation=operation)
    unit_type = _optional_mapping_text(
        mapping,
        "type",
        field="XBRL unit type",
        operation=operation,
    )
    measure = _optional_mapping_text(
        mapping,
        "measure",
        field="XBRL unit measure",
        operation=operation,
    )
    return XbrlUnit(
        unit_ref=unit_ref,
        unit_type=unit_type,
        measure=measure,
        numerator=_map_text_sequence(
            mapping.get("numerator"),
            field="XBRL unit numerator",
            operation=operation,
        ),
        denominator=_map_text_sequence(
            mapping.get("denominator"),
            field="XBRL unit denominator",
            operation=operation,
        ),
    )


def _map_xbrl_fact(
    value: Any,
    *,
    context_by_id: Mapping[str, XbrlContext],
    unit_by_ref: Mapping[str, XbrlUnit],
    element_catalog: object,
    operation: str,
) -> XbrlFact:
    element_id = _required_text(
        getattr(value, "element_id", None),
        field="XBRL fact concept",
        operation=operation,
    )
    taxonomy, concept = _split_element_id(element_id)
    context_ref = _required_text(
        getattr(value, "context_ref", None),
        field="XBRL fact context reference",
        operation=operation,
    )
    context = context_by_id.get(context_ref)
    if context is None:
        message = "XBRL fact referenced an unknown context"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    unit_ref = _optional_scalar_text(
        getattr(value, "unit_ref", None),
        field="XBRL fact unit reference",
        operation=operation,
    )
    unit = unit_by_ref.get(unit_ref) if unit_ref is not None else None
    if unit_ref is not None and unit is None:
        message = "XBRL fact referenced an unknown unit"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return XbrlFact(
        taxonomy=taxonomy,
        concept=concept,
        original_label=_element_label(element_catalog, element_id, operation=operation),
        raw_value=_required_text(
            getattr(value, "value", None),
            field="XBRL fact raw value",
            operation=operation,
        ),
        context_ref=context_ref,
        context=context,
        unit_ref=unit_ref,
        unit=unit,
        decimals=_optional_scalar_text(
            getattr(value, "decimals", None),
            field="XBRL fact decimals",
            operation=operation,
        ),
        scale=_optional_scalar_text(
            getattr(value, "scale", None),
            field="XBRL fact scale",
            operation=operation,
        ),
        precision=_optional_scalar_text(
            getattr(value, "precision", None),
            field="XBRL fact precision",
            operation=operation,
        ),
        fact_id=_optional_scalar_text(
            getattr(value, "fact_id", None),
            field="XBRL fact ID",
            operation=operation,
        ),
        instance_id=_optional_scalar_text(
            getattr(value, "instance_id", None),
            field="XBRL fact instance ID",
            operation=operation,
        ),
    )


def _split_element_id(value: str) -> tuple[str, str]:
    if ":" not in value:
        return "", value
    taxonomy, concept = value.split(":", 1)
    return taxonomy, concept


def _element_label(value: object, element_id: str, *, operation: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    element = value.get(element_id) or value.get(element_id.replace(":", "_"))
    if element is None:
        return None
    labels = getattr(element, "labels", None)
    if not isinstance(labels, Mapping) or not labels:
        return None
    standard_role = "http://www.xbrl.org/2003/role/label"
    selected = labels.get(standard_role)
    if selected is None:
        selected = labels[min(labels, key=str)]
    return _required_text(selected, field="XBRL concept label", operation=operation)


def _map_company_facts(value: Any, *, expected_cik: str) -> CompanyFactsDiscovery:
    operation = "get_company_facts"
    returned_cik = normalize_returned_cik(
        getattr(value, "cik", None),
        operation=operation,
    )
    if returned_cik != expected_cik:
        message = "Company Facts CIK did not match the exact requested CIK"
        raise AdapterSelectionError(
            message,
            state=AdapterState.SELECTION_MISMATCH,
            operation=operation,
        )
    company_name = _required_text(
        getattr(value, "name", None),
        field="Company Facts entity name",
        operation=operation,
    )
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        message = "edgartools returned invalid Company Facts"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    facts = tuple(_map_company_fact(item) for item in value)
    return CompanyFactsDiscovery(
        cik=returned_cik,
        company_name=company_name,
        facts=facts,
    )


def _map_company_fact(value: Any) -> CompanyFactCandidate:
    operation = "get_company_facts"
    qualified_concept = _required_text(
        getattr(value, "concept", None),
        field="Company Facts concept",
        operation=operation,
    )
    returned_taxonomy = _required_text(
        getattr(value, "taxonomy", None),
        field="Company Facts taxonomy",
        operation=operation,
    )
    taxonomy, concept = _split_element_id(qualified_concept)
    if taxonomy and taxonomy != returned_taxonomy:
        message = "Company Facts concept taxonomy was inconsistent"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    dimensions_mapping = _optional_mapping(
        getattr(value, "dimensions", None),
        field="Company Facts dimensions",
        operation=operation,
    )
    dimensions = tuple(
        XbrlDimension(
            axis=_mapping_key_text(
                axis,
                field="Company Facts dimension axis",
                operation=operation,
            ),
            member=_required_text(
                member,
                field="Company Facts dimension member",
                operation=operation,
            ),
        )
        for axis, member in sorted(dimensions_mapping.items(), key=lambda item: str(item[0]))
    )
    return CompanyFactCandidate(
        concept=concept,
        taxonomy=returned_taxonomy,
        raw_value=_discovery_value_text(getattr(value, "value", None)),
        unit=_required_text(
            getattr(value, "unit", None),
            field="Company Facts unit",
            operation=operation,
        ),
        period_start=_optional_date(
            getattr(value, "period_start", None),
            field="Company Facts period start",
            operation=operation,
        ),
        period_end=_optional_date(
            getattr(value, "period_end", None),
            field="Company Facts period end",
            operation=operation,
        ),
        filing_date=_optional_date(
            getattr(value, "filing_date", None),
            field="Company Facts filing date",
            operation=operation,
        ),
        form=_text_or_empty(
            getattr(value, "form_type", None),
            field="Company Facts form",
            operation=operation,
        ),
        accession_number=_text_or_empty(
            getattr(value, "accession", None),
            field="Company Facts accession",
            operation=operation,
        ),
        fiscal_year=_optional_scalar_text(
            getattr(value, "fiscal_year", None),
            field="Company Facts fiscal year",
            operation=operation,
        ),
        fiscal_period=_optional_scalar_text(
            getattr(value, "fiscal_period", None),
            field="Company Facts fiscal period",
            operation=operation,
        ),
        dimensions=dimensions,
    )


def _discovery_value_text(value: object) -> str:
    operation = "get_company_facts"
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        # Company Facts is discovery-only.  The library has already materialized
        # JSON numerics, so this is explicitly a library-value representation and
        # never eligible for publication as a source raw string.
        return repr(value)
    message = "edgartools returned an invalid Company Facts value"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _map_filing_structure(
    xbrl: Any,
    viewer: Any | None,
    *,
    filing: Filing,
) -> FilingStructure:
    presentation_arcs = _map_presentation_arcs(xbrl)
    calculation_arcs = _map_calculation_arcs(xbrl)
    definition_arcs = _map_definition_arcs(xbrl)
    footnotes = _map_xbrl_footnotes(xbrl)
    viewer_reports = _map_viewer_reports(viewer)
    viewer_issues = _map_viewer_issues(viewer, xbrl=xbrl)
    return FilingStructure(
        cik=filing.cik,
        accession_number=filing.accession_number,
        presentation_arcs=presentation_arcs,
        calculation_arcs=calculation_arcs,
        definition_arcs=definition_arcs,
        footnotes=footnotes,
        viewer_reports=viewer_reports,
        viewer_issues=viewer_issues,
    )


def _map_presentation_arcs(value: Any) -> tuple[PresentationArc, ...]:
    operation = "get_filing_structure"
    trees = _required_mapping(
        getattr(value, "presentation_trees", None),
        field="XBRL presentation trees",
        operation=operation,
    )
    arcs: list[PresentationArc] = []
    for role_key in sorted(trees, key=str):
        role_uri = _mapping_key_text(
            role_key,
            field="presentation role URI",
            operation=operation,
        )
        nodes = _required_mapping(
            getattr(trees[role_key], "all_nodes", None),
            field="presentation nodes",
            operation=operation,
        )
        for parent_key in sorted(nodes, key=str):
            parent_id = _mapping_key_text(
                parent_key,
                field="presentation parent concept",
                operation=operation,
            )
            parent = nodes[parent_key]
            children = _map_text_sequence(
                getattr(parent, "children", None),
                field="presentation child concepts",
                operation=operation,
            )
            preferred_labels = _map_optional_text_sequence(
                getattr(parent, "child_preferred_labels", None),
                field="presentation preferred labels",
                operation=operation,
            )
            for index, child_id in enumerate(children):
                child = nodes.get(child_id)
                if child is None:
                    message = "presentation tree referenced an unknown child"
                    raise AdapterParsingError(
                        message,
                        state=AdapterState.PARSING_ERROR,
                        operation=operation,
                    )
                preferred_label = (
                    preferred_labels[index]
                    if index < len(preferred_labels)
                    else _optional_scalar_text(
                        getattr(child, "preferred_label", None),
                        field="presentation preferred label",
                        operation=operation,
                    )
                )
                arcs.append(
                    PresentationArc(
                        role_uri=role_uri,
                        parent_element_id=parent_id,
                        child_element_id=child_id,
                        order=_structural_number_text(
                            getattr(child, "order", None),
                            field="presentation order",
                            operation=operation,
                        ),
                        preferred_label=preferred_label,
                    )
                )
    return tuple(arcs)


def _map_calculation_arcs(value: Any) -> tuple[CalculationArc, ...]:
    operation = "get_filing_structure"
    trees = _required_mapping(
        getattr(value, "calculation_trees", None),
        field="XBRL calculation trees",
        operation=operation,
    )
    arcs: list[CalculationArc] = []
    for role_key in sorted(trees, key=str):
        role_uri = _mapping_key_text(
            role_key,
            field="calculation role URI",
            operation=operation,
        )
        nodes = _required_mapping(
            getattr(trees[role_key], "all_nodes", None),
            field="calculation nodes",
            operation=operation,
        )
        for parent_key in sorted(nodes, key=str):
            parent_id = _mapping_key_text(
                parent_key,
                field="calculation parent concept",
                operation=operation,
            )
            parent = nodes[parent_key]
            children = _map_text_sequence(
                getattr(parent, "children", None),
                field="calculation child concepts",
                operation=operation,
            )
            for child_id in children:
                child = nodes.get(child_id)
                if child is None:
                    message = "calculation tree referenced an unknown child"
                    raise AdapterParsingError(
                        message,
                        state=AdapterState.PARSING_ERROR,
                        operation=operation,
                    )
                weight = _structural_number_text(
                    getattr(child, "weight", None),
                    field="calculation weight",
                    operation=operation,
                    required=True,
                )
                if weight is None:
                    message = "edgartools returned a missing calculation weight"
                    raise AdapterParsingError(
                        message,
                        state=AdapterState.PARSING_ERROR,
                        operation=operation,
                    )
                arcs.append(
                    CalculationArc(
                        role_uri=role_uri,
                        parent_element_id=parent_id,
                        child_element_id=child_id,
                        order=_structural_number_text(
                            getattr(child, "order", None),
                            field="calculation order",
                            operation=operation,
                        ),
                        weight=weight,
                    )
                )
    return tuple(arcs)


def _map_definition_arcs(value: Any) -> tuple[DefinitionArc, ...]:
    """Project public dimensional structures without inventing unavailable order."""
    operation = "get_filing_structure"
    tables_by_role = _required_mapping(
        getattr(value, "tables", None),
        field="XBRL definition tables",
        operation=operation,
    )
    axes = _required_mapping(
        getattr(value, "axes", None),
        field="XBRL definition axes",
        operation=operation,
    )
    domains = _required_mapping(
        getattr(value, "domains", None),
        field="XBRL definition domains",
        operation=operation,
    )
    arcs: list[DefinitionArc] = []
    seen: set[tuple[str, str, str, str]] = set()
    for role_key in sorted(tables_by_role, key=str):
        role_uri = _mapping_key_text(
            role_key,
            field="definition role URI",
            operation=operation,
        )
        tables = _required_iterable(
            tables_by_role[role_key],
            field="XBRL definition tables",
            operation=operation,
        )
        for table in tables:
            table_id = _required_text(
                getattr(table, "element_id", None),
                field="definition table concept",
                operation=operation,
            )
            context_element = _optional_scalar_text(
                getattr(table, "context_element", None),
                field="definition context element",
                operation=operation,
            )
            closed = _optional_bool_text(
                getattr(table, "closed", None),
                field="definition closed flag",
                operation=operation,
            )
            line_items = _map_text_sequence(
                getattr(table, "line_items", None),
                field="definition line items",
                operation=operation,
            )
            for line_item in line_items:
                _append_definition_arc(
                    arcs,
                    seen,
                    role_uri=role_uri,
                    arcrole="http://xbrl.org/int/dim/arcrole/all",
                    source=line_item,
                    target=table_id,
                    context_element=context_element,
                    closed=closed,
                )
            axis_ids = _map_text_sequence(
                getattr(table, "axes", None),
                field="definition axes",
                operation=operation,
            )
            for axis_id in axis_ids:
                _append_definition_arc(
                    arcs,
                    seen,
                    role_uri=role_uri,
                    arcrole="http://xbrl.org/int/dim/arcrole/hypercube-dimension",
                    source=table_id,
                    target=axis_id,
                )
                axis = axes.get(axis_id)
                if axis is None:
                    message = "definition table referenced an unknown axis"
                    raise AdapterParsingError(
                        message,
                        state=AdapterState.PARSING_ERROR,
                        operation=operation,
                    )
                domain_id = _optional_scalar_text(
                    getattr(axis, "domain_id", None),
                    field="definition domain concept",
                    operation=operation,
                )
                if domain_id is not None:
                    _append_definition_arc(
                        arcs,
                        seen,
                        role_uri=role_uri,
                        arcrole="http://xbrl.org/int/dim/arcrole/dimension-domain",
                        source=axis_id,
                        target=domain_id,
                    )
                    _append_domain_members(
                        arcs,
                        seen,
                        domains,
                        role_uri=role_uri,
                        domain_id=domain_id,
                    )
                default_member = _optional_scalar_text(
                    getattr(axis, "default_member_id", None),
                    field="definition default member",
                    operation=operation,
                )
                if default_member is not None:
                    _append_definition_arc(
                        arcs,
                        seen,
                        role_uri=role_uri,
                        arcrole="http://xbrl.org/int/dim/arcrole/dimension-default",
                        source=axis_id,
                        target=default_member,
                    )
    return tuple(arcs)


def _append_domain_members(
    arcs: list[DefinitionArc],
    seen: set[tuple[str, str, str, str]],
    domains: Mapping[object, object],
    *,
    role_uri: str,
    domain_id: str,
) -> None:
    operation = "get_filing_structure"
    domain = domains.get(domain_id)
    if domain is None:
        return
    members = _map_text_sequence(
        getattr(domain, "members", None),
        field="definition domain members",
        operation=operation,
    )
    for member in members:
        _append_definition_arc(
            arcs,
            seen,
            role_uri=role_uri,
            arcrole="http://xbrl.org/int/dim/arcrole/domain-member",
            source=domain_id,
            target=member,
        )


def _append_definition_arc(  # noqa: PLR0913 - mirrors the exact arc tuple
    arcs: list[DefinitionArc],
    seen: set[tuple[str, str, str, str]],
    *,
    role_uri: str,
    arcrole: str,
    source: str,
    target: str,
    context_element: str | None = None,
    closed: str | None = None,
) -> None:
    key = role_uri, arcrole, source, target
    if key in seen:
        return
    seen.add(key)
    arcs.append(
        DefinitionArc(
            role_uri=role_uri,
            arcrole=arcrole,
            source_element_id=source,
            target_element_id=target,
            order=None,
            context_element=context_element,
            closed=closed,
            usable=None,
        )
    )


def _map_xbrl_footnotes(value: Any) -> tuple[XbrlFootnote, ...]:
    operation = "get_filing_structure"
    footnotes = _required_mapping(
        getattr(value, "footnotes", None),
        field="XBRL footnotes",
        operation=operation,
    )
    results: list[XbrlFootnote] = []
    for key in sorted(footnotes, key=str):
        footnote = footnotes[key]
        footnote_id = _mapping_key_text(
            key,
            field="XBRL footnote ID",
            operation=operation,
        )
        returned_id = _required_text(
            getattr(footnote, "footnote_id", None),
            field="XBRL footnote ID",
            operation=operation,
        )
        if returned_id != footnote_id:
            message = "edgartools returned a mismatched footnote ID"
            raise AdapterSelectionError(
                message,
                state=AdapterState.SELECTION_MISMATCH,
                operation=operation,
            )
        related_facts = _map_text_sequence(
            getattr(footnote, "related_fact_ids", None),
            field="XBRL footnote fact IDs",
            operation=operation,
        )
        raw_text = _required_text(
            getattr(footnote, "text", None),
            field="XBRL footnote text",
            operation=operation,
        )
        language = _optional_scalar_text(
            getattr(footnote, "lang", None),
            field="XBRL footnote language",
            operation=operation,
        )
        role = _optional_scalar_text(
            getattr(footnote, "role", None),
            field="XBRL footnote role",
            operation=operation,
        )
        results.extend(
            XbrlFootnote(
                fact_id=fact_id,
                footnote_id=footnote_id,
                raw_text=raw_text,
                language=language,
                role=role,
            )
            for fact_id in related_facts
        )
    return tuple(results)


def _map_viewer_reports(viewer: Any | None) -> tuple[ViewerReport, ...]:
    if viewer is None:
        return ()
    operation = "get_filing_structure"
    reports = _required_iterable(
        getattr(viewer, "all_reports", None),
        field="viewer reports",
        operation=operation,
    )
    return tuple(
        ViewerReport(
            short_name=_text_or_empty(
                getattr(report, "short_name", None),
                field="viewer report short name",
                operation=operation,
            ),
            long_name=_text_or_empty(
                getattr(report, "long_name", None),
                field="viewer report long name",
                operation=operation,
            ),
            category=_text_or_empty(
                getattr(report, "category", None),
                field="viewer report category",
                operation=operation,
            ),
            role=_text_or_empty(
                getattr(report, "role", None),
                field="viewer report role",
                operation=operation,
            ),
            html_file_name=_text_or_empty(
                getattr(report, "html_file_name", None),
                field="viewer report filename",
                operation=operation,
            ),
            position=_optional_scalar_text(
                getattr(report, "position", None),
                field="viewer report position",
                operation=operation,
            ),
            group_type=_text_or_empty(
                getattr(report, "group_type", None),
                field="viewer report group type",
                operation=operation,
            ),
            concepts=_map_text_sequence(
                getattr(report, "concepts", None),
                field="viewer report concepts",
                operation=operation,
            ),
            period_headers=_map_text_sequence(
                getattr(report, "period_headers", None),
                field="viewer report period headers",
                operation=operation,
            ),
        )
        for report in reports
    )


def _map_viewer_issues(viewer: Any | None, *, xbrl: Any) -> tuple[ViewerValidationIssue, ...]:
    if viewer is None:
        return ()
    operation = "get_filing_structure"
    validation_results = _required_iterable(
        viewer.validate(),
        field="viewer validation results",
        operation=operation,
    )
    issues: list[ViewerValidationIssue] = []
    for result in validation_results:
        mapping = _required_mapping(
            result,
            field="viewer validation result",
            operation=operation,
        )
        valid = mapping.get("valid")
        if not isinstance(valid, bool):
            message = "edgartools returned an invalid viewer validation status"
            raise AdapterParsingError(
                message,
                state=AdapterState.PARSING_ERROR,
                operation=operation,
            )
        if valid:
            continue
        parent = mapping.get("parent")
        parent_id = _optional_object_text(parent, "id")
        role = _optional_mapping_text(
            mapping,
            "role",
            field="viewer validation role",
            operation=operation,
        )
        metadata = tuple(
            item
            for item in (
                RawMetadata(key="parent_concept", raw_value=parent_id)
                if parent_id is not None
                else None,
                RawMetadata(key="report", raw_value=role) if role is not None else None,
            )
            if item is not None
        )
        issues.append(
            ViewerValidationIssue(
                classification=(ViewerIssueClassification.EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY),
                severity="error",
                code="viewer-calculation-mismatch",
                message="SEC viewer calculation validation reported a mismatch.",
                raw_metadata=metadata,
            )
        )

    comparison = viewer.compare(xbrl)
    comparison_results = _required_iterable(
        getattr(comparison, "results", None),
        field="viewer comparison results",
        operation=operation,
    )
    for result in comparison_results:
        match = getattr(result, "match", None)
        if not isinstance(match, bool):
            message = "edgartools returned an invalid viewer comparison status"
            raise AdapterParsingError(
                message,
                state=AdapterState.PARSING_ERROR,
                operation=operation,
            )
        if match:
            continue
        metadata = tuple(
            RawMetadata(key=key, raw_value=text)
            for key, text in (
                (
                    "concept",
                    _optional_object_text(result, "concept_id"),
                ),
                ("period", _optional_object_text(result, "period")),
                ("report", _optional_object_text(result, "report")),
            )
            if text is not None
        )
        missing = getattr(result, "xbrl_value", None) is None
        issues.append(
            ViewerValidationIssue(
                classification=(ViewerIssueClassification.EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY),
                severity="warning" if missing else "error",
                code="viewer-xbrl-missing" if missing else "viewer-xbrl-mismatch",
                message=(
                    "SEC viewer concept was absent from filing-specific XBRL output."
                    if missing
                    else "SEC viewer and filing-specific XBRL values did not reconcile."
                ),
                raw_metadata=metadata,
            )
        )
    return tuple(issues)


def _optional_object_text(value: object, attribute: str) -> str | None:
    if value is None:
        return None
    candidate = getattr(value, attribute, None)
    return candidate if isinstance(candidate, str) and candidate else None


def _required_iterable(value: object, *, field: str, operation: str) -> Iterable[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        message = f"edgartools returned invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return value


def _map_optional_text_sequence(
    value: object,
    *,
    field: str,
    operation: str,
) -> tuple[str | None, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        message = f"edgartools returned an invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return tuple(_optional_scalar_text(item, field=field, operation=operation) for item in value)


def _optional_bool_text(value: object, *, field: str, operation: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _structural_number_text(
    value: object,
    *,
    field: str,
    operation: str,
    required: bool = False,
) -> str | None:
    if value is None:
        if not required:
            return None
    elif isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    elif isinstance(value, float) and math.isfinite(value):
        return repr(value)
    elif isinstance(value, str) and value:
        return value
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _required_mapping(
    value: object,
    *,
    field: str,
    operation: str,
) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        message = f"edgartools returned invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return value


def _optional_mapping(
    value: object,
    *,
    field: str,
    operation: str,
) -> Mapping[object, object]:
    if value is None:
        return {}
    return _required_mapping(value, field=field, operation=operation)


def _mapping_key_text(value: object, *, field: str, operation: str) -> str:
    if isinstance(value, str) and value:
        return value
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _optional_mapping_text(
    value: Mapping[object, object],
    key: str,
    *,
    field: str,
    operation: str,
) -> str | None:
    return _optional_scalar_text(value.get(key), field=field, operation=operation)


def _optional_scalar_text(value: object, *, field: str, operation: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _map_text_sequence(value: object, *, field: str, operation: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Iterable):
        message = f"edgartools returned an invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return tuple(_required_text(item, field=field, operation=operation) for item in value)


def _required_text(value: object, *, field: str, operation: str = "get_filing") -> str:
    if not isinstance(value, str) or not value:
        message = f"edgartools returned an invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return value


def _text_or_empty(value: object, *, field: str, operation: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _optional_text(value: object, *, operation: str = "get_filing") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    message = "edgartools returned an invalid primary_document"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _coerce_date(value: object, *, field: str, operation: str = "get_filing") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            message = f"edgartools returned an invalid {field}"
            raise AdapterParsingError(
                message,
                state=AdapterState.PARSING_ERROR,
                operation=operation,
            ) from error
    message = f"edgartools returned a missing {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _optional_date(
    value: object,
    *,
    field: str,
    operation: str = "get_filing",
) -> date | None:
    if value is None or value == "":
        return None
    return _coerce_date(value, field=field, operation=operation)


def _optional_datetime(
    value: object,
    *,
    field: str,
    operation: str = "get_filing",
) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            return datetime.fromisoformat(normalized)
        except ValueError as error:
            message = f"edgartools returned an invalid {field}"
            raise AdapterParsingError(
                message,
                state=AdapterState.PARSING_ERROR,
                operation=operation,
            ) from error
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _optional_bool(
    value: object,
    *,
    field: str,
    operation: str = "get_filing",
) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    message = f"edgartools returned an invalid {field}"
    raise AdapterParsingError(
        message,
        state=AdapterState.PARSING_ERROR,
        operation=operation,
    )


def _optional_nonnegative_int(
    value: object,
    *,
    field: str,
    operation: str = "get_filing",
) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = f"edgartools returned an invalid {field}"
        raise AdapterParsingError(
            message,
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    return value
