"""Atomic persistence contracts for coordinator-validated edgartools facts."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

import mortgage_servicing_dashboard.repository as repository_module
from mortgage_servicing_dashboard.database import (
    Company,
    Filing,
    FilingDocument,
    MetricDefinition,
    MetricObservation,
    ObservationEvidence,
    ObservationRevision,
    PipelineRun,
    QuarantineCandidate,
    RawXbrlFact,
    SourceEvidence,
    create_database_engine,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    CommittedCaseState,
    ValidatedFiling,
)
from mortgage_servicing_dashboard.financial_discovery import FinancialClassification
from mortgage_servicing_dashboard.repository import AtomicEdgarToolsRepository


def _validated(  # noqa: PLR0913 - fixture exposes the governed filing identity.
    case_id: str,
    *,
    company_id: str = "tfc",
    accession: str = "0000092230-26-000030",
    form: str = "10-K",
    filed: date = date(2026, 2, 24),
    period: date = date(2025, 12, 31),
    fiscal_year: int = 2025,
    fiscal_quarter: str = "FY",
    amendment: bool = False,
    revision_of: str | None = None,
    raw: str = "547,538",
    value: Decimal = Decimal(547538000000),
) -> ValidatedFiling:
    cik = "0000092230" if company_id == "tfc" else "0001745916"
    entity = f"{company_id}_registrant"
    filename = f"{company_id}-{accession[-6:]}.htm"
    content_hash = hashlib.sha256(case_id.encode()).hexdigest()
    return ValidatedFiling(
        case_id=case_id,
        mapping_version="financial-fields-v1",
        company_id=company_id,
        cik=cik,
        accession_number=accession,
        form=form,
        filing_date=filed,
        report_period=period,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        amendment=amendment,
        revision_of_accession=revision_of,
        primary_document=filename,
        source_url=(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{filename}"
        ),
        evidence_sha256=content_hash,
        evidence_byte_length=len(case_id.encode()),
        evidence_location=f"content-sha256://{content_hash}",
        evidence_retrieved_at=datetime(2026, 8, 13, 12, tzinfo=UTC),
        evidence_representation="EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8",
        evidence_capture_method="edgartools_attachment_text_utf8",
        evidence_media_type="text/html; charset=utf-8",
        field_id="total_assets",
        classification=FinancialClassification.CORE_FINANCIAL,
        reporting_entity_id=entity,
        reporting_scope_id=f"{company_id}_consolidated_company",
        qualified_concept="us-gaap:Assets",
        original_label="Total assets",
        raw_display_string=raw,
        normalized_value=value,
        context_ref="c-assets",
        unit="USD",
        decimals="-6" if company_id == "tfc" else "-3",
        source_scale=Decimal(1000000) if company_id == "tfc" else Decimal(1000),
        source_element_ids=("f-assets",),
        source_object_count=1,
        source_locators=("inline-xbrl#f-assets",),
    )


def _engine(tmp_path: Path, name: str) -> Engine:
    return create_database_engine(f"sqlite:///{(tmp_path / name).as_posix()}")


def test_atomic_publication_bootstraps_only_selected_financial_structure_and_lineage(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "lineage.db")
    persistence = AtomicEdgarToolsRepository(engine)
    item = _validated("tfc-annual")

    committed = persistence.persist_atomically((item,))
    assert committed.outcomes[0].state is CommittedCaseState.PUBLISHED
    assert persistence.last_result is not None
    assert persistence.last_result.as_payload() == {
        "evidence": 1,
        "filings": 1,
        "documents": 1,
        "raw_facts": 1,
        "observations": 1,
        "revisions": 1,
        "linked": 0,
        "quarantined": 0,
    }
    with Session(engine) as session:
        assert set(session.scalars(select(Company.id))) == {"tfc"}
        assert set(session.scalars(select(MetricDefinition.id))) == {"total_assets"}
        evidence = session.scalar(select(SourceEvidence))
        document = session.scalar(select(FilingDocument))
        fact = session.scalar(select(RawXbrlFact))
        observation = session.scalar(select(MetricObservation))
        link = session.scalar(select(ObservationEvidence))
        revision = session.scalar(select(ObservationRevision))
        assert evidence is not None
        assert evidence.content_sha256 == item.evidence_sha256
        assert document is not None
        assert document.source_evidence_id == evidence.id
        assert document.is_primary is True
        assert fact is not None
        assert fact.evidence_id == evidence.id
        assert fact.raw_value == item.raw_display_string
        assert observation is not None
        assert observation.value == item.normalized_value
        assert observation.fiscal_quarter == 0
        assert observation.parser_metadata["fiscal_quarter"] == "FY"
        assert link is not None
        assert link.evidence_id == evidence.id
        assert revision is not None
        assert revision.observation_id == observation.id
    engine.dispose()


def test_atomic_publication_is_idempotent_without_mutating_completed_run(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "idempotent.db")
    persistence = AtomicEdgarToolsRepository(engine)
    item = _validated("tfc-idempotent")
    first_result = persistence.persist_atomically((item,))
    assert first_result.outcomes[0].state is CommittedCaseState.PUBLISHED
    with Session(engine) as session:
        first = session.scalar(select(PipelineRun))
        assert first is not None
        completed_at = first.completed_at
        status = first.status
        outcomes = dict(first.terminal_outcomes)

    unchanged = persistence.persist_atomically((item,))
    assert unchanged.outcomes[0].state is CommittedCaseState.UNCHANGED
    assert persistence.last_result is not None
    assert persistence.last_result.as_payload() == dict.fromkeys(
        persistence.last_result.as_payload(), 0
    )
    with Session(engine) as session:
        second = session.scalar(select(PipelineRun))
        assert second is not None
        assert second.completed_at == completed_at
        assert second.status == status
        assert second.terminal_outcomes == outcomes
        assert session.scalar(select(func.count(MetricObservation.id))) == 1
    engine.dispose()


def test_equal_overlap_returns_linked_outcome_and_known_accessions(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "linked.db")
    persistence = AtomicEdgarToolsRepository(engine)
    original = _validated("tfc-linked-original")
    corroborating = _validated(
        "tfc-linked-corroborating",
        accession="0000092230-26-000088",
        filed=date(2026, 3, 3),
    )

    persistence.persist_atomically((original,))
    linked = persistence.persist_atomically((corroborating,))

    assert linked.outcomes[0].state is CommittedCaseState.LINKED
    assert linked.linked == 1
    assert linked.observations == 0
    assert persistence.known_accessions("tfc") == frozenset(
        {original.accession_number, corroborating.accession_number}
    )
    assert persistence.known_accessions("pfsi") == frozenset()
    engine.dispose()


@pytest.mark.parametrize(
    ("attribute", "mismatch"),
    [
        ("source_class", "SEC_XBRL_INSTANCE_VIA_EDGARTOOLS"),
        ("original_url", "https://www.sec.gov/incompatible"),
        ("representation", "ORIGINAL_HTTP_RESPONSE"),
        ("capture_method", "incompatible_capture"),
        ("retention_location", "content-sha256://incompatible"),
        ("media_type", "application/xml"),
    ],
)
def test_evidence_hash_reuse_requires_exact_source_and_media_identity(
    tmp_path: Path,
    attribute: str,
    mismatch: str,
) -> None:
    engine = _engine(tmp_path, f"evidence-identity-{attribute}.db")
    persistence = AtomicEdgarToolsRepository(engine)
    item = _validated(f"evidence-identity-{attribute}")
    persistence.persist_atomically((item,))
    with Session(engine) as session, session.begin():
        evidence = session.scalar(select(SourceEvidence))
        assert evidence is not None
        setattr(evidence, attribute, mismatch)

    with pytest.raises(ValueError, match="incompatible source and media identity"):
        persistence.persist_atomically((item,))
    engine.dispose()


def test_amendment_creates_immutable_successor_and_preserves_fy_from_q4(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "amendment.db")
    persistence = AtomicEdgarToolsRepository(engine)
    original = _validated("tfc-original")
    amended = _validated(
        "tfc-amended",
        accession="0000092230-26-000099",
        form="10-K/A",
        filed=date(2026, 3, 4),
        amendment=True,
        revision_of=original.accession_number,
        raw="547,539",
        value=Decimal(547539000000),
    )
    quarter = _validated(
        "pfsi-q4",
        company_id="pfsi",
        accession="0001745916-25-000077",
        form="10-Q",
        filed=date(2025, 11, 7),
        period=date(2025, 9, 30),
        fiscal_year=2025,
        fiscal_quarter="Q4",
        raw="39000000",
        value=Decimal(39000000),
    )
    persistence.persist_atomically((original, quarter))
    persistence.persist_atomically((amended,))

    with Session(engine) as session:
        tfc = session.scalars(
            select(MetricObservation)
            .where(MetricObservation.reporting_entity_id == "tfc_registrant")
            .order_by(MetricObservation.revision_number)
        ).all()
        pfsi = session.scalar(
            select(MetricObservation).where(
                MetricObservation.reporting_entity_id == "pfsi_registrant"
            )
        )
        assert len(tfc) == 2
        assert tfc[0].publication_state == "SUPERSEDED"
        assert tfc[0].knowledge_to is not None
        assert tfc[1].supersedes_observation_id == tfc[0].id
        assert tfc[1].revision_number == 2
        assert tfc[0].value == original.normalized_value
        assert tfc[1].value == amended.normalized_value
        assert tfc[1].fiscal_quarter == 0
        assert tfc[1].parser_metadata["fiscal_quarter"] == "FY"
        assert pfsi is not None
        assert pfsi.fiscal_quarter == 4
        assert pfsi.parser_metadata["fiscal_quarter"] == "Q4"
        amended_filing = session.scalar(
            select(Filing).where(Filing.accession == amended.accession_number)
        )
        assert amended_filing is not None
        assert amended_filing.amendment_of_id is not None
    engine.dispose()


def test_overlap_conflict_quarantines_without_overwriting_public_value(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "conflict.db")
    persistence = AtomicEdgarToolsRepository(engine)
    original = _validated("tfc-conflict-original")
    conflict = _validated(
        "tfc-conflict-later",
        accession="0000092230-26-000088",
        filed=date(2026, 3, 3),
        raw="547,999",
        value=Decimal(547999000000),
    )
    persistence.persist_atomically((original,))
    conflict_result = persistence.persist_atomically((conflict,))
    assert conflict_result.outcomes[0].state is CommittedCaseState.QUARANTINED
    assert persistence.last_result is not None
    assert persistence.last_result.quarantined == 1
    assert persistence.last_result.observations == 0
    replay = persistence.persist_atomically((conflict,))
    assert replay.outcomes[0].state is CommittedCaseState.UNCHANGED
    assert replay.quarantined == 0
    with Session(engine) as session:
        observations = session.scalars(select(MetricObservation)).all()
        candidate = session.scalar(select(QuarantineCandidate))
        assert len(observations) == 1
        assert observations[0].value == original.normalized_value
        assert candidate is not None
        assert candidate.status == "OVERLAPPING_FACT_CONFLICT"
        assert candidate.evidence_id is not None
    engine.dispose()


def test_amendment_without_active_prior_rolls_back_every_application_row(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "rollback.db")
    persistence = AtomicEdgarToolsRepository(engine)
    amendment = _validated(
        "orphan-amendment",
        accession="0000092230-26-000099",
        form="10-K/A",
        amendment=True,
        revision_of="0000092230-26-000030",
    )
    with pytest.raises(ValueError, match="unknown prior accession"):
        persistence.persist_atomically((amendment,))
    with Session(engine) as session:
        assert session.scalar(select(func.count(Company.id))) == 0
        assert session.scalar(select(func.count(SourceEvidence.id))) == 0
        assert session.scalar(select(func.count(MetricObservation.id))) == 0
    engine.dispose()


def test_atomic_failure_rolls_back_first_item_when_second_item_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(tmp_path, "forced-rollback.db")
    persistence = AtomicEdgarToolsRepository(engine)
    first = _validated("tfc-first")
    second = _validated(
        "pfsi-second",
        company_id="pfsi",
        accession="0001745916-26-000020",
        form="10-Q",
        filed=date(2026, 5, 8),
        period=date(2026, 3, 31),
        fiscal_year=2026,
        fiscal_quarter="Q1",
        raw="40000000",
        value=Decimal(40000000),
    )
    real = repository_module._persist_validated_filing

    def fail_second(*args: object, **kwargs: object) -> None:
        item = args[1]
        assert isinstance(item, ValidatedFiling)
        if item.case_id == second.case_id:
            message = "forced transaction failure"
            raise RuntimeError(message)
        real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_module, "_persist_validated_filing", fail_second)
    with pytest.raises(RuntimeError, match="forced transaction failure"):
        persistence.persist_atomically((first, second))
    with Session(engine) as session:
        assert session.scalar(select(func.count(Company.id))) == 0
        assert session.scalar(select(func.count(SourceEvidence.id))) == 0
    engine.dispose()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"evidence_sha256": "A" * 64}, "evidence identity"),
        ({"source_scale": Decimal("NaN")}, "source scale"),
        ({"amendment": True, "revision_of_accession": "prior"}, "form suffix"),
        ({"evidence_representation": "ORIGINAL_HTTP_RESPONSE"}, "representation"),
        ({"evidence_capture_method": "sec_http_get"}, "capture method"),
    ],
)
def test_invalid_lineage_fails_before_schema_write(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    engine = _engine(tmp_path, f"invalid-{message.replace(' ', '-')}.db")
    persistence = AtomicEdgarToolsRepository(engine)
    invalid = replace(_validated("invalid"), **change)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        persistence.persist_atomically((invalid,))
    assert not (tmp_path / f"invalid-{message.replace(' ', '-')}.db").exists()
    engine.dispose()
