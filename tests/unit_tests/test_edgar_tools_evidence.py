from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mortgage_servicing_dashboard.edgar_tools import ProviderResponse, ProviderResponseMetadata
from mortgage_servicing_dashboard.edgar_tools_evidence import (
    EdgarToolsDocumentLineage,
    EdgarToolsEvidenceError,
    EdgarToolsEvidenceStore,
    EdgarToolsEvidenceType,
)


def _response(content: bytes = b'{"status":"ok"}') -> ProviderResponse[object]:
    return ProviderResponse(
        value={"status": "ok"},
        raw_bytes=content,
        metadata=ProviderResponseMetadata(
            endpoint="/health",
            safe_params=(("page", "1"),),
            retrieved_at=datetime(2026, 8, 12, tzinfo=UTC),
            status_code=200,
            content_type="application/json",
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            rate_limit_limit="1000",
            rate_limit_remaining="999",
            rate_limit_reset=None,
            retry_after=None,
            tier="free",
            pagination=(("has_more", "false"),),
        ),
    )


def _lineage() -> EdgarToolsDocumentLineage:
    return EdgarToolsDocumentLineage(
        cik="0000092230",
        accession_number="0000092230-26-000099",
        form="10-Q",
        filing_date="2026-08-05",
        report_date="2026-06-30",
        filename="tfc-20260630.htm",
        document_type="10-Q",
        sequence=1,
        provider_reported_sec_url="https://www.sec.gov/Archives/example",
    )


def test_retain_and_verify_provider_json(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path / ".msi" / "edgar_tools" / "evidence")
    response = _response()
    retained = store.retain(response, evidence_type=EdgarToolsEvidenceType.API_JSON)

    assert store.root.is_absolute()
    assert retained.evidence_type == EdgarToolsEvidenceType.API_JSON
    assert retained.content_sha256 == response.metadata.sha256
    assert retained.evidence_id == f"edgar-tools:{response.metadata.sha256}"
    assert retained.retention_location == f"content-sha256://{response.metadata.sha256}"
    assert retained.endpoint == "/health"
    assert retained.safe_params == (("page", "1"),)
    assert retained.retrieved_at == "2026-08-12T00:00:00+00:00"
    assert retained.rate_limit_remaining == "999"
    assert retained.tier == "free"
    assert retained.lineage is None
    assert store.verify(retained) == response.raw_bytes


def test_document_bytes_require_and_preserve_full_lineage(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    response = _response(b"<html>filing</html>")
    lineage = _lineage()

    retained = store.retain(
        response,
        evidence_type=EdgarToolsEvidenceType.DOCUMENT_BYTES,
        lineage=lineage,
    )

    assert retained.evidence_type.value == "EDGAR_TOOLS_DOCUMENT_BYTES"
    assert retained.lineage == lineage
    assert store.verify(retained) == b"<html>filing</html>"


def test_retain_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    first = store.retain(_response(), evidence_type=EdgarToolsEvidenceType.API_JSON)
    second = store.retain(_response(), evidence_type=EdgarToolsEvidenceType.API_JSON)
    assert first == second


def test_metadata_mismatch_and_empty_content_fail_closed(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    response = _response()
    wrong_hash = replace(response.metadata, sha256="0" * 64)
    wrong_length = replace(response.metadata, byte_length=999)

    with pytest.raises(EdgarToolsEvidenceError, match="metadata"):
        store.retain(
            replace(response, metadata=wrong_hash),
            evidence_type=EdgarToolsEvidenceType.API_JSON,
        )
    with pytest.raises(EdgarToolsEvidenceError, match="metadata"):
        store.retain(
            replace(response, metadata=wrong_length),
            evidence_type=EdgarToolsEvidenceType.API_JSON,
        )
    with pytest.raises(EdgarToolsEvidenceError, match="metadata"):
        store.retain(_response(b""), evidence_type=EdgarToolsEvidenceType.API_JSON)


def test_document_evidence_without_lineage_fails_closed(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    with pytest.raises(EdgarToolsEvidenceError, match="requires filing"):
        store.retain(_response(), evidence_type=EdgarToolsEvidenceType.DOCUMENT_BYTES)


def test_verify_rejects_wrong_identity_missing_and_changed_bytes(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    retained = store.retain(_response(), evidence_type=EdgarToolsEvidenceType.API_JSON)

    with pytest.raises(EdgarToolsEvidenceError, match="identifier"):
        store.verify(replace(retained, evidence_id="edgar-tools:wrong"))

    target = tmp_path / retained.content_sha256[:2] / f"{retained.content_sha256}.bin"
    target.unlink()
    with pytest.raises(EdgarToolsEvidenceError, match="unavailable"):
        store.verify(retained)

    target.write_bytes(b"changed")
    with pytest.raises(EdgarToolsEvidenceError, match="integrity"):
        store.verify(retained)


def test_existing_collision_is_rejected(tmp_path: Path) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    response = _response()
    digest = response.metadata.sha256
    directory = tmp_path / digest[:2]
    directory.mkdir(parents=True)
    (directory / f"{digest}.bin").write_bytes(b"different")

    with pytest.raises(EdgarToolsEvidenceError, match="collision"):
        store.retain(response, evidence_type=EdgarToolsEvidenceType.API_JSON)


def test_unreadable_existing_and_atomic_write_failures_are_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EdgarToolsEvidenceStore(tmp_path)
    response = _response()
    digest = response.metadata.sha256
    directory = tmp_path / digest[:2]
    directory.mkdir(parents=True)
    target = directory / f"{digest}.bin"
    target.mkdir()
    with pytest.raises(EdgarToolsEvidenceError, match="unreadable"):
        store.retain(response, evidence_type=EdgarToolsEvidenceType.API_JSON)

    target.rmdir()
    monkeypatch.setattr(Path, "write_bytes", lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(EdgarToolsEvidenceError, match="atomically"):
        store.retain(response, evidence_type=EdgarToolsEvidenceType.API_JSON)
