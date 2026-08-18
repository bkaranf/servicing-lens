from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import mortgage_servicing_dashboard.financial_discovery as discovery
from mortgage_servicing_dashboard.domain import PublicationState
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    XbrlContext,
    XbrlDimension,
    XbrlFiling,
    XbrlUnit,
)
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    XbrlFact as AdapterXbrlFact,
)
from mortgage_servicing_dashboard.financial_discovery import (
    AvailabilityStatus,
    FieldDiscovery,
    FinancialClassification,
    FinancialDiscoveryError,
    FinancialFieldRegistry,
    ProposedFactLocator,
    RawFieldDiscovery,
    RawFilingFactLocator,
    ReviewStatus,
    SelectedFieldMapping,
    SelectionDecision,
    SourceRoute,
    discover_filing_fields,
    discover_parsed_filing_fields,
    discover_retained_document_fields,
)
from mortgage_servicing_dashboard.xbrl import (
    DimensionMember,
    XbrlDataError,
    XbrlFact,
    XbrlMappingRegistry,
    XbrlPeriodType,
    XbrlSource,
)

_REPOSITORY_ROOT = Path(__file__).parents[2]
_MAPPING_PATH = _REPOSITORY_ROOT / "config" / "financial_fields.v1.yaml"
_LEGACY_BASELINE_PATH = _REPOSITORY_ROOT / "config" / "audit" / "legacy-439-baseline.csv"
_GOLDEN_MANIFEST_PATH = (
    _REPOSITORY_ROOT / "tests" / "fixtures" / "edgartools" / "golden-sources.v1.yaml"
)
_AVAILABILITY_PATH = (
    _REPOSITORY_ROOT / "artifacts" / "edgar-tools-migration" / "financial-availability.csv"
)
_LEGACY_BASELINE_SHA256 = "112661f7d3414793f747c6cdd9a890f480a2f98768bb8268cae9ad70c2e3f0b2"
_LEGACY_BASELINE_BYTE_LENGTH = 326_936
_ACCESSION = "0000092230-26-000099"
_CIK = "0000092230"
_DOCUMENT = "tfc-20260630.htm"
_URL = "https://www.sec.gov/Archives/example/tfc-20260630.htm"
_EVIDENCE_ID = "sha256:" + "a" * 64


class _DefaultUnit:
    """Sentinel marking the default synthetic USD unit."""


_DEFAULT_UNIT = _DefaultUnit()


@pytest.fixture
def registry() -> FinancialFieldRegistry:
    return FinancialFieldRegistry.from_yaml(_MAPPING_PATH)


def _mapping(
    registry: FinancialFieldRegistry,
    issuer_id: str = "tfc",
) -> SelectedFieldMapping:
    return next(item for item in registry.mappings if item.issuer_id == issuer_id)


def _adapter_fact(  # noqa: PLR0913 - variations exercise every exact matching semantic.
    *,
    raw_value: str = "000556023000000.00",
    concept: str = "Assets",
    taxonomy: str = "us-gaap",
    context_ref: str = "c-7",
    period_type: str = "instant",
    period_instant: str | None = "2026-06-30",
    dimensions: tuple[XbrlDimension, ...] = (),
    unit_measure: str | None = "iso4217:USD",
    unit: XbrlUnit | _DefaultUnit | None = _DEFAULT_UNIT,
    decimals: str | None = "-6",
    scale: str | None = None,
    precision: str | None = None,
    fact_id: str | None = "fact-1",
    instance_id: str | None = "instance-1",
    original_label: str | None = "Total assets",
) -> AdapterXbrlFact:
    context = XbrlContext(
        context_id=context_ref,
        entity_identifier=_CIK,
        entity_scheme="http://www.sec.gov/CIK",
        period_type=period_type,
        period_start=None,
        period_end=None,
        period_instant=period_instant,
        dimensions=dimensions,
    )
    resolved_unit = (
        XbrlUnit(
            unit_ref="USD",
            unit_type="measure",
            measure=unit_measure,
            numerator=(),
            denominator=(),
        )
        if isinstance(unit, _DefaultUnit)
        else unit
    )
    return AdapterXbrlFact(
        taxonomy=taxonomy,
        concept=concept,
        original_label=original_label,
        raw_value=raw_value,
        context_ref=context_ref,
        context=context,
        unit_ref=None if resolved_unit is None else resolved_unit.unit_ref,
        unit=resolved_unit,
        decimals=decimals,
        scale=scale,
        precision=precision,
        fact_id=fact_id,
        instance_id=instance_id,
    )


