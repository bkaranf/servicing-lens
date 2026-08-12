"""Persistence contracts joining the Phase 2 adapters to replayable raw facts."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    RawRegulatoryFact,
    RawXbrlFact,
    SourceEvidence,
    create_database_engine,
)
from mortgage_servicing_dashboard.regulatory import (
    FrY9cBulkAdapter,
    load_regulatory_config,
)
from mortgage_servicing_dashboard.repository import (
    config_directory,
    ingest_live_sec_acquisitions,
    load_stage_a_configuration,
    persist_regulatory_facts,
    persist_xbrl_facts,
    seed_stage_a,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    ContentAddressedEvidenceStore,
    LiveSecAcquisition,
    RecordedSourceDefinition,
    SecFilingMetadata,
    prepare_live_sec_acquisition,
)
from mortgage_servicing_dashboard.xbrl import SecCompanyFactsAdapter

_ROOT = Path(__file__).resolve().parents[2]


def _synthetic_evidence(
    session: Session,
    *,
    evidence_id: str,
    content: bytes,
    source_class: str,
) -> None:
    digest = hashlib.sha256(content).hexdigest()
    session.add(
        SourceEvidence(
            id=evidence_id,
            source_class=source_class,
            original_url="https://www.sec.gov/Archives/phase2-synthetic-fixture",
            retrieved_at=datetime(2026, 8, 12, tzinfo=UTC),
            published_at=None,
            accession_or_identifier="SYNTHETIC_TEST_DATA",
            content_sha256=digest,
            byte_length=len(content),
            media_type="application/octet-stream",
            representation="SYNTHETIC_TEST_DATA",
            capture_method="test_fixture",
            parser_version="1.0.0",
            acquisition_run_id="fixture-only",
            reporting_entity_candidate="test-only",
            reporting_period_candidate="2026-06-30",
            retention_location=f"test-fixture-sha256://{digest}",
            bounded_excerpt="Synthetic adapter fixture; not a public observation.",
            response_status=None,
            etag=None,
            last_modified=None,
        )
    )


def test_xbrl_context_is_persisted_exactly_and_idempotently(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'xbrl.db').as_posix()}")
    seed_stage_a(engine)
    fixture = (
        _ROOT / "tests" / "fixtures" / "xbrl" / "synthetic_tfc_companyfacts.json"
    ).read_bytes()
    evidence_id = "evidence:synthetic-tfc-companyfacts"
    with Session(engine) as session:
        _synthetic_evidence(
            session,
            evidence_id=evidence_id,
            content=fixture,
            source_class="SYNTHETIC_SEC_COMPANY_FACTS",
        )
        session.commit()
    facts = SecCompanyFactsAdapter().parse(
        fixture,
        issuer_id="tfc",
        evidence_id=evidence_id,
    )
    assert persist_xbrl_facts(engine, facts) == 2
    assert persist_xbrl_facts(engine, facts) == 0
    with Session(engine) as session:
        rows = session.scalars(select(RawXbrlFact).order_by(RawXbrlFact.concept)).all()
    assert len(rows) == 2
    assert all(isinstance(row.scale, Decimal) for row in rows)
    assert all(row.period_type == "instant" and row.instant == date(2026, 6, 30) for row in rows)
    assert all(row.methodology == "SEC_COMPANY_FACTS_XBRL" for row in rows)
    engine.dispose()


def test_regulatory_native_scope_is_persisted_without_sec_blending(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'regulatory.db').as_posix()}")
    seed_stage_a(engine)
    fixture = (
        _ROOT / "tests" / "fixtures" / "phase2" / "regulatory" / "fr_y9c_2026q2.txt"
    ).read_bytes()
    evidence_id = "evidence:synthetic-tfc-y9c"
    with Session(engine) as session:
        _synthetic_evidence(
            session,
            evidence_id=evidence_id,
            content=fixture,
            source_class="SYNTHETIC_FR_Y9C",
        )
        session.commit()
    config = load_regulatory_config(_ROOT / "config" / "regulatory" / "regulatory_mappings.v1.yaml")
    facts = FrY9cBulkAdapter(config).parse(
        fixture,
        rssd_id="1074156",
        report_date=date(2026, 6, 30),
    )
    assert persist_regulatory_facts(engine, facts, evidence_id=evidence_id) == 5
    assert persist_regulatory_facts(engine, facts, evidence_id=evidence_id) == 0
    with Session(engine) as session:
        rows = session.scalars(select(RawRegulatoryFact)).all()
    assert len(rows) == 5
    assert {row.reporting_entity_id for row in rows} == {"tfc_bhc_regulatory_reporter"}
    assert {row.reporting_scope_id for row in rows} == {"tfc_bhc_regulatory"}
    assert {row.source_family for row in rows} == {"FR_Y9C"}
    engine.dispose()


def _live_acquisitions(
    tmp_path: Path,
    *,
    tfc_replacement: tuple[bytes, bytes] | None = None,
    suffix: bytes = b"",
) -> tuple[LiveSecAcquisition, ...]:
    root = config_directory()
    universe, _, data = load_stage_a_configuration(root)
    companies = {str(item["id"]): item for item in universe["companies"]}
    store = ContentAddressedEvidenceStore(tmp_path / "retained")
    acquisitions: list[LiveSecAcquisition] = []
    for key, payload in sorted(data["sources"].items()):
        definition = RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=root,
        )
        content = definition.fixture_path.read_bytes()
        if definition.company_id == "tfc" and tfc_replacement is not None:
            content = content.replace(*tfc_replacement, 1)
        content += suffix
        digest = hashlib.sha256(content).hexdigest()
        document = AcquiredDocument(
            url=definition.url,
            content=content,
            media_type=definition.media_type,
            sha256=digest,
            cache_path=tmp_path / "cache" / f"{digest}.bin",
            retrieved_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )
        retained = store.retain(document)
        company = companies[definition.company_id]
        filename = definition.url.rsplit("/", maxsplit=1)[-1]
        filing = SecFilingMetadata(
            company_id=definition.company_id,
            cik=str(company["cik"]),
            accession=definition.accession,
            form="8-K",
            filing_date=definition.published_at.date(),
            report_date=date.fromisoformat(definition.period_end),
            acceptance_at=definition.published_at,
            primary_document=filename,
            primary_document_url=definition.url,
            items=("2.02", "9.01"),
            is_xbrl=True,
            is_inline_xbrl=True,
        )
        acquisitions.append(
            prepare_live_sec_acquisition(
                source=definition,
                cik=str(company["cik"]),
                discovered_filing=filing,
                acquired_document=document,
                retained_document=retained,
            )
        )
    return tuple(acquisitions)


def test_live_repository_is_idempotent_links_and_quarantines_conflicts(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}")
    first = _live_acquisitions(tmp_path / "first")
    assert ingest_live_sec_acquisitions(engine, first) == {
        "evidence": 2,
        "published": 36,
        "linked": 0,
        "quarantined": 0,
    }
    assert ingest_live_sec_acquisitions(engine, first) == {
        "evidence": 0,
        "published": 0,
        "linked": 0,
        "quarantined": 0,
    }

    corroborating = _live_acquisitions(tmp_path / "corroborating", suffix=b"\n")
    linked = ingest_live_sec_acquisitions(engine, corroborating)
    assert linked == {"evidence": 2, "published": 0, "linked": 36, "quarantined": 0}

    changed = _live_acquisitions(
        tmp_path / "changed",
        tfc_replacement=(b"298,658", b"298,659"),
        suffix=b"\nchanged",
    )
    conflicted = ingest_live_sec_acquisitions(engine, changed)
    assert conflicted["evidence"] == 2
    assert conflicted["published"] == 0
    assert conflicted["linked"] == 35
    assert conflicted["quarantined"] == 1
    with Session(engine) as session:
        live_evidence = session.scalars(
            select(SourceEvidence).where(SourceEvidence.representation == "ORIGINAL_HTTP_RESPONSE")
        ).all()
        assert len(live_evidence) == 6
    engine.dispose()
