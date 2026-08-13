"""Reuse the application's immutable content-addressed evidence store."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    AcquiredContent,
    ContentRepresentation,
    RetainedContent,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterIntegrityError,
    AdapterState,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    ContentAddressedEvidenceStore,
    PublicSourceError,
)


class EvidenceStore(Protocol):
    """Injectable retention seam for offline unit tests."""

    def retain(self, content: AcquiredContent) -> RetainedContent:
        """Retain and return an immutable content identity."""
        ...


class GeneralEvidenceStore:
    """Representation-aware wrapper over the existing general evidence store."""

    def __init__(self, root: Path) -> None:
        """Create a content-addressed store below the application-owned root."""
        self._store = ContentAddressedEvidenceStore(root)

    def retain(self, content: AcquiredContent) -> RetainedContent:
        """Verify and retain represented bytes without relabeling their origin."""
        _verify_acquired_content(content)
        document = AcquiredDocument(
            url=content.source_url,
            content=content.content,
            media_type=content.media_type,
            sha256=content.sha256,
            cache_path=Path("edgartools-library-memory"),
            retrieved_at=content.retrieved_at,
        )
        try:
            retained = self._store.retain(document)
            verified = self._store.verify(retained)
        except (OSError, PublicSourceError) as error:
            message = "edgartools content could not be retained immutably"
            raise AdapterIntegrityError(
                message,
                state=AdapterState.INTEGRITY_ERROR,
                operation="retain_attachment",
            ) from error
        if verified != content.content:
            message = "retained edgartools content failed integrity verification"
            raise AdapterIntegrityError(
                message,
                state=AdapterState.INTEGRITY_ERROR,
                operation="retain_attachment",
            )
        return RetainedContent(
            content_sha256=retained.sha256,
            byte_length=retained.byte_length,
            retention_location=f"content-sha256://{retained.sha256}",
            retained_at=retained.retained_at.astimezone(UTC)
            if retained.retained_at.tzinfo
            else retained.retained_at.replace(tzinfo=UTC),
            representation=content.representation,
            capture_method=content.capture_method,
            media_type=content.media_type,
            source_url=content.source_url,
        )


def utc_now() -> datetime:
    """Return an injectable-compatible UTC acquisition timestamp."""
    return datetime.now(UTC)


def _verify_acquired_content(content: AcquiredContent) -> None:
    digest = hashlib.sha256(content.content).hexdigest()
    if not hmac.compare_digest(digest, content.sha256):
        message = "edgartools content hash did not match its represented bytes"
        raise AdapterIntegrityError(
            message,
            state=AdapterState.INTEGRITY_ERROR,
            operation="retain_attachment",
        )
    if content.representation is ContentRepresentation.LIBRARY_TEXT_UTF8:
        try:
            content.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            message = "canonical edgartools text was not valid UTF-8"
            raise AdapterIntegrityError(
                message,
                state=AdapterState.INTEGRITY_ERROR,
                operation="retain_attachment",
            ) from error
