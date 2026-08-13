"""Deterministic EdgarTools discovery and evidence-retention workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import TypeVar

from mortgage_servicing_dashboard.edgar_tools import (
    EdgarToolsAuthenticationError,
    EdgarToolsClient,
    EdgarToolsContentError,
    EdgarToolsError,
    EdgarToolsNotFoundError,
    EdgarToolsProviderUnavailableError,
    EdgarToolsQuotaBlockedError,
    EdgarToolsRateLimitError,
    EdgarToolsSchemaError,
    EdgarToolsTierBlockedError,
    EdgarToolsTransportError,
    EdgarToolsUnsafeRequestError,
    FilingDocument,
    FilingSummary,
    ProviderResponse,
)
from mortgage_servicing_dashboard.edgar_tools_evidence import (
    EdgarToolsDocumentLineage,
    EdgarToolsEvidenceStore,
    EdgarToolsEvidenceType,
)

_OVERLAP_DAYS = 7
_MAX_FILINGS_PER_SYNC = 100
_MAX_DOCUMENTS_PER_FILING = 25
_ELIGIBLE_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"})
T = TypeVar("T")


class EdgarToolsSyncState(StrEnum):
    """Explicit provider-only terminal states; missing disclosure is never inferred here."""

    DISCOVERED = "DISCOVERED"
    AUTHENTICATION_BLOCKED = "AUTHENTICATION_BLOCKED"
    TIER_BLOCKED = "TIER_BLOCKED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_NOT_AVAILABLE_VIA_PROVIDER = "SOURCE_NOT_AVAILABLE_VIA_PROVIDER"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PARSER_UNQUALIFIED = "PARSER_UNQUALIFIED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class EdgarToolsCompany:
    """Governed issuer identity used for provider resolution."""

    company_id: str
    ticker: str
    cik: str


@dataclass(frozen=True, slots=True)
class FilingSyncResult:
    """Bounded outcome for one filing without unpublished financial values."""

    accession_number: str
    form: str
    filing_date: str
    already_known: bool
    state: EdgarToolsSyncState
    retained_evidence_ids: tuple[str, ...]
    safe_detail: str

    def as_payload(self) -> dict[str, object]:
        """Return a CLI-safe result with identities but no response bodies or values."""
        return {
            "accession_number": self.accession_number,
            "form": self.form,
            "filing_date": self.filing_date,
            "already_known": self.already_known,
            "state": self.state.value,
            "retained_evidence_ids": list(self.retained_evidence_ids),
            "detail": self.safe_detail,
        }


@dataclass(frozen=True, slots=True)
class EdgarToolsSyncSummary:
    """One bounded company synchronization summary."""

    company_id: str
    ticker: str
    cik: str
    dry_run: bool
    overlap_start: str | None
    discovered_count: int
    eligible_count: int
    filing_results: tuple[FilingSyncResult, ...]
    retained_metadata_evidence_ids: tuple[str, ...]
    terminal_state: EdgarToolsSyncState

    def as_payload(self) -> dict[str, object]:
        """Return a safe run summary without credentials or unpublished values."""
        state_counts = {
            state.value: sum(result.state is state for result in self.filing_results)
            for state in EdgarToolsSyncState
            if any(result.state is state for result in self.filing_results)
        }
        return {
            "company_id": self.company_id,
            "ticker": self.ticker,
            "cik": self.cik,
            "dry_run": self.dry_run,
            "overlap_start": self.overlap_start,
            "discovered_count": self.discovered_count,
            "eligible_count": self.eligible_count,
            "state_counts": state_counts,
            "terminal_state": self.terminal_state.value,
            "retained_metadata_evidence_ids": list(self.retained_metadata_evidence_ids),
            "filings": [result.as_payload() for result in self.filing_results],
            "published_count": 0,
        }


def _classify_error(error: EdgarToolsError) -> tuple[EdgarToolsSyncState, str]:
    if isinstance(error, EdgarToolsAuthenticationError):
        result = EdgarToolsSyncState.AUTHENTICATION_BLOCKED, "provider authentication failed"
    elif isinstance(error, EdgarToolsTierBlockedError):
        result = EdgarToolsSyncState.TIER_BLOCKED, "provider capability is tier-blocked"
    elif isinstance(error, EdgarToolsQuotaBlockedError):
        result = EdgarToolsSyncState.QUOTA_BLOCKED, "provider quota is exhausted"
    elif isinstance(error, EdgarToolsRateLimitError):
        result = EdgarToolsSyncState.RATE_LIMITED, "provider rate limit remained active"
    elif isinstance(error, EdgarToolsNotFoundError):
        result = (
            EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER,
            "required filing resource is not exposed by the provider",
        )
    elif isinstance(error, EdgarToolsProviderUnavailableError | EdgarToolsTransportError):
        result = EdgarToolsSyncState.PROVIDER_UNAVAILABLE, "provider is unavailable"
    elif isinstance(
        error, EdgarToolsContentError | EdgarToolsSchemaError | EdgarToolsUnsafeRequestError
    ):
        result = EdgarToolsSyncState.FAILED, "provider response failed a safety or schema check"
    else:
        result = EdgarToolsSyncState.FAILED, "provider request failed"
    return result


def _eligible_document(document: FilingDocument, *, form: str) -> bool:
    document_type = (document.document_type or "").upper()
    filename = document.filename.lower()
    return document_type in {form.upper(), "EX-99", "EX-99.1", "EX-99.2"} or filename.endswith(
        (".htm", ".html", ".xml")
    )


class EdgarToolsSyncPipeline:
    """Single provider-only path through discovery, retention, and parser qualification."""

    def __init__(
        self,
        *,
        client: EdgarToolsClient,
        evidence_store: EdgarToolsEvidenceStore,
    ) -> None:
        """Bind the one hosted client and one content-addressed evidence store."""
        self._client = client
        self._evidence_store = evidence_store

    def _retain_json(self, response: ProviderResponse[T]) -> str:
        retained = self._evidence_store.retain(
            response,
            evidence_type=EdgarToolsEvidenceType.API_JSON,
        )
        return retained.evidence_id

    def _sync_filing(
        self,
        filing: FilingSummary,
        *,
        already_known: bool,
        dry_run: bool,
    ) -> FilingSyncResult:
        del dry_run  # Publication stays closed until a filing-specific parser is qualified.
        evidence_ids: list[str] = []
        try:
            detail_response = self._client.get_filing(filing.cik, filing.accession_number)
            evidence_ids.append(self._retain_json(detail_response))
            listing = self._client.list_filing_documents(filing.cik, filing.accession_number)
            evidence_ids.append(self._retain_json(listing))
            documents = tuple(
                document
                for document in listing.value
                if _eligible_document(document, form=filing.form)
            )
            if not documents:
                return FilingSyncResult(
                    filing.accession_number,
                    filing.form,
                    filing.filing_date,
                    already_known,
                    EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER,
                    tuple(evidence_ids),
                    "provider returned no eligible filing documents",
                )
            if len(documents) > _MAX_DOCUMENTS_PER_FILING:
                return FilingSyncResult(
                    filing.accession_number,
                    filing.form,
                    filing.filing_date,
                    already_known,
                    EdgarToolsSyncState.QUARANTINED,
                    tuple(evidence_ids),
                    "eligible document count exceeded the deterministic bound",
                )
            for document in documents:
                fetched = self._client.fetch_filing_document(
                    filing.cik,
                    filing.accession_number,
                    document.filename,
                )
                lineage = EdgarToolsDocumentLineage(
                    cik=filing.cik,
                    accession_number=filing.accession_number,
                    form=filing.form,
                    filing_date=filing.filing_date,
                    filename=document.filename,
                    document_type=document.document_type,
                    sequence=document.sequence,
                    provider_reported_sec_url=document.provider_reported_sec_url,
                )
                retained = self._evidence_store.retain(
                    fetched,
                    evidence_type=EdgarToolsEvidenceType.DOCUMENT_BYTES,
                    lineage=lineage,
                )
                evidence_ids.append(retained.evidence_id)
        except EdgarToolsError as error:
            state, safe_detail = _classify_error(error)
            return FilingSyncResult(
                filing.accession_number,
                filing.form,
                filing.filing_date,
                already_known,
                state,
                tuple(evidence_ids),
                safe_detail,
            )
        return FilingSyncResult(
            filing.accession_number,
            filing.form,
            filing.filing_date,
            already_known,
            EdgarToolsSyncState.PARSER_UNQUALIFIED,
            tuple(evidence_ids),
            "documents retained; no filing-specific parser is qualified for publication",
        )

    def sync_company(
        self,
        company: EdgarToolsCompany,
        *,
        since: date | None = None,
        dry_run: bool = False,
        known_accessions: frozenset[str] = frozenset(),
    ) -> EdgarToolsSyncSummary:
        """Discover with an overlap window and retain each provider response deterministically."""
        overlap_start = since - timedelta(days=_OVERLAP_DAYS) if since is not None else None
        metadata_evidence: list[str] = []
        filing_results: list[FilingSyncResult] = []
        discovered_count = 0
        eligible: list[FilingSummary] = []
        try:
            company_response = self._client.get_company(company.ticker)
            metadata_evidence.append(self._retain_json(company_response))
            if company_response.value.cik != company.cik:
                message = "EdgarTools company resolution did not match the governed CIK"
                raise EdgarToolsSchemaError(
                    message,
                    endpoint=f"/companies/{company.ticker}",
                    metadata=company_response.metadata,
                )
            page = self._client.list_company_filings(
                company.cik,
                page=1,
                limit=_MAX_FILINGS_PER_SYNC,
            )
            metadata_evidence.append(self._retain_json(page))
            discovered_count = len(page.value.filings)
            eligible = [
                filing
                for filing in page.value.filings
                if filing.form.upper() in _ELIGIBLE_FORMS
                and (
                    overlap_start is None or date.fromisoformat(filing.filing_date) >= overlap_start
                )
            ]
            filing_results.extend(
                self._sync_filing(
                    filing,
                    already_known=filing.accession_number in known_accessions,
                    dry_run=dry_run,
                )
                for filing in eligible
            )
        except EdgarToolsError as error:
            state, safe_detail = _classify_error(error)
            filing_results.append(
                FilingSyncResult(
                    accession_number="",
                    form="",
                    filing_date="",
                    already_known=False,
                    state=state,
                    retained_evidence_ids=(),
                    safe_detail=safe_detail,
                )
            )
        except ValueError:
            filing_results.append(
                FilingSyncResult(
                    accession_number="",
                    form="",
                    filing_date="",
                    already_known=False,
                    state=EdgarToolsSyncState.FAILED,
                    retained_evidence_ids=(),
                    safe_detail="provider filing date was invalid",
                )
            )

        states = {result.state for result in filing_results}
        terminal_state = (
            EdgarToolsSyncState.DISCOVERED
            if not states
            else next(
                state
                for state in EdgarToolsSyncState
                if state in states and state is not EdgarToolsSyncState.DISCOVERED
            )
        )
        return EdgarToolsSyncSummary(
            company_id=company.company_id,
            ticker=company.ticker,
            cik=company.cik,
            dry_run=dry_run,
            overlap_start=overlap_start.isoformat() if overlap_start else None,
            discovered_count=discovered_count,
            eligible_count=len(eligible),
            filing_results=tuple(filing_results),
            retained_metadata_evidence_ids=tuple(metadata_evidence),
            terminal_state=terminal_state,
        )
