"""Content-addressed evidence retained from the EdgarTools provider proxy."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from mortgage_servicing_dashboard.edgar_tools import ProviderResponse

T = TypeVar("T")


class EdgarToolsEvidenceError(RuntimeError):
    """Provider evidence could not be retained or verified safely."""


class EdgarToolsEvidenceType(StrEnum):
    """Honest labels for bytes returned by the hosted provider."""

    API_JSON = "EDGAR_TOOLS_API_JSON"
    DOCUMENT_BYTES = "EDGAR_TOOLS_DOCUMENT_BYTES"


@dataclass(frozen=True, slots=True)
class EdgarToolsDocumentLineage:
    """Filing and document identity reported through EdgarTools."""

    cik: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str | None = None
    filename: str | None = None
    document_type: str | None = None
    sequence: int | None = None
    provider_reported_sec_url: str | None = None


@dataclass(frozen=True, slots=True)
class RetainedEdgarToolsEvidence:
    """Immutable provider-response identity and resolvable local retention reference."""

    evidence_id: str
    evidence_type: EdgarToolsEvidenceType
    content_sha256: str
    byte_length: int
    media_type: str
    endpoint: str
    safe_params: tuple[tuple[str, str], ...]
    retrieved_at: str
    response_status: int
    rate_limit_limit: str | None
    rate_limit_remaining: str | None
    tier: str | None
    pagination: tuple[tuple[str, str], ...]
    adapter_version: str
    lineage: EdgarToolsDocumentLineage | None
    retention_location: str


class EdgarToolsEvidenceStore:
    """Retain exact provider responses below an application-owned content root."""

    def __init__(self, root: Path) -> None:
        """Create or open the ignored runtime evidence directory."""
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Return the resolved application-owned root."""
        return self._root

    def retain(
        self,
        response: ProviderResponse[T],
        *,
        evidence_type: EdgarToolsEvidenceType,
        lineage: EdgarToolsDocumentLineage | None = None,
    ) -> RetainedEdgarToolsEvidence:
        """Atomically retain exact bytes after checking provider metadata integrity."""
        content = response.raw_bytes
        digest = hashlib.sha256(content).hexdigest()
        metadata = response.metadata
        if not content or digest != metadata.sha256 or len(content) != metadata.byte_length:
            message = "EdgarTools response metadata does not match exact response bytes"
            raise EdgarToolsEvidenceError(message)
        if evidence_type is EdgarToolsEvidenceType.DOCUMENT_BYTES and lineage is None:
            message = "EdgarTools document evidence requires filing and document lineage"
            raise EdgarToolsEvidenceError(message)

        directory = self._root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.bin"
        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as error:
                message = "retained EdgarTools evidence is unreadable"
                raise EdgarToolsEvidenceError(message) from error
            if existing != content:
                message = "content-addressed EdgarTools evidence collision"
                raise EdgarToolsEvidenceError(message)
        else:
            temporary = directory / f".{digest}.{os.getpid()}.tmp"
            try:
                temporary.write_bytes(content)
                temporary.replace(target)
            except OSError as error:
                message = "EdgarTools evidence could not be retained atomically"
                raise EdgarToolsEvidenceError(message) from error
            finally:
                if temporary.exists():
                    temporary.unlink()

        return RetainedEdgarToolsEvidence(
            evidence_id=f"edgar-tools:{digest}",
            evidence_type=evidence_type,
            content_sha256=digest,
            byte_length=len(content),
            media_type=metadata.content_type,
            endpoint=metadata.endpoint,
            safe_params=metadata.safe_params,
            retrieved_at=metadata.retrieved_at.isoformat(),
            response_status=metadata.status_code,
            rate_limit_limit=metadata.rate_limit_limit,
            rate_limit_remaining=metadata.rate_limit_remaining,
            tier=metadata.tier,
            pagination=metadata.pagination,
            adapter_version=metadata.adapter_version,
            lineage=lineage,
            retention_location=f"content-sha256://{digest}",
        )

    def verify(self, evidence: RetainedEdgarToolsEvidence) -> bytes:
        """Resolve and verify one retained response using its content identity only."""
        if evidence.evidence_id != f"edgar-tools:{evidence.content_sha256}":
            message = "EdgarTools evidence identifier does not match its content identity"
            raise EdgarToolsEvidenceError(message)
        target = self._root / evidence.content_sha256[:2] / f"{evidence.content_sha256}.bin"
        try:
            if target.resolve().parent != (self._root / evidence.content_sha256[:2]).resolve():
                message = "EdgarTools evidence path escaped its retention root"
                raise EdgarToolsEvidenceError(message)
            content = target.read_bytes()
        except OSError as error:
            message = "retained EdgarTools evidence is unavailable"
            raise EdgarToolsEvidenceError(message) from error
        if (
            len(content) != evidence.byte_length
            or hashlib.sha256(content).hexdigest() != evidence.content_sha256
        ):
            message = "retained EdgarTools evidence failed integrity verification"
            raise EdgarToolsEvidenceError(message)
        return content