def _filing(*facts: AdapterXbrlFact, cik: str = _CIK) -> XbrlFiling:
    contexts = tuple(dict.fromkeys(fact.context for fact in facts))
    units = tuple(dict.fromkeys(fact.unit for fact in facts if fact.unit is not None))
    return XbrlFiling(
        cik=cik,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        facts=tuple(facts),
        contexts=contexts,
        units=units,
    )


def _parsed_fact(  # noqa: PLR0913 - variations exercise every exact matching semantic.
    *,
    raw_value: str = "556023",
    value: Decimal = Decimal(556023000000),
    source: XbrlSource = XbrlSource.SEC_FILING_XBRL,
    issuer_id: str = "tfc",
    cik: str = _CIK,
    taxonomy: str = "us-gaap",
    concept: str = "Assets",
    unit: str = "USD",
    decimals: int | str | None = -6,
    period_type: XbrlPeriodType = XbrlPeriodType.INSTANT,
    dimensions: tuple[DimensionMember, ...] = (),
    context_id: str = "c-7",
    locator: str = "xbrl:us-gaap:Assets;context=c-7;unit=USD;element_id=fact-1;occurrence=0",
    source_element_id: str | None = "fact-1",
) -> XbrlFact:
    return XbrlFact(
        source=source,
        issuer_id=issuer_id,
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        raw_value=raw_value,
        value=value,
        unit=unit,
        scale=Decimal(1),
        decimals=decimals,
        period_type=period_type,
        period_start=None if period_type is XbrlPeriodType.INSTANT else date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        dimensions=dimensions,
        entity_identifier=_CIK,
        context_id=context_id,
        accession=_ACCESSION,
        form="10-Q",
        filed=date(2026, 7, 31),
        evidence_id=_EVIDENCE_ID,
        locator=locator,
        source_element_id=source_element_id,
    )


def _inline_xbrl(*, selected_format: str = "ixt:num-dot-decimal") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025">
 <body>
  <ix:resources>
   <xbrli:context id="c-7"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">92230</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
   <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  </ix:resources>
  <ix:nonFraction id="assets-1" name="us-gaap:Assets" contextRef="c-7"
   unitRef="USD" decimals="-6" scale="6"
   format="{selected_format}">556023</ix:nonFraction>
  <ix:nonFraction id="unrelated" name="us-gaap:Liabilities" contextRef="missing"
   unitRef="missing" decimals="-6" format="bad:unknown">1</ix:nonFraction>
 </body>
