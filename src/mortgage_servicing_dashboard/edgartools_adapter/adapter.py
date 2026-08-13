"""Thin injectable facade over the public-edgartools acquisition backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from mortgage_servicing_dashboard.edgartools_adapter.backend import (
    EdgarToolsBackend,
    FilingDateFilter,
    PublicEdgarToolsBackend,
)
from mortgage_servicing_dashboard.edgartools_adapter.bootstrap import (
    EdgarBootstrap,
    EdgarBootstrapConfig,
)
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    Attachment,
    AttachmentAcquisition,
    Company,
    CompanyFactsDiscovery,
    Filing,
    FilingStructure,
    XbrlFiling,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterConfigurationError,
    AdapterState,
)
from mortgage_servicing_dashboard.edgartools_adapter.retention import (
    EvidenceStore,
    GeneralEvidenceStore,
)

_DEFAULT_EVIDENCE_PATH = Path(".msi") / "evidence" / "edgartools"


class EdgarToolsAdapter:
    """Application-facing adapter with injectable acquisition and retention seams."""

    def __init__(
        self,
        backend: EdgarToolsBackend,
        *,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        """Store dependencies without importing edgartools or opening a network."""
        self._backend = backend
        self._evidence_store = evidence_store

    @classmethod
    def from_config(
        cls,
        config: EdgarBootstrapConfig,
        *,
        evidence_store: EvidenceStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> EdgarToolsAdapter:
        """Build the lazy production boundary from secret-bearing bootstrap config."""
        backend = PublicEdgarToolsBackend(EdgarBootstrap(config), clock=clock)
        store = (
            evidence_store
            if evidence_store is not None
            else GeneralEvidenceStore((config.repository_root / _DEFAULT_EVIDENCE_PATH).resolve())
        )
        return cls(backend, evidence_store=store)

    def company(self, cik_or_ticker: str) -> Company:
        """Resolve an exact CIK or ticker while returning CIK as stable identity."""
        return self._backend.resolve_company(cik_or_ticker)

    def filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: FilingDateFilter = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        """List one company's filings with explicit form, date, and amendment rules."""
        return self._backend.list_filings(
            cik,
            forms=forms,
            filing_date=filing_date,
            include_amendments=include_amendments,
        )

    def filing(self, accession: str, *, expected_cik: str | None = None) -> Filing:
        """Return one exact filing by canonical accession number."""
        return self._backend.get_filing(accession, expected_cik=expected_cik)

    def attachments(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        """Enumerate primary documents, attachments, and exhibits for a filing."""
        return self._backend.list_attachments(accession, expected_cik=expected_cik)

    def acquire_attachment(
        self,
        accession: str,
        document: str,
        *,
        expected_cik: str | None = None,
        retain: bool = True,
    ) -> AttachmentAcquisition:
        """Acquire exact attachment content and optionally retain its canonical bytes."""
        result = self._backend.acquire_attachment(
            accession,
            document,
            expected_cik=expected_cik,
        )
        if not retain:
            return result
        if self._evidence_store is None:
            message = "evidence retention was requested without an evidence store"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="acquire_attachment",
            )
        return replace(result, retained=self._evidence_store.retain(result.content))

    def filing_xbrl(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> XbrlFiling | None:
        """Return filing-specific raw facts and registries, never convenience numerics."""
        return self._backend.get_filing_xbrl(accession, expected_cik=expected_cik)

    def company_facts(self, cik: str) -> CompanyFactsDiscovery | None:
        """Return Company Facts candidates for discovery and cross-checking only."""
        return self._backend.get_company_facts(cik)

    def filing_structure(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> FilingStructure | None:
        """Return linkbases, footnotes, and viewer validation metadata."""
        return self._backend.get_filing_structure(accession, expected_cik=expected_cik)
