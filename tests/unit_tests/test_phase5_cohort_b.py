"""Offline acceptance for the exact five-bank/five-nonbank Phase 5 cohort B."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from fastapi import HTTPException
from fastapi.routing import APIRoute
from scripts.phase5_replay import build_outputs, check_outputs
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.cli import _edgar_companies, build_parser
from mortgage_servicing_dashboard.database import (
    Company as CompanyRow,
)
from mortgage_servicing_dashboard.database import Filing as FilingRow
from mortgage_servicing_dashboard.database import (
    FilingDocument,
    MetricDefinition,
    MetricDefinitionVersion,
    MetricObservation,
    ObservationEvidence,
    ReportingEntity,
    ReportingScope,
    Security,
    SourceEvidence,
    create_database_engine,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncPipeline,
    EdgarToolsSyncState,
)
from mortgage_servicing_dashboard.edgartools_adapter.adapter import EdgarToolsAdapter
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    AcquiredContent,
    Attachment,
    AttachmentAcquisition,
    Company,
    ContentRepresentation,
    Filing,
    RetainedContent,
)
from mortgage_servicing_dashboard.financial_discovery import FinancialFieldRegistry
from mortgage_servicing_dashboard.presentation import CompanyIdentity, normalize_companies
from mortgage_servicing_dashboard.repository import (
    AtomicEdgarToolsRepository,
    EdgarToolsCompanyIdentity,
    IntelligenceRepository,
    seed_phase3,
)
from tests.unit_tests.test_phase5_cohort_a import _endpoint, _request

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _ROOT / "config" / "phase5" / "cohort-b-sources.v1.yaml"
_REPLAY_PATH = _ROOT / "config" / "phase5" / "cohort-b-replay.v1.yaml"
_REGISTRY_PATH = _ROOT / "config" / "phase5" / "financial_fields.v1.yaml"
_UNIVERSE_PATH = _ROOT / "config" / "phase5" / "cohort-b-universe.v1.yaml"
_EVIDENCE_CASES_PATH = _ROOT / "config" / "phase5" / "evidence-cases.v1.yaml"
_REPLAY_INDEX_PATH = _ROOT / "tests" / "fixtures" / "phase5" / "replay-index.v1.yaml"
_LEGACY_BASELINE_PATH = _ROOT / "config" / "audit" / "legacy-439-baseline.csv"
_EXPECTED_SNAPSHOT_HASH = "4494c97fa6cd8dfe6bffcf9f8fdc55eaf8211ac0ef0c81106299d60868c5dae2"
_EXPECTED_REPLAY_INDEX_HASH = "e9b984f54a3f47df60526a894f1cb103ac8856eb9bd21972dc098fb019b52b72"
_EXPECTED_EVIDENCE_CASES_HASH = "e03f350aea4dee6f56669b78ae71fc129e8423258a8dd5b2c234cd7a6747b06e"
_EXPECTED_REPLAY_MANIFEST_HASH = "0fa65bce18b399a7641e0823693495ffaa1c9fc35e930217da268f19d693010b"
_EXPECTED_COHORT_A_UNIVERSE_HASH = (
    "3abfdb587b0bfdad79edddf7d783d763d8c328f782ae05fc770df07b9e8a6b8e"
)
_EXPECTED_COHORT_B_UNIVERSE_HASH = (
    "5212aa8007ff80175bca7898271c575b2f336348da73503c7df925f30a4922c9"
)
_EXPECTED_SUPPORTED_UNIVERSE_HASH = (
    "b6920b7936a3fb5cdc7dabc2a095dbb690b1cbceb7610c133c948a2b2a0495dd"
)
_EXPECTED_LEGACY_BASELINE_HASH = "112661f7d3414793f747c6cdd9a890f480a2f98768bb8268cae9ad70c2e3f0b2"
_EXPECTED_LEGACY_EVIDENCE_HASH = "bb80fd7ee5fc5c081dc4741cf1a33c7686211a05ccfc351412e819aae879c981"
_NOW = datetime(2026, 8, 18, 16, tzinfo=UTC)
_ISSUER_ORDER = ("tfc", "wfc", "jpm", "bac", "usb", "pfsi", "rkt", "uwmc", "ritm", "ldi")
_COMPANIES = {
    "tfc": EdgarToolsCompanyIdentity(
        "Truist Financial Corporation", "TFC", "bank", "0000092230", "tfc_registrant"
    ),
    "wfc": EdgarToolsCompanyIdentity(
        "Wells Fargo & Company", "WFC", "bank", "0000072971", "wfc_registrant"
    ),
    "jpm": EdgarToolsCompanyIdentity(
        "JPMorgan Chase & Co.", "JPM", "bank", "0000019617", "jpm_registrant"
    ),
    "bac": EdgarToolsCompanyIdentity(
        "Bank of America Corporation", "BAC", "bank", "0000070858", "bac_registrant"
    ),
    "usb": EdgarToolsCompanyIdentity("U.S. Bancorp", "USB", "bank", "0000036104", "usb_registrant"),
    "pfsi": EdgarToolsCompanyIdentity(
        "PennyMac Financial Services, Inc.",
        "PFSI",
        "nonbank",
        "0001745916",
        "pfsi_registrant",
    ),
    "rkt": EdgarToolsCompanyIdentity(
        "Rocket Companies, Inc.", "RKT", "nonbank", "0001805284", "rkt_registrant"
    ),
    "uwmc": EdgarToolsCompanyIdentity(
        "UWM Holdings Corp", "UWMC", "nonbank", "0001783398", "uwmc_registrant"
    ),
    "ritm": EdgarToolsCompanyIdentity(
        "Rithm Capital Corp.", "RITM", "nonbank", "0001556593", "ritm_registrant"
    ),
    "ldi": EdgarToolsCompanyIdentity(
        "loanDepot, Inc.", "LDI", "nonbank", "0001831631", "ldi_registrant"
    ),
}


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


class _ReplayAdapter(EdgarToolsAdapter):
    """Socket-free adapter that exposes tracked replay excerpts as acquisitions."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        cases = cast("list[dict[str, Any]]", manifest["cases"])
        self.companies = {
            identity.cik: Company(identity.cik, identity.legal_name, (identity.ticker,))
            for identity in _COMPANIES.values()
        }
        self.filings_by_cik: dict[str, list[Filing]] = {}
        self.attachments_by_accession: dict[str, tuple[Attachment, ...]] = {}
        self.payload_by_source: dict[tuple[str, str], bytes] = {}
        self.source_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        by_accession: dict[str, dict[str, Any]] = {}
        for case in cases:
            by_accession.setdefault(str(case["accession"]), case)
        for accession, case in by_accession.items():
            cik = str(case["cik"]).zfill(10)
            source = cast("dict[str, Any]", case["edgartools_source"])
            source_document = str(case["source_document"])
            payload = (_ROOT / str(source["fixture_path"])).read_bytes()
            self.payload_by_source[(accession, source_document)] = payload
            self.source_by_key[(accession, source_document)] = source
            filing = Filing(
                cik=cik,
                accession_number=accession,
                company_name=_COMPANIES[str(case["issuer_id"])].legal_name,
                form=str(case["form"]),
                filing_date=date.fromisoformat(str(case["filing_date"])),
                acceptance_timestamp=_NOW,
                report_period=date.fromisoformat(str(case["period_end"])),
                primary_document=str(case["primary_document"]),
                amendment=bool(case["amendment"]),
                is_xbrl=True,
                is_inline_xbrl=True,
                size=None,
                homepage_url=f"https://www.sec.gov/Archives/{accession}",
                text_url=f"https://www.sec.gov/Archives/{accession}.txt",
            )
            self.filings_by_cik.setdefault(cik, []).append(filing)
            primary = Attachment(
                cik=cik,
                accession_number=accession,
                document=str(case["primary_document"]),
                sequence=str(case["primary_sequence"]),
                description=str(case["primary_description"]),
                attachment_type=str(case["primary_document_type"]),
                size=None,
                source_url=str(case["primary_source_url"]),
                is_primary=True,
                is_binary=False,
            )
            attachments = [primary]
            if source_document != primary.document:
                attachments.append(
                    Attachment(
                        cik=cik,
                        accession_number=accession,
                        document=source_document,
                        sequence=str(case["source_sequence"]),
                        description=str(case["source_description"]),
                        attachment_type=str(case["source_document_type"]),
                        size=len(payload),
                        source_url=str(case["source_url"]),
                        is_primary=False,
                        is_binary=False,
                    )
                )
            self.attachments_by_accession[accession] = tuple(attachments)

    def company(self, cik_or_ticker: str) -> Company:
        return self.companies[cik_or_ticker]

    def filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: date | tuple[date, date] | None = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        del forms, filing_date, include_amendments
        return tuple(self.filings_by_cik[cik])

    def attachments(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        del expected_cik
        return self.attachments_by_accession[accession]

    def acquire_attachment(
        self,
        accession: str,
        document: str,
        *,
        expected_cik: str | None = None,
        retain: bool = True,
    ) -> AttachmentAcquisition:
        del expected_cik
        attachment = next(
            item for item in self.attachments_by_accession[accession] if item.document == document
        )
        payload = self.payload_by_source[(accession, document)]
        source = self.source_by_key[(accession, document)]
        digest = hashlib.sha256(payload).hexdigest()
        assert digest == source["sha256"]
        content = AcquiredContent(
            cik=attachment.cik,
            accession_number=accession,
            document=document,
            source_url=attachment.source_url,
            content=payload,
            media_type="application/xml",
            representation=ContentRepresentation.BOUNDED_REPLAY_EXCERPT,
            capture_method="offline_bounded_xbrl_replay_excerpt",
            sha256=digest,
            retrieved_at=_NOW,
        )
        retained = RetainedContent(
            content_sha256=digest,
            byte_length=len(payload),
            retention_location=f"content-sha256://{digest}",
            retained_at=_NOW,
            representation=ContentRepresentation.BOUNDED_REPLAY_EXCERPT,
            capture_method="offline_bounded_xbrl_replay_excerpt",
            media_type="application/xml",
            source_url=attachment.source_url,
        )
        return AttachmentAcquisition(attachment, content, retained if retain else None)


def _pipeline(
    engine: Any,
) -> tuple[
    EdgarToolsSyncPipeline,
    tuple[EdgarToolsCompany, ...],
    AtomicEdgarToolsRepository,
]:
    manifest = _load_yaml(_REPLAY_PATH)
    registry = FinancialFieldRegistry.from_yaml(_REGISTRY_PATH)
    persistence = AtomicEdgarToolsRepository(engine, companies=_COMPANIES, registry=registry)
    pipeline = EdgarToolsSyncPipeline(
        adapter=_ReplayAdapter(manifest),
        registry=registry,
        golden_manifest=manifest,
        persistence=persistence,
    )
    companies = tuple(
        EdgarToolsCompany(company_id, identity.ticker, identity.cik)
        for company_id, identity in _COMPANIES.items()
    )
    return pipeline, companies, persistence


def _run_batch(
    pipeline: EdgarToolsSyncPipeline,
    companies: tuple[EdgarToolsCompany, ...],
) -> tuple[Any, ...]:
    prepared = tuple(pipeline.prepare_company(company) for company in companies)
    assert sum(len(item.validated_filings) for item in prepared) == 160
    assert sum(item.summary.failed_count for item in prepared) == 0
    assert sum(item.summary.quarantined_count for item in prepared) == 0
    return pipeline.persist_prepared_batch(prepared)


def test_phase5_cohort_b_manifest_and_registry_are_exact() -> None:
    manifest = _load_yaml(_MANIFEST_PATH)
    replay = _load_yaml(_REPLAY_PATH)
    universe = _load_yaml(_UNIVERSE_PATH)
    cases = cast("list[dict[str, Any]]", manifest["cases"])
    assert manifest["manifest_version"] == "phase5-cohort-b-v1"
    assert manifest["status"] == "FILING_XBRL_LINEAGE_VERIFIED"
    assert manifest["expected_case_count"] == len(cases) == 160
    assert manifest["acquisition_snapshot"]["sha256"] == _EXPECTED_SNAPSHOT_HASH
    assert manifest["acquisition_snapshot"]["retained_unique_object_count"] == 84
    assert manifest["acquisition_snapshot"]["all_object_hashes_and_lengths_verified"] is True
    assert tuple(dict.fromkeys(item["issuer_id"] for item in cases)) == tuple(sorted(_ISSUER_ORDER))
    assert len({item["case_id"] for item in cases}) == 160
    assert replay["manifest_version"] == "phase5-cohort-b-replay-v1"
    assert replay["replay_only"] is True
    assert replay["expected_case_count"] == len(replay["cases"]) == 160
    assert all(
        item["edgartools_source"]["sha256"] != item["original_edgartools_source"]["sha256"]
        for item in replay["cases"]
    )
    for issuer_id in _ISSUER_ORDER:
        issuer_cases = [item for item in cases if item["issuer_id"] == issuer_id]
        assert len(issuer_cases) == 16
        assert len({item["accession"] for item in issuer_cases}) == 8
        assert {item["classification"] for item in issuer_cases} == {
            "CORE_FINANCIAL",
            "OPTIONAL_SERVICING",
        }
        assert max(item["period_end"] for item in issuer_cases) == "2026-06-30"
        assert (
            max(item["period_end"] for item in issuer_cases if item["form"] == "10-K")
            == "2025-12-31"
        )

    assert universe["issuer_count"] == 10
    assert universe["bank_count"] == universe["nonbank_count"] == 5
    assert universe["source_manifest"] == "cohort-b-replay.v1.yaml"
    companies = cast("list[dict[str, Any]]", universe["companies"])
    assert tuple(item["id"] for item in companies) == _ISSUER_ORDER
    evidence = _load_yaml(_EVIDENCE_CASES_PATH)
    evidence_records = {
        item["evidence_case_id"]: item for item in cast("list[dict[str, Any]]", evidence["records"])
    }
    for company in companies:
        assert company["corporate_actions"] == []
        for claim_name in (
            "identity_evidence",
            "most_recent_filing",
            "latest_annual",
            "filing_coverage_evidence",
            "material_servicing_evidence",
            "latest_servicing_upb_or_msr",
        ):
            claim = company[claim_name]
            assert claim["evidence_cases_path"] == _EVIDENCE_CASES_PATH.name
            assert claim["evidence_case_ids"]
            for case_id in claim["evidence_case_ids"]:
                record = evidence_records[case_id]
                filing = record["filing"]
                original = record["original_document"]
                assert record["issuer_id"] == company["id"]
                assert all(filing[key] for key in ("accession", "form", "filed", "report_period"))
                assert original["source_url"].startswith("https://www.sec.gov/Archives/")
                assert original["byte_length"] > 0
                assert original["locators"]
    assert "exact_value" not in _UNIVERSE_PATH.read_text(encoding="utf-8")
    assert "priority_exclusions" not in universe
    assert universe["unsupported_corporate_action_notes"]["status"] == "NOT_DISCLOSED"


def test_phase5_replay_generator_and_recorded_hashes_are_exact() -> None:
    check_outputs(_ROOT)
    outputs = {path.name for path in build_outputs(_ROOT)}
    assert outputs == {
        "cohort-a-universe.v1.yaml",
        "cohort-b-replay.v1.yaml",
        "cohort-b-universe.v1.yaml",
        "evidence-cases.v1.yaml",
        "supported-universe.v1.yaml",
    }
    assert hashlib.sha256(_REPLAY_INDEX_PATH.read_bytes()).hexdigest() == (
        _EXPECTED_REPLAY_INDEX_HASH
    )
    assert hashlib.sha256(_EVIDENCE_CASES_PATH.read_bytes()).hexdigest() == (
        _EXPECTED_EVIDENCE_CASES_HASH
    )
    assert hashlib.sha256(_REPLAY_PATH.read_bytes()).hexdigest() == (_EXPECTED_REPLAY_MANIFEST_HASH)
    assert (
        hashlib.sha256(
            (_ROOT / "config" / "phase5" / "cohort-a-universe.v1.yaml").read_bytes()
        ).hexdigest()
        == _EXPECTED_COHORT_A_UNIVERSE_HASH
    )
    assert hashlib.sha256(_UNIVERSE_PATH.read_bytes()).hexdigest() == (
        _EXPECTED_COHORT_B_UNIVERSE_HASH
    )
    assert (
        hashlib.sha256(
            (_ROOT / "config" / "phase5" / "supported-universe.v1.yaml").read_bytes()
        ).hexdigest()
        == _EXPECTED_SUPPORTED_UNIVERSE_HASH
    )
    for command in (
        [sys.executable, "scripts/generate_phase5_manifest.py", "--check"],
        [sys.executable, "-m", "scripts.generate_phase5_manifest", "--check"],
    ):
        subprocess.run(command, cwd=_ROOT, check=True, capture_output=True, text=True)  # noqa: S603


def test_phase5_generator_rejects_missing_tampered_and_manual_value_outputs(
    tmp_path: Path,
) -> None:
    shutil.copytree(_ROOT / "config" / "phase5", tmp_path / "config" / "phase5")
    shutil.copytree(
        _ROOT / "tests" / "fixtures" / "phase5",
        tmp_path / "tests" / "fixtures" / "phase5",
    )
    generated = tmp_path / "config" / "phase5" / "cohort-a-universe.v1.yaml"
    original = generated.read_text(encoding="utf-8")
    generated.write_text(original + "tampered: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cohort-a-universe"):
        check_outputs(tmp_path)
    generated.write_text(original, encoding="utf-8")
    generated.unlink()
    with pytest.raises(ValueError, match="cohort-a-universe"):
        check_outputs(tmp_path)
    generated.write_text(original, encoding="utf-8")
    metadata = tmp_path / "config" / "phase5" / "cohort-b-universe.metadata.v1.yaml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8") + "exact_value: '999'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="manual financial authority"):
        check_outputs(tmp_path)