</html>""".encode()


def test_legacy_439_baseline_is_byte_identical_with_exact_company_and_state_counts() -> None:
    payload = _LEGACY_BASELINE_PATH.read_bytes()

    assert len(payload) == _LEGACY_BASELINE_BYTE_LENGTH
    assert hashlib.sha256(payload).hexdigest() == _LEGACY_BASELINE_SHA256
    with _LEGACY_BASELINE_PATH.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 439
    assert Counter(row["company_id"] for row in rows) == {"pfsi": 223, "tfc": 216}
    assert Counter(row["observation_state"] for row in rows) == {
        "REPORTED_ACTUAL": 174,
        "DERIVED": 43,
        "NOT_DISCLOSED": 222,
    }
    assert Counter(row["publication_state"] for row in rows) == {"PUBLISHED": 439}


def test_compact_mapping_is_raw_document_only_and_preserves_consolidated_scope() -> None:
    xbrl_registry = XbrlMappingRegistry.from_yaml(_MAPPING_PATH)
    financial_registry = FinancialFieldRegistry.from_yaml(_MAPPING_PATH)

    assert xbrl_registry.version == financial_registry.version == "financial-fields-v1"
    assert {(item.issuer_id, item.field_id) for item in financial_registry.mappings} == {
        ("pfsi", "total_assets"),
        ("tfc", "total_assets"),
    }
    assert all(
        item.classification is FinancialClassification.CORE_FINANCIAL
        and item.selection_decision is SelectionDecision.SELECTED
        and item.source_route is SourceRoute.SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS
        and item.publication_state is PublicationState.CANDIDATE
        and item.review_status is ReviewStatus.REVIEW_REQUIRED
        and item.reporting_scope_category == "CONSOLIDATED_COMPANY"
        and item.portfolio_population == "consolidated_sec_registrant"
        and "servicing portfolio" in item.scope_methodology
        and item.eligible_forms == ("10-K", "10-K/A", "10-Q", "10-Q/A")
        for item in financial_registry.mappings
    )


def test_registry_filters_exact_cik_form_and_selected_decision(
    registry: FinancialFieldRegistry,
) -> None:
    selected = _mapping(registry)
    excluded = replace(selected, selection_decision=SelectionDecision.EXCLUDED)
    mixed = FinancialFieldRegistry(version="v", mappings=(excluded,))

    assert registry.for_filing(cik=_CIK, form="10-Q") == (selected,)
    assert registry.for_filing(cik="0000000000", form="10-Q") == ()
    assert registry.for_filing(cik=_CIK, form="8-K") == ()
    assert mixed.for_filing(cik=_CIK, form="10-Q") == ()


def _exercise_selected_mapping_invalid_change(
    selected: SelectedFieldMapping,
    change: str,
) -> None:
    if change == "blank_display_name":
        replace(selected, display_name=" ")
    elif change == "empty_eligible_forms":
        replace(selected, eligible_forms=())
    elif change == "blank_eligible_form":
        replace(selected, eligible_forms=("10-Q", ""))
    elif change == "wrong_portfolio_population":
        replace(selected, portfolio_population="servicing")
    elif change == "published":
        replace(selected, publication_state=PublicationState.PUBLISHED)
    elif change == "independent_review":
        replace(selected, review_status=ReviewStatus.INDEPENDENTLY_CROSS_CHECKED)
    else:
        raise AssertionError


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("blank_display_name", "semantics must not be blank"),
        ("empty_eligible_forms", "explicit eligible forms"),
        ("blank_eligible_form", "explicit eligible forms"),
        ("wrong_portfolio_population", "consolidated SEC registrant"),
        ("published", "must remain CANDIDATE"),
        ("independent_review", "require independent review"),
    ],
)
def test_selected_mapping_fails_closed_on_unsafe_semantics(
    registry: FinancialFieldRegistry,
    change: str,
    message: str,
) -> None:
    selected = _mapping(registry)
    with pytest.raises(FinancialDiscoveryError, match=message):
        _exercise_selected_mapping_invalid_change(selected, change)


def test_excluded_mapping_may_record_completed_review_without_becoming_selected(
    registry: FinancialFieldRegistry,
) -> None:
    excluded = replace(
        _mapping(registry),
        selection_decision=SelectionDecision.EXCLUDED,
        publication_state=PublicationState.REJECTED,
        review_status=ReviewStatus.REVIEWER_APPROVED,
    )

    assert excluded.field_id == "total_assets"
    assert excluded.issuer_id == "tfc"


def test_registry_rejects_blank_empty_and_duplicate_identity(
    registry: FinancialFieldRegistry,
) -> None:
    mapping = _mapping(registry)
    with pytest.raises(FinancialDiscoveryError, match="requires a version and mappings"):
        FinancialFieldRegistry(version=" ", mappings=(mapping,))
    with pytest.raises(FinancialDiscoveryError, match="requires a version and mappings"):
        FinancialFieldRegistry(version="v", mappings=())
    with pytest.raises(FinancialDiscoveryError, match="repeats an issuer/field"):
        FinancialFieldRegistry(version="v", mappings=(mapping, mapping))


def test_registry_loader_wraps_second_read_errors(
    monkeypatch: pytest.MonkeyPatch,
    registry: FinancialFieldRegistry,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        XbrlMappingRegistry,
        "from_yaml",
        classmethod(lambda _cls, _path: SimpleNamespace(version="v", mappings=registry.mappings)),
    )
    with pytest.raises(FinancialDiscoveryError, match="unavailable or invalid"):
        FinancialFieldRegistry.from_yaml(tmp_path / "absent.yaml")
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("mappings: [", encoding="utf-8")
    with pytest.raises(FinancialDiscoveryError, match="unavailable or invalid"):
        FinancialFieldRegistry.from_yaml(malformed)


def test_registry_loader_rejects_mismatched_counts_and_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
    registry: FinancialFieldRegistry,
    tmp_path: Path,
) -> None:
    mapping = _mapping(registry)
    monkeypatch.setattr(
        XbrlMappingRegistry,
        "from_yaml",
        classmethod(lambda _cls, _path: SimpleNamespace(version="v", mappings=(mapping.xbrl,))),
    )
    mismatch = tmp_path / "mismatch.yaml"
    mismatch.write_text("mappings: []\n", encoding="utf-8")
    with pytest.raises(FinancialDiscoveryError, match="counts disagree"):
        FinancialFieldRegistry.from_yaml(mismatch)
    bad_root = tmp_path / "root.yaml"
    bad_root.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(FinancialDiscoveryError, match="root must be a string-keyed mapping"):
        FinancialFieldRegistry.from_yaml(bad_root)


def test_config_helpers_reject_unsafe_types_and_enum_values(
    registry: FinancialFieldRegistry,
) -> None:
    with pytest.raises(FinancialDiscoveryError, match="string-keyed mapping"):
        discovery._as_mapping({1: "bad"}, location="x")
    with pytest.raises(FinancialDiscoveryError, match="must be a sequence"):
        discovery._as_sequence("not-a-sequence", location="x")
    with pytest.raises(FinancialDiscoveryError, match="nonblank strings"):
        discovery._string_sequence(["10-Q", ""], location="x")
    with pytest.raises(FinancialDiscoveryError, match="must be a nonblank string"):
        discovery._required_string({}, "missing", location="x")

    payload = yaml.safe_load(_MAPPING_PATH.read_text(encoding="utf-8"))["mappings"][0]
    payload["classification"] = "NOT_A_CLASSIFICATION"
    with pytest.raises(FinancialDiscoveryError, match="unsupported financial-field enum"):
        discovery._selected_mapping_from_payload(
            payload,
            xbrl=_mapping(registry).xbrl,
            location="root.mappings[0]",
        )


def test_adapter_discovery_preserves_exact_raw_string_and_complete_locator(
    registry: FinancialFieldRegistry,
) -> None:
    raw_value = "000556023000000.000000000000000000"
    fact = _adapter_fact(raw_value=raw_value, scale="0", precision="INF")

    (result,) = discover_filing_fields(_filing(fact), form="10-Q", registry=registry)

    assert result.status is AvailabilityStatus.AVAILABLE
    assert result.ambiguities == ()
    (candidate,) = result.candidates
    assert candidate.raw_value == raw_value
    assert isinstance(candidate.raw_value, str)
    assert candidate.accession_number == _ACCESSION
    assert candidate.source_document == _DOCUMENT
    assert candidate.source_url == _URL
    assert candidate.qualified_concept == "us-gaap:Assets"
    assert candidate.original_labels == ("Total assets",)
    assert candidate.context_ref == "c-7"
    assert candidate.entity_identifier == _CIK
    assert candidate.period_type == "instant"
    assert candidate.period_instant == "2026-06-30"
    assert candidate.dimensions == ()
    assert candidate.unit_measure == "iso4217:USD"
    assert candidate.decimals == "-6"
    assert candidate.scale == "0"
    assert candidate.precision == "INF"
    assert candidate.fact_ids == ("fact-1",)
    assert candidate.instance_ids == ("instance-1",)
    assert candidate.review_status is ReviewStatus.REVIEW_REQUIRED


def test_adapter_duplicate_source_objects_coalesce_and_retain_union_lineage(
    registry: FinancialFieldRegistry,
) -> None:
    first = _adapter_fact(fact_id="fact-2", instance_id="instance-2", original_label=None)
    second = replace(
        first,
        fact_id="fact-1",
        instance_id="instance-1",
        original_label="Total assets",
    )

    (result,) = discover_filing_fields(_filing(first, second), form="10-Q", registry=registry)

    assert result.status is AvailabilityStatus.AVAILABLE
    (candidate,) = result.candidates
    assert candidate.source_object_count == 2
    assert candidate.fact_ids == ("fact-1", "fact-2")
    assert candidate.instance_ids == ("instance-1", "instance-2")
    assert candidate.original_labels == ("Total assets",)


def test_adapter_conflicting_raw_values_for_same_semantics_are_ambiguous(
    registry: FinancialFieldRegistry,
) -> None:
    facts = (_adapter_fact(raw_value="1"), _adapter_fact(raw_value="2", fact_id="fact-2"))

    (result,) = discover_filing_fields(_filing(*facts), form="10-Q", registry=registry)

    assert result.status is AvailabilityStatus.AMBIGUOUS
    assert len(result.candidates) == 2
    assert result.ambiguities == ("CONFLICTING_RAW_VALUES_FOR_EXACT_SEMANTIC_PERIOD",)


@pytest.mark.parametrize(
    "fact",
    [
        _adapter_fact(taxonomy="dei"),
        _adapter_fact(concept="Liabilities"),
        _adapter_fact(period_type="duration", period_instant=None),
        _adapter_fact(dimensions=(XbrlDimension(axis="us-gaap:Axis", member="us-gaap:Member"),)),
        _adapter_fact(unit=None),
        _adapter_fact(unit_measure=None),
        _adapter_fact(unit_measure="shares"),
        _adapter_fact(decimals="-3"),
    ],
)
def test_adapter_wrong_fact_semantics_are_not_found(
    registry: FinancialFieldRegistry,
    fact: AdapterXbrlFact,
) -> None:
    (result,) = discover_filing_fields(_filing(fact), form="10-Q", registry=registry)
    assert result.status is AvailabilityStatus.NOT_FOUND
    assert result.candidates == ()


def test_adapter_wrong_form_and_cik_return_no_field_results(
    registry: FinancialFieldRegistry,
) -> None:
    assert discover_filing_fields(_filing(_adapter_fact()), form="8-K", registry=registry) == ()
    assert (
        discover_filing_fields(
            _filing(_adapter_fact(), cik="0000000000"), form="10-Q", registry=registry
        )
        == ()
    )
    with pytest.raises(FinancialDiscoveryError, match="exact filing form"):
        discover_filing_fields(_filing(_adapter_fact()), form=" ", registry=registry)


def test_adapter_unit_canonicalization_supports_xbrli_prefix_and_custom_units() -> None:
    assert discovery._canonical_unit_measure("xbrli:shares") == "shares"
    assert discovery._canonical_unit_measure("custom:USD") == "custom:USD"
    assert discovery._canonical_unit_measure(None) is None


def test_parsed_discovery_preserves_raw_display_decimal_hash_and_source_locators(
    registry: FinancialFieldRegistry,
) -> None:
    fact = _parsed_fact()

    (result,) = discover_parsed_filing_fields(
        (fact,),
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        registry=registry,
    )

    assert result.status is AvailabilityStatus.AVAILABLE
    (candidate,) = result.candidates
    assert candidate.raw_value == "556023"
    assert candidate.normalized_value == Decimal(556023000000)
    assert isinstance(candidate.normalized_value, Decimal)
    assert candidate.evidence_id == _EVIDENCE_ID
    assert candidate.source_locators == (fact.locator,)
    assert candidate.source_element_ids == ("fact-1",)
    assert candidate.context_ref == "c-7"
    assert candidate.period_end == date(2026, 6, 30)
    assert candidate.decimals == -6
    assert candidate.scale == Decimal(1)
    assert candidate.review_status is ReviewStatus.REVIEW_REQUIRED


def test_parsed_duplicate_semantics_coalesce_and_conflicts_are_ambiguous(
    registry: FinancialFieldRegistry,
) -> None:
    first = _parsed_fact(source_element_id="fact-2", locator="locator-2")
    duplicate = replace(first, source_element_id="fact-1", locator="locator-1")
    (coalesced,) = discover_parsed_filing_fields(
        (first, duplicate),
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        registry=registry,
    )
    assert coalesced.status is AvailabilityStatus.AVAILABLE
    assert coalesced.candidates[0].source_object_count == 2
    assert coalesced.candidates[0].source_element_ids == ("fact-1", "fact-2")
    assert coalesced.candidates[0].source_locators == ("locator-2", "locator-1")

    conflicting = replace(first, raw_value="556024", value=Decimal(556024000000))
    (ambiguous,) = discover_parsed_filing_fields(
        (first, conflicting),
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        registry=registry,
    )
    assert ambiguous.status is AvailabilityStatus.AMBIGUOUS
    assert ambiguous.ambiguities == ("CONFLICTING_RAW_VALUES_FOR_EXACT_SEMANTIC_PERIOD",)


@pytest.mark.parametrize(
    "fact",
    [
        _parsed_fact(source=XbrlSource.SEC_COMPANY_FACTS),
        _parsed_fact(issuer_id="pfsi"),
        _parsed_fact(cik="0001745916"),
        _parsed_fact(taxonomy="dei"),
        _parsed_fact(concept="Liabilities"),
        _parsed_fact(unit="shares"),
        _parsed_fact(decimals=-3),
        _parsed_fact(period_type=XbrlPeriodType.DURATION),
        _parsed_fact(dimensions=(DimensionMember("us-gaap:Axis", "us-gaap:Member"),)),
    ],
)
def test_parsed_wrong_fact_semantics_are_not_found(
    registry: FinancialFieldRegistry,
    fact: XbrlFact,
) -> None:
    (result,) = discover_parsed_filing_fields(
        (fact,),
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        registry=registry,
    )
    assert result.status is AvailabilityStatus.NOT_FOUND


def test_retained_document_route_selects_only_assets_and_converts_directly_to_decimal(
    registry: FinancialFieldRegistry,
) -> None:
    (result,) = discover_retained_document_fields(
        _inline_xbrl(),
        issuer_id="tfc",
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        filed=date(2026, 7, 31),
        registry=registry,
    )

    assert result.status is AvailabilityStatus.AVAILABLE
    (candidate,) = result.candidates
    assert candidate.raw_value == "556023"
    assert candidate.normalized_value == Decimal(556023000000)
    assert candidate.source_element_ids == ("assets-1",)
    assert "element_id=assets-1" in candidate.source_locators[0]
    assert candidate.source_object_count == 1


def test_retained_document_route_fails_selected_unsupported_transform_and_issuer_mismatch(
    registry: FinancialFieldRegistry,
) -> None:
    with pytest.raises(XbrlDataError, match="unsupported transformation"):
        discover_retained_document_fields(
            _inline_xbrl(selected_format="ixt:unsupported"),
            issuer_id="tfc",
            cik=_CIK,
            evidence_id=_EVIDENCE_ID,
            accession_number=_ACCESSION,
            source_document=_DOCUMENT,
            source_url=_URL,
            form="10-Q",
            filed=date(2026, 7, 31),
            registry=registry,
        )
    with pytest.raises(FinancialDiscoveryError, match="issuer does not match"):
        discover_retained_document_fields(
            _inline_xbrl(),
            issuer_id="pfsi",
            cik=_CIK,
            evidence_id=_EVIDENCE_ID,
            accession_number=_ACCESSION,
            source_document=_DOCUMENT,
            source_url=_URL,
            form="10-Q",
            filed=date(2026, 7, 31),
            registry=registry,
        )


def _exercise_raw_locator_invalid_change(
    candidate: RawFilingFactLocator,
    change: str,
) -> None:
    if change == "blank_accession_number":
        replace(candidate, accession_number="")
    elif change == "wrong_normalized_value_type":
        invalid_candidate = replace(candidate)
        object.__setattr__(invalid_candidate, "normalized_value", "1")
        invalid_candidate.__post_init__()
    elif change == "nonfinite_normalized_value":
        replace(candidate, normalized_value=Decimal("NaN"))
    elif change == "zero_scale":
        replace(candidate, scale=Decimal(0))
    elif change == "infinite_scale":
        replace(candidate, scale=Decimal("Infinity"))
    elif change == "missing_source_locators":
        replace(candidate, source_object_count=0, source_locators=())
    elif change == "mismatched_source_object_count":
        replace(candidate, source_object_count=2)
    elif change == "approved_review_status":
        replace(candidate, review_status=ReviewStatus.REVIEWER_APPROVED)
    else:
        raise AssertionError


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("blank_accession_number", "nonblank lineage"),
        ("wrong_normalized_value_type", "finite Decimal"),
        ("nonfinite_normalized_value", "finite Decimal"),
        ("zero_scale", "positive finite Decimal"),
        ("infinite_scale", "positive finite Decimal"),
        ("missing_source_locators", "complete source-object"),
        ("mismatched_source_object_count", "complete source-object"),
        ("approved_review_status", "cannot approve"),
    ],
)
def test_raw_locator_invariants_fail_closed(
    registry: FinancialFieldRegistry,
    change: str,
    message: str,
) -> None:
    result = discover_parsed_filing_fields(
        (_parsed_fact(),),
        cik=_CIK,
        evidence_id=_EVIDENCE_ID,
        accession_number=_ACCESSION,
        source_document=_DOCUMENT,
        source_url=_URL,
        form="10-Q",
        registry=registry,
    )[0]
    with pytest.raises(FinancialDiscoveryError, match=message):
        _exercise_raw_locator_invalid_change(result.candidates[0], change)


def _exercise_adapter_locator_invalid_change(
    candidate: ProposedFactLocator,
    change: str,
) -> None:
    if change == "blank_raw_value":
        replace(candidate, raw_value="")
    elif change == "zero_source_object_count":
        replace(candidate, source_object_count=0)
    elif change == "approved_review_status":
        replace(candidate, review_status=ReviewStatus.REVIEWER_APPROVED)
    else:
        raise AssertionError


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("blank_raw_value", "nonblank lineage"),
        ("zero_source_object_count", "at least one source object"),
        ("approved_review_status", "cannot approve"),
    ],
)
def test_adapter_locator_invariants_fail_closed(
    registry: FinancialFieldRegistry,
    change: str,
    message: str,
) -> None:
    candidate = discover_filing_fields(_filing(_adapter_fact()), form="10-Q", registry=registry)[
        0
    ].candidates[0]
    with pytest.raises(FinancialDiscoveryError, match=message):
        _exercise_adapter_locator_invalid_change(candidate, change)


@pytest.mark.parametrize(
    ("factory", "status", "candidate", "ambiguities", "message"),
    [
        (FieldDiscovery, AvailabilityStatus.NOT_FOUND, True, (), "NOT_FOUND discovery"),
        (FieldDiscovery, AvailabilityStatus.AVAILABLE, False, (), "AVAILABLE discovery"),
        (FieldDiscovery, AvailabilityStatus.AVAILABLE, True, ("x",), "AVAILABLE discovery"),
        (FieldDiscovery, AvailabilityStatus.AMBIGUOUS, False, ("x",), "AMBIGUOUS discovery"),
        (FieldDiscovery, AvailabilityStatus.AMBIGUOUS, True, (), "AMBIGUOUS discovery"),
        (RawFieldDiscovery, AvailabilityStatus.NOT_FOUND, True, (), "NOT_FOUND raw discovery"),
        (RawFieldDiscovery, AvailabilityStatus.AVAILABLE, False, (), "AVAILABLE raw discovery"),
        (RawFieldDiscovery, AvailabilityStatus.AVAILABLE, True, ("x",), "AVAILABLE raw discovery"),
        (RawFieldDiscovery, AvailabilityStatus.AMBIGUOUS, False, ("x",), "AMBIGUOUS raw discovery"),
        (RawFieldDiscovery, AvailabilityStatus.AMBIGUOUS, True, (), "AMBIGUOUS raw discovery"),
    ],
)
def test_discovery_result_invariants_fail_closed(  # noqa: PLR0913, PLR0917
    registry: FinancialFieldRegistry,
    factory: type[FieldDiscovery | RawFieldDiscovery],
    status: AvailabilityStatus,
    candidate: bool,  # noqa: FBT001 - parameterized boolean describes invalid fixture shape.
    ambiguities: tuple[str, ...],
    message: str,
) -> None:
    if factory is FieldDiscovery:
        adapter_candidate = discover_filing_fields(
            _filing(_adapter_fact()), form="10-Q", registry=registry
        )[0].candidates[0]
        with pytest.raises(FinancialDiscoveryError, match=message):
            FieldDiscovery(
                mapping=_mapping(registry),
                accession_number=_ACCESSION,
                form="10-Q",
                candidates=(adapter_candidate,) if candidate else (),
                status=status,
                ambiguities=ambiguities,
            )
    else:
        raw_candidate = discover_parsed_filing_fields(
            (_parsed_fact(),),
            cik=_CIK,
            evidence_id=_EVIDENCE_ID,
            accession_number=_ACCESSION,
            source_document=_DOCUMENT,
            source_url=_URL,
            form="10-Q",
            registry=registry,
        )[0].candidates[0]
        with pytest.raises(FinancialDiscoveryError, match=message):
            RawFieldDiscovery(
                mapping=_mapping(registry),
                accession_number=_ACCESSION,
                form="10-Q",
                candidates=(raw_candidate,) if candidate else (),
                status=status,
                ambiguities=ambiguities,
            )


def test_golden_manifest_has_exactly_four_bounded_honestly_reviewed_cases() -> None:
    manifest = yaml.safe_load(_GOLDEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    case_ids = {case["case_id"] for case in cases}

    assert len(cases) == 4
    assert case_ids == {
        "tfc-2025-annual-total-assets",
        "tfc-2026q2-quarterly-total-assets",
        "pfsi-2025-annual-total-assets",
        "pfsi-2026q2-quarterly-total-assets",
    }
    assert Counter(case["issuer_id"] for case in cases) == {"tfc": 2, "pfsi": 2}
    assert Counter(case["case_kind"] for case in cases) == {
        "ANNUAL_CORE_FINANCIAL": 2,
        "QUARTERLY_CORE_FINANCIAL": 2,
    }
    assert all(case["field_id"] == "total_assets" for case in cases)
    assert all(case["classification"] == "CORE_FINANCIAL" for case in cases)
    assert all(case["amendment"] is False for case in cases)
    assert manifest["publication_authority"] is False
    assert manifest["generated_extractor_output_is_expectation"] is False

    quarterly_hashes = {
        "tfc-2026q2-quarterly-total-assets": (
            "8b4e75df610503670a55802f4a29e36fae8bb9195b78abddd0272b11f1d0efed",
            6_452_566,
        ),
        "pfsi-2026q2-quarterly-total-assets": (
            "9b0062e5c2d62e2abe50a89a5b6f606140d6eaee6e3ef27ee824529292642155",
            7_492_215,
        ),
    }
    for case in cases:
        source = case["edgartools_source"]
        if source["acquisition_status"] in {
            "RETAINED",
            "RETAINED_AND_DETERMINISTICALLY_PARSED",
            "QUALIFIED",
            "APPROVED",
        }:
            assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
            assert source["byte_length"] > 0
            assert source["representation"] == "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
        if case["case_id"] in quarterly_hashes:
            assert (source["sha256"], source["byte_length"]) == quarterly_hashes[case["case_id"]]

    approved = set(manifest["approved_expectations"])
    if approved:
        assert approved == case_ids
        assert manifest["status"] in {"INDEPENDENTLY_CROSS_CHECKED", "REVIEWER_APPROVED"}
        for case in cases:
            assert case["review_status"] in {
                "INDEPENDENTLY_CROSS_CHECKED",
                "REVIEWER_APPROVED",
            }
            assert (
                "independent" in yaml.safe_dump(case).lower()
                or "reviewer" in yaml.safe_dump(case).lower()
            )
    else:
        assert manifest["status"] == "REVIEW_REQUIRED"
        assert all(case["review_status"] == "REVIEW_REQUIRED" for case in cases)
        assert all(case.get("review_gap") for case in cases)


def test_financial_availability_has_16_rows_and_honest_pfsi_stop_classifications() -> None:
    with _AVAILABILITY_PATH.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 16
    assert Counter(row["issuer_id"] for row in rows) == {"tfc": 8, "pfsi": 8}
    assert all(row["field_id"] == "total_assets" for row in rows)
    assert all(row["selection_decision"] == "SELECTED" for row in rows)
    assert all(ReviewStatus(row["review_status"]) for row in rows)
    assert all("NOT_FOUND" not in row["availability_status"] for row in rows)
    assert Counter(row["availability_status"] for row in rows) == {
        "FILING_METADATA_AVAILABLE_FACT_NOT_INSPECTED": 12,
        "QUALIFIED_RAW_DOCUMENT_INLINE_XBRL": 4,
    }
    assert Counter(row["cross_check_status"] for row in rows) == {
        "NOT_PERFORMED": 12,
        "EXACT_DISTINCT_REPRESENTATION_MATCH": 4,
    }
    assert Counter(row["review_status"] for row in rows) == {
        ReviewStatus.REVIEW_REQUIRED: 12,
        ReviewStatus.REVIEWER_APPROVED: 4,
    }
