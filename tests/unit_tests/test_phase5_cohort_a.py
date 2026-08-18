"""Offline acceptance for the exact four-issuer Phase 5 cohort A contract."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import Request
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.database import (
    FilingDocument,
    MetricObservation,
    SourceEvidence,
    create_database_engine,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import ValidatedFiling
from mortgage_servicing_dashboard.financial_discovery import (
    FinancialClassification,
    FinancialFieldRegistry,
)
from mortgage_servicing_dashboard.repository import (
    AtomicEdgarToolsRepository,
    EdgarToolsCompanyIdentity,
    IntelligenceRepository,
)
from mortgage_servicing_dashboard.xbrl import XbrlPeriodType

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _ROOT / "config" / "phase5" / "cohort-a-sources.v1.yaml"
_UNIVERSE_PATH = _ROOT / "config" / "phase5" / "cohort-a-universe.v1.yaml"
_EVIDENCE_CASES_PATH = _ROOT / "config" / "phase5" / "evidence-cases.v1.yaml"
_REGISTRY_PATH = _ROOT / "config" / "phase5" / "financial_fields.v1.yaml"
_EXPECTED_SNAPSHOT_HASH = "a2a4cfe81cf2b3ccc23fd2b79cd906120aeeb86c6c5ac0c1874fdf1845fdb297"
_EXPECTED_UNIVERSE_HASH = "3abfdb587b0bfdad79edddf7d783d763d8c328f782ae05fc770df07b9e8a6b8e"
_COMPANIES = {
    "tfc": EdgarToolsCompanyIdentity(
        "Truist Financial Corporation", "TFC", "bank", "0000092230", "tfc_registrant"
    ),
    "wfc": EdgarToolsCompanyIdentity(
        "Wells Fargo & Company", "WFC", "bank", "0000072971", "wfc_registrant"
    ),
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
}


def _load_manifest() -> dict[str, Any]:
    loaded = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for nested in value.values():
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_all_keys(nested))
    return keys


def _endpoint(app: Any, path: str) -> Any:
    return next(
        route.endpoint for route in app.routes if isinstance(route, APIRoute) and route.path == path
    )


def _request(app: Any, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "router": app.router,
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _validated(case: dict[str, Any], registry: FinancialFieldRegistry) -> ValidatedFiling:
    mapping = next(item for item in registry.mappings if item.mapping_id == case["mapping_id"])
    fact = cast("dict[str, Any]", case["approved_fact"])
    source = cast("dict[str, Any]", case["edgartools_source"])
    filing_date = date.fromisoformat(case["filing_date"])
    period_end = date.fromisoformat(case["period_end"])
    return ValidatedFiling(
        case_id=case["case_id"],
        mapping_version=registry.version,
        company_id=case["issuer_id"],
        cik=case["cik"],
        accession_number=case["accession"],
        form=case["form"],
        filing_date=filing_date,
        acceptance_timestamp=datetime.combine(filing_date, time(21, 0), tzinfo=UTC),
        report_period=period_end,
        fiscal_year=int(case["fiscal_year"]),
        fiscal_quarter=case["fiscal_quarter"],
        amendment=bool(case["amendment"]),
        revision_of_accession=case.get("revision_of_accession"),
        primary_document=case["primary_document"],
        primary_sequence=str(case["primary_sequence"]),
        primary_document_type=case["primary_document_type"],
        primary_description=case["primary_description"],
        source_url=case["source_url"],
        evidence_sha256=source["sha256"],
        evidence_byte_length=int(source["byte_length"]),
        evidence_location=source["retention_location"],
        evidence_retrieved_at=datetime.fromisoformat(source["retrieved_at"]),
        edgartools_version="5.48.0",
        evidence_representation=source["representation"],
        evidence_capture_method=source["capture_method"],
        evidence_media_type=(
            "application/xml"
            if str(case["source_document"]).endswith(".xml")
            else "text/html; charset=utf-8"
        ),
        field_id=case["field_id"],
        classification=FinancialClassification(case["classification"]),
        reporting_entity_id=mapping.xbrl.reporting_entity_id,
        reporting_scope_id=mapping.xbrl.reporting_scope_id,
        reporting_scope_name=mapping.reporting_scope_name,
        portfolio_population=mapping.portfolio_population,
        scope_methodology=mapping.scope_methodology,
        qualified_concept=fact["qualified_concept"],
        original_label=fact["original_label"],
        raw_display_string=fact["raw_display_string"],
        normalized_value=Decimal(fact["normalized_decimal_string"]),
        context_ref=fact["context_ref"],
        unit=fact["unit"],
        decimals=fact["decimals"],
        source_scale=Decimal(fact["scale"]),
        source_sign=fact["source_sign"],
        source_precision=fact["source_precision"],
        presentation_sign=fact["presentation_sign"],
        source_element_ids=tuple(fact["source_element_ids"]),
        source_object_count=int(fact["source_object_count"]),
        source_locators=tuple(fact["source_locators"]),
        mapping_id=case["mapping_id"],
        source_document=case["source_document"],
        source_sequence=str(case["source_sequence"]),
        source_document_type=case["source_document_type"],
        source_description=case["source_description"],
        source_is_primary=bool(case["source_is_primary"]),
        period_type=XbrlPeriodType(fact["period_type"]),
        period_start=(
            None if fact["period_start"] is None else date.fromisoformat(fact["period_start"])
        ),
        dimensions=tuple((item["dimension"], item["member"]) for item in fact["dimensions"]),
        metric_version=mapping.xbrl.metric_version,
        metric_display_name=mapping.display_name,
        reporting_scope_category=mapping.reporting_scope_category.value,
        primary_source_url=case["primary_source_url"],
    )


def test_phase5_cohort_a_manifest_is_bounded_complete_and_exact() -> None:
    manifest = _load_manifest()
    universe = _load_yaml(_UNIVERSE_PATH)
    evidence = _load_yaml(_EVIDENCE_CASES_PATH)
    cases = cast("list[dict[str, Any]]", manifest["cases"])
    assert manifest["status"] == "FILING_XBRL_LINEAGE_VERIFIED"
    assert manifest["expected_case_count"] == len(cases) == 64
    assert manifest["acquisition_snapshot"]["sha256"] == _EXPECTED_SNAPSHOT_HASH
    assert manifest["acquisition_snapshot"]["retained_unique_object_count"] == 34
    assert manifest["acquisition_snapshot"]["all_object_hashes_and_lengths_verified"] is True
    assert {item["issuer_id"] for item in cases} == {"tfc", "wfc", "pfsi", "rkt"}
    assert len({item["case_id"] for item in cases}) == 64
    for issuer_id in ("tfc", "wfc", "pfsi", "rkt"):
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

    assert hashlib.sha256(_UNIVERSE_PATH.read_bytes()).hexdigest() == _EXPECTED_UNIVERSE_HASH
    assert universe["source_manifest"] == "cohort-b-replay.v1.yaml"
    assert universe["source_manifest_case_count"] == 64
    assert universe["issuer_count"] == 4
    assert universe["bank_count"] == universe["nonbank_count"] == 2
    forbidden = {
        "authoritative_observations",
        "exact_value",
        "normalized_value",
        "raw_value",
        "raw_display_string",
        "normalized_decimal_string",
        "context_id",
        "context_ref",
        "locator",
        "source_locator",
        "source_locators",
        "scale",
        "unit",
    }
    assert not forbidden & _all_keys(universe)
    records = {
        item["evidence_case_id"]: item for item in cast("list[dict[str, Any]]", evidence["records"])
    }
    companies = cast("list[dict[str, Any]]", universe["companies"])
    assert tuple(item["id"] for item in companies) == ("tfc", "wfc", "pfsi", "rkt")
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
            for case_id in claim["evidence_case_ids"]:
                record = records[case_id]
                filing = record["filing"]
                original = record["original_document"]
                assert record["issuer_id"] == company["id"]
                assert all(filing[key] for key in ("accession", "form", "filed", "report_period"))
                assert original["source_url"].startswith("https://www.sec.gov/Archives/")
                assert original["sha256"]
                assert original["byte_length"] > 0
                assert original["locators"]


def test_generalized_a_persistence_api_dashboard_and_idempotence(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    manifest = _load_manifest()
    registry = FinancialFieldRegistry.from_yaml(_REGISTRY_PATH)
    cases = cast("list[dict[str, Any]]", manifest["cases"])
    selected = [item for item in cases if item["period_end"] == "2026-06-30"]
    selected.append(
        next(
            item
            for item in cases
            if item["issuer_id"] == "wfc"
            and item["period_end"] == "2025-12-31"
            and item["field_id"] == "total_assets"
        )
    )
    validated = tuple(_validated(item, registry) for item in selected)
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'phase5-a.db').as_posix()}")
    persistence = AtomicEdgarToolsRepository(
        engine,
        companies=_COMPANIES,
        registry=registry,
    )

    first = persistence.persist_atomically(validated)
    assert first.observations == first.raw_facts == first.revisions == 9
    assert first.evidence == first.filings == 5
    assert first.documents == 6
    assert first.quarantined == 0
    replay = persistence.persist_atomically(validated)
    assert replay.observations == replay.raw_facts == replay.revisions == 0
    assert all(outcome.state.value == "UNCHANGED" for outcome in replay.outcomes)

    repository = IntelligenceRepository(engine)
    assert {item["id"] for item in repository.companies()} == {
        "tfc",
        "wfc",
        "pfsi",
        "rkt",
    }
    rows = repository.observations(period_end=date(2026, 6, 30))
    assert len(rows) == 8
    assert all(row.value is not None and not isinstance(row.value, float) for row in rows)
    assert all(row.evidence_id and row.evidence_sha256 for row in rows)
    assert all(row.evidence_links for row in rows)
    with Session(engine) as session:
        assert session.scalar(select(func.count(SourceEvidence.id))) == 5
        assert session.scalar(select(func.count(FilingDocument.id))) == 6
        assert session.scalar(select(func.count(MetricObservation.id))) == 9
        wfc_xml = session.scalar(
            select(FilingDocument).where(FilingDocument.filename == "wfc-20251231_d2_htm.xml")
        )
        wfc_primary = session.scalar(
            select(FilingDocument).where(FilingDocument.filename == "wfc-20251231_d2.htm")
        )
        assert wfc_xml is not None
        assert wfc_xml.is_primary is False
        assert wfc_xml.source_evidence_id is not None
        assert wfc_primary is not None
        assert wfc_primary.is_primary is True
        assert wfc_primary.source_evidence_id is None

    app = create_app(repository=repository)
    api_routes = {
        route.path: set(route.methods or set())
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    }
    assert api_routes
    assert all(methods == {"GET"} for methods in api_routes.values())
    companies_payload = _endpoint(app, "/api/v1/companies")(
        repository,
        limit=100,
        offset=0,
    )
    assert {item["id"] for item in companies_payload} == {"tfc", "wfc", "pfsi", "rkt"}
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
    assert len(payload) == 8
    assert all(isinstance(item["value"], str) for item in payload)
    detail_payload = _endpoint(app, "/api/v1/observations/{observation_id}")(
        payload[0]["id"],
        repository,
    )
    assert detail_payload["evidence"]["content_sha256"] == detail_payload["evidence_sha256"]
    evidence_payload = _endpoint(app, "/api/v1/evidence/{evidence_id}")(
        detail_payload["evidence_id"],
        repository,
    )
    assert detail_payload["id"] in evidence_payload["linked_observation_ids"]
    drill_through = _endpoint(
        app,
        "/evidence/{evidence_id}/observations/{observation_id}",
    )(
        _request(app, detail_payload["evidence_locator_url"]),
        detail_payload["evidence_id"],
        detail_payload["id"],
        repository,
    )
    assert drill_through.status_code == 200
    overview = _endpoint(app, "/")(
        _request(app, "/"),
        repository,
        metric_id="total_servicing_upb",
        period_end=date(2026, 6, 30),
    )
    assert overview.status_code == 200
    for company_id in ("tfc", "wfc", "pfsi", "rkt"):
        company_page = _endpoint(app, "/companies/{company_id}")(
            company_id,
            _request(app, f"/companies/{company_id}"),
            repository,
            metric_id="total_servicing_upb",
            period_end=date(2026, 6, 30),
        )
        assert company_page.status_code == 200
        assert _COMPANIES[company_id].ticker in bytes(company_page.body).decode()
    calendar_payload = _endpoint(app, "/api/v1/calendar")(repository)
    assert {item["company_id"] for item in calendar_payload} == {
        "tfc",
        "wfc",
        "pfsi",
        "rkt",
    }
    engine.dispose()