def test_phase5_cohort_b_cli_selection_is_bounded() -> None:
    parser = build_parser()
    args = parser.parse_args(["sync", "--phase5-cohort-b", "--all", "--dry-run"])
    assert args.phase5_cohort_b is True
    assert args.phase5_cohort_a is False
    companies = _edgar_companies(phase5_cohort_b=True)
    assert tuple(item.company_id for item in companies) == _ISSUER_ORDER
    assert tuple(item.ticker for item in companies) == (
        "TFC",
        "WFC",
        "JPM",
        "BAC",
        "USB",
        "PFSI",
        "RKT",
        "UWMC",
        "RITM",
        "LDI",
    )


def test_phase5_cohort_b_real_parser_pipeline_api_dashboard_and_idempotence(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'phase5-b.db').as_posix()}")
    pipeline, companies, _ = _pipeline(engine)
    first = _run_batch(pipeline, companies)
    first_results = tuple(result for summary in first for result in summary.filing_results)
    assert len(first_results) == 160
    assert all(result.state is EdgarToolsSyncState.PUBLISHED for result in first_results)
    replay = _run_batch(pipeline, companies)
    replay_results = tuple(result for summary in replay for result in summary.filing_results)
    assert len(replay_results) == 160
    assert all(result.state is EdgarToolsSyncState.UNCHANGED for result in replay_results)

    repository = IntelligenceRepository(engine)
    assert {item["id"] for item in repository.companies()} == set(_ISSUER_ORDER)
    latest = repository.observations(period_end=date(2026, 6, 30))
    assert len(latest) == 20
    assert all(row.value is not None and not isinstance(row.value, float) for row in latest)
    assert all(row.evidence_id and row.evidence_sha256 and row.evidence_links for row in latest)
    cards = normalize_companies(
        cast("list[CompanyIdentity]", repository.companies()),
        latest,
        target_periods=dict.fromkeys(_ISSUER_ORDER, "2026-06-30"),
    )
    rows_by_id = {row.id: row for row in latest}
    expected_servicing_metrics = {
        "tfc": "total_servicing_upb",
        "wfc": "servicing_for_others_upb",
        "jpm": "servicing_for_others_upb",
        "bac": "servicing_for_others_upb",
        "usb": "servicing_for_others_upb",
        "pfsi": "owned_msr_upb",
        "rkt": "total_servicing_upb",
        "uwmc": "servicing_for_others_upb",
        "ritm": "owned_msr_upb",
        "ldi": "total_servicing_upb",
    }
    assert {card.id for card in cards if card.classification == "bank"} == set(_ISSUER_ORDER[:5])
    assert {card.id for card in cards if card.classification == "nonbank"} == set(_ISSUER_ORDER[5:])
    for card in cards:
        assert card.upb.status == "reported"
        assert len(card.upb.inputs) == 1
        assert (
            rows_by_id[card.upb.inputs[0].observation_id].metric_id
            == (expected_servicing_metrics[card.id])
        )
        expected_mix_label = (
            "Bank-owned share" if card.classification == "bank" else "Owned MSR mix"
        )
        assert card.owned_mix.label == expected_mix_label
        assert card.owned_mix.status == "unavailable"
    with Session(engine) as session:
        company_rows = session.scalars(select(CompanyRow).order_by(CompanyRow.id)).all()
        assert len(company_rows) == 10
        assert {row.universe_version for row in company_rows} == {"financial-fields-phase5-v1"}
        assert session.scalar(select(func.count(Security.id))) == 10
        assert session.scalar(select(func.count(SourceEvidence.id))) == 80
        assert session.scalar(select(func.count(FilingRow.id))) == 80
        assert session.scalar(select(func.count(FilingDocument.id))) == 84
        assert session.scalar(select(func.count(MetricObservation.id))) == 160
        evidence_rows = session.scalars(select(SourceEvidence)).all()
        assert all(row.source_class == "SEC_XBRL_BOUNDED_REPLAY_EXCERPT" for row in evidence_rows)
        assert all("not the original SEC document" in row.bounded_excerpt for row in evidence_rows)
        observations = session.scalars(select(MetricObservation)).all()
        assert all(row.evidence_locator for row in observations)
        assert all(row.parser_metadata["original_evidence_sha256"] for row in observations)
        assert all(
            row.parser_metadata["original_evidence_sha256"]
            != row.parser_metadata["evidence_sha256"]
            for row in observations
        )
        assert all(row.parser_metadata["original_source_locators"] for row in observations)
        definitions = {
            row.id: (row.display_name, row.category)
            for row in session.scalars(select(MetricDefinition)).all()
        }
        assert definitions == {
            "owned_msr_upb": ("Owned MSR UPB", "portfolio"),
            "servicing_for_others_upb": ("Servicing For Others UPB", "portfolio"),
            "total_assets": ("Total Assets", "core_financial"),
            "total_servicing_upb": ("Total Servicing UPB", "portfolio"),
        }
        versions = session.scalars(select(MetricDefinitionVersion)).all()
        assert {row.rules["mapping_version"] for row in versions} == {"financial-fields-phase5-v1"}
        assert all(
            identity.legal_name not in row.business_meaning
            for row in versions
            for identity in _COMPANIES.values()
        )
        for filename in (
            "wfc-20241231_htm.xml",
            "wfc-20251231_d2_htm.xml",
            "usb-20241231_htm.xml",
            "usb-20251231_htm.xml",
        ):
            source = session.scalar(
                select(FilingDocument).where(FilingDocument.filename == filename)
            )
            assert source is not None
            assert source.is_primary is False
            assert source.source_evidence_id is not None

    app = create_app(repository=repository)
    api_routes = {
        route.path: set(route.methods or set())
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    }
    assert api_routes
    assert all(methods == {"GET"} for methods in api_routes.values())
    companies_payload = _endpoint(app, "/api/v1/companies")(repository, limit=100, offset=0)
    assert {item["id"] for item in companies_payload} == set(_ISSUER_ORDER)
    payload = _endpoint(app, "/api/v1/observations")(
        repository,
        company_id=None,
        metric_id=None,
        period_end=date(2026, 6, 30),
        as_of=None,
        include_missing=True,
        limit=100,
        offset=0,
    )
    assert len(payload) == 20
    assert all(isinstance(item["value"], str) for item in payload)
    detail = _endpoint(app, "/api/v1/observations/{observation_id}")(payload[0]["id"], repository)
    evidence = _endpoint(app, "/api/v1/evidence/{evidence_id}")(detail["evidence_id"], repository)
    assert detail["evidence"]["content_sha256"] == detail["evidence_sha256"]
    assert detail["id"] in evidence["linked_observation_ids"]
    drill_through = _endpoint(app, "/evidence/{evidence_id}/observations/{observation_id}")(
        _request(app, detail["evidence_locator_url"]),
        detail["evidence_id"],
        detail["id"],
        repository,
    )
    assert drill_through.status_code == 200
    comparison_endpoint = _endpoint(app, "/api/v1/comparisons")
    for pair in (("wfc", "jpm"), ("rkt", "uwmc"), ("tfc", "rkt")):
        comparison = comparison_endpoint(
            repository,
            metric_id="total_assets",
            period_end=date(2026, 6, 30),
            as_of=None,
            company_id=list(pair),
        )
        assert (comparison["left"]["company_id"], comparison["right"]["company_id"]) == pair
        assert comparison["left"]["evidence_links"]
        assert comparison["right"]["evidence_links"]
        assert comparison["left"]["evidence_locator"]
        assert comparison["right"]["evidence_locator"]
    reversed_pair = comparison_endpoint(
        repository,
        metric_id="total_assets",
        period_end=date(2026, 6, 30),
        as_of=None,
        company_id=["jpm", "wfc"],
    )
    assert reversed_pair["left"]["company_id"] == "jpm"
    three_way = comparison_endpoint(
        repository,
        metric_id="total_assets",
        period_end=date(2026, 6, 30),
        as_of=None,
        company_id=["wfc", "jpm", "bac"],
    )
    assert [(item["left"]["company_id"], item["right"]["company_id"]) for item in three_way] == [
        ("wfc", "jpm"),
        ("wfc", "bac"),
        ("jpm", "bac"),
    ]
    reversed_three_way = comparison_endpoint(
        repository,
        metric_id="total_assets",
        period_end=date(2026, 6, 30),
        as_of=None,
        company_id=["bac", "jpm", "wfc"],
    )
    assert [
        (item["left"]["company_id"], item["right"]["company_id"]) for item in reversed_three_way
    ] == [("bac", "jpm"), ("bac", "wfc"), ("jpm", "wfc")]
    incompatible = comparison_endpoint(
        repository,
        metric_id="servicing_for_others_upb",
        period_end=date(2026, 6, 30),
        as_of=None,
        company_id=["wfc", "jpm"],
    )
    assert incompatible["status"] == "not_comparable"
    assert "portfolio populations differ" in incompatible["reasons"]
    assert "reporting scopes differ" in incompatible["reasons"]
    for invalid in (
        ["tfc"],
        ["tfc", "tfc"],
        ["tfc", "pfsi", "wfc", "jpm"],
        ["tfc", "unsupported"],
        ["tfc", "bad identifier"],
    ):
        with pytest.raises(HTTPException) as rejected:
            comparison_endpoint(
                repository,
                metric_id="total_assets",
                period_end=date(2026, 6, 30),
                as_of=None,
                company_id=invalid,
            )
        assert rejected.value.status_code == 422
    comparison_page = _endpoint(app, "/comparison")(
        _request(app, "/comparison?company_id=wfc&company_id=jpm"),
        repository,
        metric_id="total_assets",
        period_end=date(2026, 6, 30),
        company_id=["wfc", "jpm"],
    )
    comparison_html = bytes(comparison_page.body).decode()
    assert 'data-selected-company-ids="wfc,jpm"' in comparison_html
    assert comparison_html.count('class="compare-card"') == 2
    assert 'option value="wfc" selected' in comparison_html
    assert 'option value="jpm" selected' in comparison_html
    three_way_page = _endpoint(app, "/comparison")(
        _request(app, "/comparison?company_id=wfc&company_id=jpm&company_id=bac"),
        repository,
        metric_id="total_assets",
        period_end=date(2026, 6, 30),
        company_id=["wfc", "jpm", "bac"],
    )
    three_way_html = bytes(three_way_page.body).decode()
    assert 'data-selected-company-ids="wfc,jpm,bac"' in three_way_html
    assert three_way_html.count('class="compare-card"') == 3
    for selected_ids, expected_label in (
        (["wfc", "uwmc"], "Servicing For Others UPB"),
        (["pfsi", "ritm"], "Owned MSR UPB"),
    ):
        label_page = _endpoint(app, "/comparison")(
            _request(
                app,
                "/comparison?" + "&".join(f"company_id={item}" for item in selected_ids),
            ),
            repository,
            metric_id="total_assets",
            period_end=date(2026, 6, 30),
            company_id=selected_ids,
        )
        label_html = bytes(label_page.body).decode()
        for selected_id in selected_ids:
            selected_card = next(card for card in cards if card.id == selected_id)
            assert selected_card.upb.reporting_scope is not None
            assert (
                f"<span>{expected_label} · Scope {selected_card.upb.reporting_scope} · "
                "Q2 2026</span>"
            ) in label_html
    overview = _endpoint(app, "/")(
        _request(app, "/"),
        repository,
        metric_id="total_servicing_upb",
        period_end=date(2026, 6, 30),
    )
    assert overview.status_code == 200
    for company_id in _ISSUER_ORDER:
        page = _endpoint(app, "/companies/{company_id}")(
            company_id,
            _request(app, f"/companies/{company_id}"),
            repository,
            metric_id="total_servicing_upb",
            period_end=date(2026, 6, 30),
        )
        assert page.status_code == 200
        assert _COMPANIES[company_id].ticker in bytes(page.body).decode()
    calendar = _endpoint(app, "/api/v1/calendar")(repository)
    assert {item["company_id"] for item in calendar} == set(_ISSUER_ORDER)
    with Session(engine) as session:
        inactive = session.get(CompanyRow, "ldi")
        assert inactive is not None
        inactive.active = False
        session.commit()
    with pytest.raises(HTTPException) as inactive_rejected:
        comparison_endpoint(
            repository,
            metric_id="total_assets",
            period_end=date(2026, 6, 30),
            as_of=None,
            company_id=["tfc", "ldi"],
        )
    assert inactive_rejected.value.status_code == 422
    with Session(engine) as session:
        inactive = session.get(CompanyRow, "ldi")
        assert inactive is not None
        inactive.active = True
        unpublished = session.scalars(
            select(MetricObservation)
            .join(
                ReportingEntity,
                MetricObservation.reporting_entity_id == ReportingEntity.id,
            )
            .where(ReportingEntity.company_id == "ldi")
        ).all()
        assert unpublished
        for observation in unpublished:
            observation.publication_state = "SUPERSEDED"
        session.commit()
    with pytest.raises(HTTPException) as unpublished_rejected:
        comparison_endpoint(
            repository,
            metric_id="total_assets",
            period_end=date(2026, 6, 30),
            as_of=None,
            company_id=["tfc", "ldi"],
        )
    assert unpublished_rejected.value.status_code == 422
    engine.dispose()


def test_phase5_replay_coexists_with_exact_legacy_439_baseline(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    assert hashlib.sha256(_LEGACY_BASELINE_PATH.read_bytes()).hexdigest() == (
        _EXPECTED_LEGACY_BASELINE_HASH
    )
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'phase5-legacy.db').as_posix()}")
    seed_phase3(engine, config_dir=_ROOT / "config")
    with _LEGACY_BASELINE_PATH.open(encoding="utf-8", newline="") as stream:
        baseline_rows = list(csv.DictReader(stream))
    expected_snapshot = {
        row["observation_id"]: (
            row["normalized_decimal_value"] or None,
            row["observation_state"],
            row["publication_state"],
            row["methodology"],
        )
        for row in baseline_rows
    }
    expected_links = {
        row["observation_id"]: tuple(sorted(filter(None, row["evidence_ids"].split(";"))))
        for row in baseline_rows
    }
    assert len(baseline_rows) == len(expected_snapshot) == 439
    with Session(engine) as session:
        baseline = session.scalars(
            select(MetricObservation)
            .where(MetricObservation.id.in_(expected_snapshot))
            .order_by(MetricObservation.id)
        ).all()
        assert len(baseline) == 439
        baseline_snapshot = {
            row.id: (
                None if row.value is None else str(row.value),
                row.observation_state,
                row.publication_state,
                row.methodology,
            )
            for row in baseline
        }
        assert baseline_snapshot == expected_snapshot
        actual_links: dict[str, list[str]] = {
            observation_id: [] for observation_id in expected_snapshot
        }
        for observation_id, evidence_id in session.execute(
            select(ObservationEvidence.observation_id, ObservationEvidence.evidence_id).where(
                ObservationEvidence.observation_id.in_(expected_snapshot)
            )
        ):
            actual_links[observation_id].append(evidence_id)
        assert {
            observation_id: tuple(sorted(evidence_ids))
            for observation_id, evidence_ids in actual_links.items()
        } == expected_links
        evidence_ids = sorted(
            {evidence_id for linked_ids in expected_links.values() for evidence_id in linked_ids}
        )
        evidence_projection = [
            (row.id, row.content_sha256)
            for row in session.scalars(
                select(SourceEvidence)
                .where(SourceEvidence.id.in_(evidence_ids))
                .order_by(SourceEvidence.id)
            )
        ]
        assert len(evidence_projection) == len(evidence_ids) == 22
        assert (
            hashlib.sha256(
                json.dumps(evidence_projection, separators=(",", ":")).encode()
            ).hexdigest()
            == _EXPECTED_LEGACY_EVIDENCE_HASH
        )
    pipeline, companies, _ = _pipeline(engine)
    summaries = _run_batch(pipeline, companies)
    results = tuple(result for summary in summaries for result in summary.filing_results)
    assert len(results) == 160
    assert all(
        result.state
        in {
            EdgarToolsSyncState.PUBLISHED,
            EdgarToolsSyncState.LINKED,
            EdgarToolsSyncState.UNCHANGED,
        }
        for result in results
    )
    repository = IntelligenceRepository(engine)
    public_page = repository.observations()
    complete_snapshot = repository.observation_snapshot()
    assert len(public_page) == 500
    assert repository.observation_count() == len(complete_snapshot) == 599
    assert len({row.company_id for row in complete_snapshot}) == 10
    latest_rows = [row for row in complete_snapshot if row.period_end == "2026-06-30"]
    assert len(latest_rows) > 100
    assert set(_ISSUER_ORDER) <= {row.company_id for row in latest_rows}

    app = create_app(repository=repository)
    overview = _endpoint(app, "/")(
        _request(app, "/"),
        repository,
        metric_id="total_assets",
        period_end=None,
    )
    assert overview.status_code == 200
    overview_html = bytes(overview.body).decode()
    assert all(ticker in overview_html for ticker in ("WFC", "JPM", "BAC", "USB", "RKT"))
    governed_order = [str(company["id"]) for company in repository.companies()]
    row_positions = [
        overview_html.index(
            f'<article class="company-row" role="row" data-company-id="{company_id}"'
        )
        for company_id in governed_order
    ]
    assert row_positions == sorted(row_positions)
    assert 'id="company-sort"' not in overview_html
    assert "data-sort-upb" not in overview_html
    assert "different metric or scope definitions are not auto-sorted" in overview_html
    assert "Scope " in overview_html
    nondefault_overview = _endpoint(app, "/comparison")(
        _request(app, "/comparison?company_id=wfc&company_id=jpm"),
        repository,
        metric_id="total_servicing_upb",
        period_end=None,
        company_id=["wfc", "jpm"],
        third_company_id=None,
    )
    assert nondefault_overview.status_code == 200
    nondefault_html = bytes(nondefault_overview.body).decode()
    assert "Wells Fargo &amp; Company" in nondefault_html
    assert "JPMorgan Chase &amp; Co." in nondefault_html
    freshness = _endpoint(app, "/api/v1/pipeline/freshness")(repository)
    assert freshness["calendar_freshness_state"] == "MIXED"
    assert len(freshness["calendar_freshness_by_company"]) == 10
    assert freshness["calendar_freshness_by_company"]["wfc"] == "CALENDAR_NOT_CONFIGURED"
    assert freshness["source_assessment_count"] > 0
    with Session(engine) as session:
        after = {
            row.id: (
                None if row.value is None else str(row.value),
                row.observation_state,
                row.publication_state,
                row.methodology,
            )
            for row in session.scalars(
                select(MetricObservation).where(MetricObservation.id.in_(baseline_snapshot))
            ).all()
        }
        assert after == baseline_snapshot
    engine.dispose()


def test_phase5_metadata_is_order_independent_and_rerun_identity_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    preparation_engine = create_database_engine(
        f"sqlite:///{(tmp_path / 'phase5-prepare.db').as_posix()}"
    )
    pipeline, companies, _ = _pipeline(preparation_engine)
    prepared = tuple(pipeline.prepare_company(company) for company in companies[:2])
    validated = tuple(item for batch in prepared for item in batch.validated_filings)
    assert len(validated) == 32
    registry = FinancialFieldRegistry.from_yaml(_REGISTRY_PATH)

    forward_engine = create_database_engine(
        f"sqlite:///{(tmp_path / 'phase5-forward.db').as_posix()}"
    )
    reverse_engine = create_database_engine(
        f"sqlite:///{(tmp_path / 'phase5-reverse.db').as_posix()}"
    )
    forward = AtomicEdgarToolsRepository(
        forward_engine,
        companies=_COMPANIES,
        registry=registry,
    )
    reverse = AtomicEdgarToolsRepository(
        reverse_engine,
        companies=_COMPANIES,
        registry=registry,
    )
    forward.persist_atomically(validated)
    reverse.persist_atomically(tuple(reversed(validated)))

    def snapshot(engine: Any) -> dict[str, Any]:
        with Session(engine) as session:
            return {
                "companies": [
                    (
                        row.id,
                        row.legal_name,
                        row.ticker,
                        row.classification,
                        row.universe_version,
                        row.active,
                    )
                    for row in session.scalars(select(CompanyRow).order_by(CompanyRow.id))
                ],
                "securities": [
                    (row.id, row.company_id, row.ticker, row.exchange, row.security_type)
                    for row in session.scalars(select(Security).order_by(Security.id))
                ],
                "entities": [
                    (row.id, row.company_id, row.legal_name, row.entity_type)
                    for row in session.scalars(select(ReportingEntity).order_by(ReportingEntity.id))
                ],
                "scopes": [
                    (
                        row.id,
                        row.reporting_entity_id,
                        row.name,
                        row.portfolio_population,
                        row.methodology,
                    )
                    for row in session.scalars(select(ReportingScope).order_by(ReportingScope.id))
                ],
                "definitions": [
                    (row.id, row.display_name, row.category)
                    for row in session.scalars(
                        select(MetricDefinition).order_by(MetricDefinition.id)
                    )
                ],
                "versions": [
                    (
                        row.id,
                        row.metric_id,
                        row.semantic_version,
                        row.business_meaning,
                        row.grain,
                        row.unit,
                        tuple(row.permitted_scopes),
                        row.rules,
                    )
                    for row in session.scalars(
                        select(MetricDefinitionVersion).order_by(MetricDefinitionVersion.id)
                    )
                ],
            }

    assert snapshot(forward_engine) == snapshot(reverse_engine)

    def assert_rerun_rejects(
        model: Any,
        key: str,
        attribute: str,
        conflicting: object,
        message: str,
    ) -> None:
        with Session(forward_engine) as session, session.begin():
            row = session.get(model, key)
            assert row is not None
            original = getattr(row, attribute)
            setattr(row, attribute, conflicting)
        with pytest.raises(ValueError, match=message):
            forward.persist_atomically(validated)
        with Session(forward_engine) as session, session.begin():
            row = session.get(model, key)
            assert row is not None
            setattr(row, attribute, original)

    assert_rerun_rejects(
        CompanyRow,
        "tfc",
        "legal_name",
        "Conflicting Registrant",
        "company identity",
    )
    assert_rerun_rejects(
        CompanyRow,
        "tfc",
        "universe_version",
        "phase-2-acquisition-2026-08-12",
        "universe version",
    )
    assert_rerun_rejects(
        Security,
        "tfc:common",
        "exchange",
        "CONFLICT",
        "security identity",
    )
    assert_rerun_rejects(
        ReportingEntity,
        "tfc_registrant",
        "entity_type",
        "CONFLICT",
        "reporting entity",
    )
    scope_id = validated[0].reporting_scope_id
    assert_rerun_rejects(
        ReportingScope,
        scope_id,
        "methodology",
        "Conflicting scope methodology",
        "scope conflicts",
    )
    assert_rerun_rejects(
        MetricDefinition,
        "total_assets",
        "display_name",
        "Issuer-specific assets",
        "issuer-neutral metadata",
    )
    assert_rerun_rejects(
        MetricDefinitionVersion,
        "total_assets:1.0.0",
        "business_meaning",
        "Issuer-specific business meaning",
        "issuer-neutral metadata",
    )
    unchanged = forward.persist_atomically(tuple(reversed(validated)))
    assert all(outcome.state.value == "UNCHANGED" for outcome in unchanged.outcomes)
    preparation_engine.dispose()
    forward_engine.dispose()
    reverse_engine.dispose()
