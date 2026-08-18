"""Deterministic, socket-blocked tests for the Phase 2 XBRL path."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest

from mortgage_servicing_dashboard.financial_discovery import (
    AvailabilityStatus,
    FinancialFieldRegistry,
    discover_retained_document_fields,
)
from mortgage_servicing_dashboard.xbrl import (
    DecisionDisposition,
    DimensionMember,
    MappingDecision,
    ReconciliationDecision,
    ReconciliationValue,
    SecCompanyFactsAdapter,
    SecFilingXbrlAdapter,
    XbrlConceptMapping,
    XbrlDataError,
    XbrlFact,
    XbrlMappingRegistry,
    XbrlMethodology,
    XbrlPeriodType,
    XbrlSource,
    apply_mapping,
    map_facts,
    reconcile_values,
)

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config" / "xbrl_concepts.yaml"
_FINANCIAL_CONFIG = _ROOT / "config" / "financial_fields.v1.yaml"
_FIXTURES = _ROOT / "tests" / "fixtures" / "xbrl"


class _FilingParseKwargs(TypedDict):
    issuer_id: str
    evidence_id: str
    accession: str
    form: str
    filed: date


def _registry() -> XbrlMappingRegistry:
    return XbrlMappingRegistry.from_yaml(_CONFIG)


def _company_facts() -> tuple[XbrlFact, ...]:
    return SecCompanyFactsAdapter().parse(
        (_FIXTURES / "synthetic_tfc_companyfacts.json").read_bytes(),
        issuer_id="tfc",
        evidence_id="evidence:synthetic-tfc-companyfacts",
    )


def _filing_facts() -> tuple[XbrlFact, ...]:
    return SecFilingXbrlAdapter().parse(
        (_FIXTURES / "synthetic_pfsi_filing_inline_xbrl.xhtml").read_bytes(),
        issuer_id="pfsi",
        evidence_id="evidence:synthetic-pfsi-inline-xbrl",
        accession="0001745916-26-000001",
        form="10-Q",
        filed=date(2026, 7, 31),
    )


def test_versioned_registry_is_per_issuer_metric_and_exact_context() -> None:
    registry = _registry()

    assert registry.version == "1.0.0"
    assert len(registry.mappings) == 4
    assert {mapping.issuer_id for mapping in registry.mappings} == {"tfc", "pfsi"}
    assert all(isinstance(mapping.scale, Decimal) for mapping in registry.mappings)
    assert all(mapping.decimals == -6 for mapping in registry.mappings)
    assert registry.for_metric(issuer_id="missing", metric_id="owned_msr_upb") == ()

    owned = registry.for_metric(issuer_id="pfsi", metric_id="owned_msr_upb")
    assert len(owned) == 1
    assert owned[0].qualified_concept == (
        "pfsi:UnpaidPrincipalBalanceOfLoansUnderlyingOwnedMortgageServicingRights"
    )
    assert owned[0].dimensions == (
        DimensionMember(
            dimension="pfsi:ServicingPortfolioAxis",
            member="pfsi:OwnedMortgageServicingRightsMember",
        ),
    )
    assert owned[0].eligible_sources == (XbrlSource.SEC_FILING_XBRL,)


def test_company_facts_adapter_is_exact_deterministic_and_mapped() -> None:
    facts = _company_facts()
    replay = _company_facts()

    assert facts == replay
    assert [fact.qualified_concept for fact in facts] == [
        "tfc:LoansServicedForOthersUnpaidPrincipalBalance",
        "us-gaap:MortgageServicingRightsFairValue",
    ]
    assert [fact.value for fact in facts] == [
        Decimal(298658000000),
        Decimal(2450000000),
    ]
    assert all(fact.scale == Decimal(1) for fact in facts)
    assert all(fact.decimals == -6 for fact in facts)
    assert all(fact.period_type is XbrlPeriodType.INSTANT for fact in facts)
    assert all(fact.period_start is None for fact in facts)
    assert all(fact.period_end == date(2026, 6, 30) for fact in facts)
    assert all(fact.cik == "0000092230" for fact in facts)
    assert all(fact.source is XbrlSource.SEC_COMPANY_FACTS for fact in facts)

    decisions = map_facts(facts, _registry())
    assert len(decisions) == 2
    assert all(item.disposition is DecisionDisposition.VALIDATED for item in decisions)
    candidates = [item.candidate for item in decisions]
    assert all(candidate is not None for candidate in candidates)
    first = candidates[0]
    assert first is not None
    assert first.normalized_value == Decimal(298658000000)
    assert first.methodology is XbrlMethodology.SEC_COMPANY_FACTS_XBRL
    assert first.extraction_method == "deterministic_sec_company_facts"
    assert len(first.candidate_id) == 64


def test_filing_inline_xbrl_resolves_scale_period_and_dimensions() -> None:
    facts = _filing_facts()

    assert [fact.value for fact in facts] == [
        Decimal(410500000000),
        Decimal(312400000),
    ]
    assert all(fact.scale == Decimal(1000000) for fact in facts)
    assert facts[0].period_type is XbrlPeriodType.INSTANT
    assert facts[0].period_start is None
    assert facts[0].dimensions == (
        DimensionMember(
            "pfsi:ServicingPortfolioAxis",
            "pfsi:OwnedMortgageServicingRightsMember",
        ),
    )
    assert facts[1].period_type is XbrlPeriodType.DURATION
    assert facts[1].period_start == date(2026, 4, 1)
    assert facts[1].period_end == date(2026, 6, 30)
    assert facts[1].dimensions == (
        DimensionMember(
            "us-gaap:StatementBusinessSegmentsAxis",
            "pfsi:ServicingSegmentMember",
        ),
    )
    assert all(fact.cik == "0001745916" for fact in facts)

    decisions = map_facts(facts, _registry())
    assert [item.disposition for item in decisions] == [
        DecisionDisposition.VALIDATED,
        DecisionDisposition.VALIDATED,
    ]
    candidates = [item.candidate for item in decisions]
    assert [item.metric_id for item in candidates if item is not None] == [
        "owned_msr_upb",
        "servicing_fee_income",
    ]
    assert all(
        item is not None and item.methodology is XbrlMethodology.SEC_FILING_XBRL
        for item in candidates
    )
    assert all(
        item is not None and item.extraction_method == "deterministic_sec_filing_xbrl"
        for item in candidates
    )


def test_inline_xbrl_skips_nil_and_transforms_fixed_zero_exactly() -> None:
    payload = b"".join(
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml" ',
            b'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" ',
            b'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" ',
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ',
            b'xmlns:xbrli="http://www.xbrl.org/2003/instance">',
            b'<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="sec">92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
            b'<ix:nonFraction name="us-gaap:NilAmount" contextRef="I" unitRef="USD" ',
            b'xsi:nil="true" decimals="-6" />',
            b'<ix:nonFraction name="us-gaap:ZeroAmount" contextRef="I" unitRef="USD" ',
            b'decimals="-6" scale="6" format="ixt:fixed-zero">\xe2\x80\x94',
            b"</ix:nonFraction>",
            b'<ix:nonFraction name="us-gaap:Assets" contextRef="I" unitRef="USD" ',
            b'decimals="-6" scale="6" sign="-" format="ixt:num-dot-decimal">',
            b"547,538</ix:nonFraction></html>",
        )
    )

    facts = SecFilingXbrlAdapter().parse(
        payload,
        issuer_id="tfc",
        evidence_id="evidence:inline-transformations",
        accession="0000092230-26-000030",
        form="10-K",
        filed=date(2026, 2, 20),
    )

    assert [fact.qualified_concept for fact in facts] == [
        "us-gaap:ZeroAmount",
        "us-gaap:Assets",
    ]
    assert [fact.raw_value for fact in facts] == ["—", "547,538"]
    assert [fact.value for fact in facts] == [Decimal(0), Decimal(-547538000000)]
    assert [fact.scale for fact in facts] == [Decimal(1000000), Decimal(1000000)]
    assert all(isinstance(fact.value, Decimal) for fact in facts)


@pytest.mark.parametrize(
    ("format_name", "error"),
    [
        ("invalid", "invalid transformation format"),
        ("ixt:num-comma-decimal", "unsupported transformation"),
        ("other:num-dot-decimal", "unsupported transformation"),
    ],
)
def test_inline_xbrl_rejects_malformed_or_unknown_transformations(
    format_name: str,
    error: str,
) -> None:
    payload = (
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" '
        'xmlns:other="https://synthetic.example.test/transform" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance">'
        '<xbrli:context id="I"><xbrli:entity><xbrli:identifier>92230'
        "</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31"
        "</xbrli:instant></xbrli:period></xbrli:context>"
        '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        '<ix:nonFraction name="us-gaap:Assets" contextRef="I" unitRef="USD" '
        f'format="{format_name}">1</ix:nonFraction></html>'
    ).encode()

    with pytest.raises(XbrlDataError, match=error):
        SecFilingXbrlAdapter().parse(
            payload,
            issuer_id="tfc",
            evidence_id="evidence:unknown-transformation",
            accession="0000092230-26-000030",
            form="10-K",
            filed=date(2026, 2, 20),
        )


def test_inline_xbrl_rejects_malformed_nil_boolean() -> None:
    payload = b"".join(
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml" ',
            b'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" ',
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ',
            b'xmlns:xbrli="http://www.xbrl.org/2003/instance">',
            b'<xbrli:context id="I"><xbrli:entity><xbrli:identifier>92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
            b'<ix:nonFraction name="us-gaap:Assets" contextRef="I" unitRef="USD" ',
            b'xsi:nil="yes" />',
            b"</html>",
        )
    )

    with pytest.raises(XbrlDataError, match="xsi:nil must be"):
        SecFilingXbrlAdapter().parse(
            payload,
            issuer_id="tfc",
            evidence_id="evidence:invalid-nil",
            accession="0000092230-26-000030",
            form="10-K",
            filed=date(2026, 2, 20),
        )


def test_inline_xbrl_concept_filter_skips_only_unrelated_unknown_transform() -> None:
    payload = b"".join(
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml" ',
            b'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" ',
            b'xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31" ',
            b'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" ',
            b'xmlns:xbrli="http://www.xbrl.org/2003/instance" ',
            b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" ',
            b'xmlns:test="https://synthetic.example.test/taxonomy">',
            b'<xbrli:context id="I"><xbrli:entity><xbrli:identifier>92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:context id="unused"><xbrli:entity><xbrli:identifier>92230',
            b"</xbrli:identifier><xbrli:segment><xbrldi:typedMember ",
            b'dimension="test:TypedAxis"><test:Value>unused</test:Value>',
            b"</xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period>",
            b"<xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
            b'<ix:nonFraction name="us-gaap:NumberOfReportableSegments" contextRef="I" ',
            b'unitRef="USD" format="ixt-sec:numwordsen">two</ix:nonFraction>',
            b'<ix:nonFraction name="us-gaap:Assets" contextRef="I" unitRef="USD" ',
            b'decimals="-6" scale="6" format="ixt:num-dot-decimal">547,538',
            b"</ix:nonFraction></html>",
        )
    )

    adapter = SecFilingXbrlAdapter()
    parse_kwargs: _FilingParseKwargs = {
        "issuer_id": "tfc",
        "evidence_id": "evidence:concept-filter",
        "accession": "0000092230-26-000030",
        "form": "10-K",
        "filed": date(2026, 2, 20),
    }
    facts = adapter.parse(
        payload,
        **parse_kwargs,
        qualified_concepts=frozenset({"us-gaap:Assets"}),
    )

    assert [(fact.qualified_concept, fact.raw_value, fact.value) for fact in facts] == [
        ("us-gaap:Assets", "547,538", Decimal(547538000000))
    ]
    assert (
        adapter.parse(
            payload,
            **parse_kwargs,
            qualified_concepts=frozenset({"us-gaap:Liabilities"}),
        )
        == ()
    )
    with pytest.raises(XbrlDataError, match="unsupported transformation"):
        adapter.parse(
            payload,
            **parse_kwargs,
            qualified_concepts=frozenset({"us-gaap:NumberOfReportableSegments"}),
        )


def test_inline_xbrl_rejects_invalid_concept_filter() -> None:
    with pytest.raises(XbrlDataError, match="taxonomy-qualified"):
        SecFilingXbrlAdapter().parse(
            b"<root />",
            issuer_id="tfc",
            evidence_id="evidence:invalid-filter",
            accession="0000092230-26-000030",
            form="10-K",
            filed=date(2026, 2, 20),
            qualified_concepts=frozenset({"Assets"}),
        )


def test_raw_document_discovery_selects_only_mapped_concept_and_preserves_lineage() -> None:
    payload = b"".join(
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml" ',
            b'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" ',
            b'xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31" ',
            b'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" ',
            b'xmlns:xbrli="http://www.xbrl.org/2003/instance">',
            b'<xbrli:context id="c-9"><xbrli:entity><xbrli:identifier>92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
            b'<ix:nonFraction id="unrelated" name="us-gaap:NumberOfReportableSegments" ',
            b'contextRef="c-9" unitRef="USD" format="ixt-sec:numwordsen">two',
            b"</ix:nonFraction>",
            b'<ix:nonFraction id="f-103" name="us-gaap:Assets" contextRef="c-9" ',
            b'unitRef="USD" decimals="-6" scale="6" format="ixt:num-dot-decimal">',
            b"547,538</ix:nonFraction></html>",
        )
    )
    registry = FinancialFieldRegistry.from_yaml(_FINANCIAL_CONFIG)

    results = discover_retained_document_fields(
        payload,
        issuer_id="tfc",
        cik="0000092230",
        evidence_id="evidence:raw-document",
        accession_number="0000092230-26-000030",
        source_document="tfc-20251231.htm",
        source_url="https://www.sec.gov/Archives/example/tfc-20251231.htm",
        form="10-K",
        filed=date(2026, 2, 20),
        registry=registry,
    )

    assert len(results) == 1
    assert results[0].status is AvailabilityStatus.AVAILABLE
    assert len(results[0].candidates) == 1
    candidate = results[0].candidates[0]
    assert candidate.source_element_ids == ("f-103",)
    assert candidate.raw_value == "547,538"
    assert candidate.normalized_value == Decimal(547538000000)
    assert candidate.context_ref == "c-9"
    assert candidate.scale == Decimal(1000000)
    assert candidate.source_object_count == 1
    assert isinstance(candidate.normalized_value, Decimal)


def test_raw_document_discovery_returns_not_found_for_missing_selected_fact() -> None:
    payload = b"".join(
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml" ',
            b'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" ',
            b'xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31" ',
            b'xmlns:xbrli="http://www.xbrl.org/2003/instance">',
            b'<xbrli:context id="c-9"><xbrli:entity><xbrli:identifier>92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
            b'<ix:nonFraction name="us-gaap:NumberOfReportableSegments" contextRef="c-9" ',
            b'unitRef="USD" format="ixt-sec:numwordsen">two</ix:nonFraction></html>',
        )
    )

    results = discover_retained_document_fields(
        payload,
        issuer_id="tfc",
        cik="0000092230",
        evidence_id="evidence:raw-document-missing",
        accession_number="0000092230-26-000030",
        source_document="tfc-20251231.htm",
        source_url="https://www.sec.gov/Archives/example/tfc-20251231.htm",
        form="10-K",
        filed=date(2026, 2, 20),
        registry=FinancialFieldRegistry.from_yaml(_FINANCIAL_CONFIG),
    )

    assert len(results) == 1
    assert results[0].status is AvailabilityStatus.NOT_FOUND
    assert results[0].candidates == ()


def test_mapping_mismatch_quarantines_instead_of_guessing() -> None:
    fact = _company_facts()[0]
    configured = _registry().for_metric(
        issuer_id="pfsi",
        metric_id="servicing_fee_income",
    )[0]
    incompatible = replace(
        configured,
        unit="count",
        decimals=-3,
        eligible_sources=(XbrlSource.SEC_FILING_XBRL,),
    )

    decision = apply_mapping(fact, incompatible, mapping_version="1.0.0")

    assert decision.disposition is DecisionDisposition.QUARANTINED
    assert decision.candidate is None
    assert decision.reasons == (
        "ISSUER_MISMATCH",
        "CIK_MISMATCH",
        "CONCEPT_MISMATCH",
        "SOURCE_NOT_ELIGIBLE",
        "UNIT_MISMATCH",
        "PERIOD_TYPE_MISMATCH",
        "DIMENSION_CONTEXT_MISMATCH",
        "DECIMALS_MISMATCH",
    )
    unrelated = replace(fact, concept="UnmappedSyntheticConcept")
    assert map_facts((unrelated,), _registry()) == ()


def test_company_facts_without_decimals_uses_reviewed_mapping_precision() -> None:
    fact = replace(_company_facts()[0], decimals=None)
    mapping = _registry().for_metric(
        issuer_id="tfc",
        metric_id="servicing_for_others_upb",
    )[0]

    decision = apply_mapping(fact, mapping, mapping_version="1.0.0")

    assert decision.disposition is DecisionDisposition.VALIDATED
    assert decision.candidate is not None
    assert decision.candidate.reported_decimals == -6


def test_reconciliation_exact_match_and_value_mismatch_have_no_preference() -> None:
    decision = map_facts((_company_facts()[0],), _registry())[0]
    candidate = decision.candidate
    assert candidate is not None
    xbrl = ReconciliationValue.from_xbrl(candidate)
    exhibit = replace(
        xbrl,
        methodology=XbrlMethodology.SEC_FILING_EXHIBIT,
        evidence_id="evidence:synthetic-exhibit",
    )

    matched = reconcile_values(xbrl, exhibit)
    assert matched.disposition is DecisionDisposition.VALIDATED
    assert matched.code == "RECONCILIATION_EXACT_MATCH"
    assert matched.difference == Decimal(0)
    assert not matched.quarantine_required
    assert matched.left.methodology is XbrlMethodology.SEC_COMPANY_FACTS_XBRL
    assert matched.right.methodology is XbrlMethodology.SEC_FILING_EXHIBIT

    conflict = reconcile_values(
        xbrl,
        replace(exhibit, value=exhibit.value - Decimal(1)),
    )
    assert conflict.disposition is DecisionDisposition.QUARANTINED
    assert conflict.code == "RECONCILIATION_VALUE_MISMATCH"
    assert conflict.difference == Decimal(1)
    assert conflict.quarantine_required
    assert conflict.left.value == Decimal(298658000000)
    assert conflict.right.value == Decimal(298657999999)
    assert not hasattr(conflict, "preferred_value")


def test_reconciliation_semantic_mismatch_quarantines_without_arithmetic() -> None:
    candidate = map_facts((_filing_facts()[0],), _registry())[0].candidate
    assert candidate is not None
    left = ReconciliationValue.from_xbrl(candidate)
    right = replace(
        left,
        issuer_id="different",
        metric_id="different_metric",
        reporting_entity_id="different_entity",
        reporting_scope_id="different_scope",
        period_type=XbrlPeriodType.DURATION,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 29),
        unit="count",
        methodology=XbrlMethodology.SEC_FILING_EXHIBIT,
    )

    result = reconcile_values(left, right)

    assert result.disposition is DecisionDisposition.QUARANTINED
    assert result.code == "RECONCILIATION_SEMANTICS_MISMATCH"
    assert result.difference is None
    assert result.reasons == (
        "ISSUER_MISMATCH",
        "METRIC_MISMATCH",
        "REPORTING_ENTITY_MISMATCH",
        "REPORTING_SCOPE_MISMATCH",
        "PERIOD_TYPE_MISMATCH",
        "PERIOD_START_MISMATCH",
        "PERIOD_END_MISMATCH",
        "UNIT_MISMATCH",
    )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"not json", "valid UTF-8 JSON"),
        (b'{"cik": -1, "facts": {}}', "nonnegative integer CIK"),
        (b'{"cik": null, "facts": {}}', "string or integer CIK"),
        (b'{"cik": "not-a-cik", "facts": {}}', "not a valid CIK"),
        (b'{"cik": 92230, "facts": []}', "companyfacts.facts must be"),
        (
            b'{"cik": 92230, "facts": {"tfc": {"Fact": {"units": {"USD": {}}}}}}',
            "must be a sequence",
        ),
        (
            b"".join(
                (
                    b'{"cik": 92230, "facts": {"tfc": {"Fact": {"units": {"USD": ',
                    b'[{"end": "bad", "val": 1}]}}}}}',
                )
            ),
            "must be an ISO date",
        ),
        (
            (b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":[{"end":1,"val":1}]}}}}}'),
            "must be an ISO date",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":true}]}}}}}'
            ),
            "must be an exact JSON number",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":"NaN"}]}}}}}'
            ),
            "must be finite",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":"bad"}]}}}}}'
            ),
            "not an exact decimal",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":1,"accn":2}]}}}}}'
            ),
            "optional XBRL metadata must be text",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":1,"decimals":1.5}]}}}}}'
            ),
            "decimals must be integral",
        ),
        (
            (
                b'{"cik":92230,"facts":{"tfc":{"F":{"units":{"USD":'
                b'[{"end":"2026-06-30","val":1,"decimals":"bad"}]}}}}}'
            ),
            "decimals must be an integer or INF",
        ),
    ],
)
def test_company_facts_adapter_fails_closed(payload: bytes, error: str) -> None:
    with pytest.raises(XbrlDataError, match=error):
        SecCompanyFactsAdapter().parse(
            payload,
            issuer_id="tfc",
            evidence_id="evidence:synthetic",
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"<not-closed>", "well-formed XML"),
        (b"<!DOCTYPE x [<!ENTITY y 'z'>]><x/>", "DTD or entity"),
        (
            b"".join(
                (
                    b'<x xmlns:x="http://www.xbrl.org/2003/instance" ',
                    b'xmlns:t="https://synthetic.example.test"><t:F contextRef="missing" ',
                    b'unitRef="USD">1</t:F></x>',
                )
            ),
            "unknown context",
        ),
    ],
)
def test_filing_xbrl_adapter_fails_closed(payload: bytes, error: str) -> None:
    with pytest.raises(XbrlDataError, match=error):
        SecFilingXbrlAdapter().parse(
            payload,
            issuer_id="pfsi",
            evidence_id="evidence:synthetic",
            accession="synthetic-accession",
            form="10-Q",
            filed=date(2026, 7, 31),
        )


def test_filing_xbrl_context_unit_and_fact_errors_fail_closed() -> None:
    prefix = (
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:test="https://synthetic.example.test/taxonomy">'
    )
    suffix = "</xbrli:xbrl>"
    context = (
        '<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="sec">92230'
        "</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30"
        "</xbrli:instant></xbrli:period></xbrli:context>"
    )
    unit = '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    cases = (
        (context + '<test:F contextRef="I" unitRef="missing">1</test:F>', "unresolved unit"),
        (
            context + unit + '<test:F contextRef="I" unitRef="USD" scale="bad">1</test:F>',
            "scale must be an integer string",
        ),
        (
            context + unit + '<test:F contextRef="I" unitRef="USD">bad</test:F>',
            "not an exact decimal",
        ),
        (
            (
                "<xbrli:context><xbrli:entity><xbrli:identifier>92230</xbrli:identifier>"
                "</xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant>"
                "</xbrli:period></xbrli:context>"
            ),
            "missing its ID or entity identifier",
        ),
        (
            (
                '<xbrli:context id="D"><xbrli:entity><xbrli:identifier>92230</xbrli:identifier>'
                "</xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>"
                "</xbrli:period></xbrli:context>"
            ),
            "unresolved period",
        ),
        (
            (
                '<xbrli:context id="I"><xbrli:entity><xbrli:identifier>92230</xbrli:identifier>'
                '<xbrli:segment><xbrldi:explicitMember dimension="test:Axis" />'
                "</xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30"
                "</xbrli:instant></xbrli:period></xbrli:context>"
            ),
            "blank explicit member",
        ),
        (
            (
                '<xbrli:context id="I"><xbrli:entity><xbrli:identifier>92230</xbrli:identifier>'
                '<xbrli:segment><xbrldi:typedMember dimension="test:Axis"><test:Value>one'
                "</test:Value></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period>"
                "<xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>"
            ),
            "typed dimension is unsupported",
        ),
        (context + context, "repeats a context ID"),
        (
            (
                '<xbrli:unit id="ratio"><xbrli:divide><xbrli:unitNumerator>'
                "<xbrli:measure>test:a</xbrli:measure></xbrli:unitNumerator>"
                "</xbrli:divide></xbrli:unit>"
            ),
            "divide unit is incomplete",
        ),
        ("<xbrli:unit><xbrli:measure>test:a</xbrli:measure></xbrli:unit>", "missing its ID"),
        (
            (
                '<xbrli:unit id="u"><xbrli:measure>test:a</xbrli:measure></xbrli:unit>'
                '<xbrli:unit id="u"><xbrli:measure>test:a</xbrli:measure></xbrli:unit>'
            ),
            "repeats a unit ID",
        ),
        ('<xbrli:unit id="u"><xbrli:measure /></xbrli:unit>', "unit measure is blank"),
        (
            context
            + unit
            + '<ix:nonFraction name="invalid" contextRef="I" unitRef="USD">1</ix:nonFraction>',
            "invalid concept name",
        ),
        (
            context + unit + '<Plain contextRef="I" unitRef="USD">1</Plain>',
            "namespace has no prefix",
        ),
    )
    for body, error in cases:
        with pytest.raises(XbrlDataError, match=error):
            SecFilingXbrlAdapter().parse(
                f"{prefix}{body}{suffix}".encode(),
                issuer_id="tfc",
                evidence_id="evidence:synthetic-errors",
                accession="synthetic-accession",
                form="10-Q",
                filed=date(2026, 7, 31),
            )


def _exercise_invalid_fact_value(fact: XbrlFact, value: object) -> None:
    invalid_fact = replace(fact)
    object.__setattr__(invalid_fact, "value", value)
    invalid_fact.__post_init__()


def _exercise_invalid_reconciliation_value(
    value: ReconciliationValue,
    invalid_value: object,
) -> None:
    invalid_value_record = replace(value)
    object.__setattr__(invalid_value_record, "value", invalid_value)
    invalid_value_record.__post_init__()


def _exercise_invalid_reconciliation_difference(
    decision: ReconciliationDecision,
    difference: object,
) -> None:
    invalid_decision = replace(decision)
    object.__setattr__(invalid_decision, "difference", difference)
    invalid_decision.__post_init__()


def test_decimal_and_context_model_guards() -> None:
    fact = _company_facts()[0]
    with pytest.raises(TypeError, match="finite Decimal"):
        _exercise_invalid_fact_value(fact, 1.0)
    with pytest.raises(XbrlDataError, match="instant XBRL facts"):
        replace(fact, period_start=date(2026, 4, 1))
    with pytest.raises(XbrlDataError, match="dimension and member"):
        DimensionMember("", "member")
    with pytest.raises(XbrlDataError, match="eligible source"):
        XbrlConceptMapping(
            issuer_id="tfc",
            cik="0000092230",
            metric_id="metric",
            metric_version="1.0.0",
            taxonomy="tfc",
            concept="Concept",
            unit="USD",
            scale=Decimal(1),
            decimals=-6,
            period_type=XbrlPeriodType.INSTANT,
            dimensions=(),
            reporting_entity_id="entity",
            reporting_scope_id="scope",
            eligible_sources=(),
        )


def test_domain_models_reject_incomplete_or_inexact_semantics() -> None:
    fact = _company_facts()[0]
    mapping = _registry().mappings[0]
    valid_decision = apply_mapping(fact, _registry().mappings[1], mapping_version="1.0.0")
    assert valid_decision.candidate is not None
    quarantined = apply_mapping(fact, mapping, mapping_version="1.0.0")
    assert quarantined.disposition is DecisionDisposition.QUARANTINED

    duplicate_dimension = DimensionMember("axis", "member")
    with pytest.raises(XbrlDataError, match="scale must be positive"):
        replace(fact, scale=Decimal(0))
    with pytest.raises(XbrlDataError, match="semantics must not be blank"):
        replace(fact, issuer_id="")
    with pytest.raises(XbrlDataError, match="duration XBRL facts"):
        replace(fact, period_type=XbrlPeriodType.DURATION, period_start=None)
    with pytest.raises(XbrlDataError, match="cannot repeat"):
        replace(fact, dimensions=(duplicate_dimension, duplicate_dimension))
    with pytest.raises(XbrlDataError, match="decimals must be"):
        replace(fact, decimals="BAD")

    with pytest.raises(XbrlDataError, match="mapping scale must be positive"):
        replace(mapping, scale=Decimal(0))
    with pytest.raises(XbrlDataError, match="mapping semantics must not be blank"):
        replace(mapping, metric_id="")
    with pytest.raises(XbrlDataError, match="cannot repeat dimension"):
        replace(mapping, dimensions=(duplicate_dimension, duplicate_dimension))
    assert replace(mapping, decimals="INF").decimals == "INF"

    with pytest.raises(XbrlDataError, match="requires a version and mappings"):
        XbrlMappingRegistry(version="", mappings=(mapping,))
    with pytest.raises(XbrlDataError, match="requires a version and mappings"):
        XbrlMappingRegistry(version="1.0.0", mappings=())
    with pytest.raises(XbrlDataError, match="one clean candidate"):
        replace(valid_decision, candidate=None)
    with pytest.raises(XbrlDataError, match="reasons and no candidate"):
        MappingDecision(
            disposition=DecisionDisposition.QUARANTINED,
            fact=fact,
            mapping=mapping,
            candidate=valid_decision.candidate,
            reasons=(),
        )

    exact = ReconciliationValue.from_xbrl(valid_decision.candidate)
    with pytest.raises(TypeError, match="finite Decimal"):
        _exercise_invalid_reconciliation_value(exact, float("inf"))
    with pytest.raises(XbrlDataError, match="must not be blank"):
        replace(exact, evidence_id="")
    with pytest.raises(XbrlDataError, match="instant reconciliation"):
        replace(exact, period_start=date(2026, 4, 1))
    with pytest.raises(XbrlDataError, match="duration reconciliation"):
        replace(exact, period_type=XbrlPeriodType.DURATION, period_start=None)
    reconciliation = reconcile_values(exact, exact)
    with pytest.raises(XbrlDataError, match="stable code and reasons"):
        ReconciliationDecision(
            disposition=DecisionDisposition.VALIDATED,
            code="",
            reasons=(),
            left=exact,
            right=exact,
            difference=Decimal(0),
        )
    with pytest.raises(TypeError, match="finite Decimal"):
        _exercise_invalid_reconciliation_difference(reconciliation, 1.0)


def test_company_facts_duration_string_value_and_optional_metadata() -> None:
    payload = b"".join(
        (
            b'{"cik":"0000092230","facts":{"tfc":{"DurationFact":{"units":{"USD":[',
            b'{"start":"2026-04-01","end":"2026-06-30","val":"(1,234.5)",',
            b'"decimals":"INF"}]}}}}}',
        )
    )

    fact = SecCompanyFactsAdapter().parse(
        payload,
        issuer_id="tfc",
        evidence_id="evidence:synthetic-duration",
    )[0]

    assert fact.value == Decimal("-1234.5")
    assert fact.period_type is XbrlPeriodType.DURATION
    assert fact.period_start == date(2026, 4, 1)
    assert fact.accession is None
    assert fact.form is None
    assert fact.filed is None
    assert fact.decimals == "INF"


def test_standard_filing_instance_supports_divide_units_and_sign() -> None:
    payload = b"".join(
        (
            b'<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" ',
            b'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" ',
            b'xmlns:test="https://synthetic.example.test/taxonomy">',
            b'<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="sec">92230',
            b"</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30",
            b"</xbrli:instant></xbrli:period></xbrli:context>",
            b'<xbrli:unit id="USDPerShare"><xbrli:divide><xbrli:unitNumerator>',
            b"<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>",
            b"<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>",
            b"</xbrli:unitDenominator></xbrli:divide></xbrli:unit>",
            b'<xbrli:unit id="Loans"><xbrli:measure>test:loans</xbrli:measure></xbrli:unit>',
            b'<test:Amount contextRef="I" unitRef="USDPerShare" decimals="INF" sign="-">',
            b'1.25</test:Amount><test:Count contextRef="I" unitRef="Loans">10</test:Count>',
            b"</xbrli:xbrl>",
        )
    )

    facts = SecFilingXbrlAdapter().parse(
        payload,
        issuer_id="tfc",
        evidence_id="evidence:synthetic-instance",
        accession="synthetic-accession",
        form="10-Q",
        filed=date(2026, 7, 31),
    )

    assert [(fact.qualified_concept, fact.unit, fact.value) for fact in facts] == [
        ("test:Amount", "USD/shares", Decimal("-1.25")),
        ("test:Count", "test:loans", Decimal(10)),
    ]
    assert facts[0].decimals == "INF"
    assert facts[1].decimals is None
    assert all(fact.scale == Decimal(1) for fact in facts)


def test_registry_rejects_inexact_scale_and_duplicate_mapping(tmp_path: Path) -> None:
    inexact = tmp_path / "inexact.yaml"
    inexact.write_text(
        "mapping_version: '1.0.0'\nmappings:\n"
        "  - {issuer_id: tfc, cik: '0000092230', metric_id: metric, "
        "metric_version: '1.0.0', taxonomy: tfc, concept: Fact, unit: USD, "
        "scale: 1.0, decimals: -6, period_type: instant, dimensions: [], "
        "reporting_entity_id: entity, reporting_scope_id: scope, "
        "eligible_sources: [SEC_COMPANY_FACTS]}\n",
        encoding="utf-8",
    )
    with pytest.raises(XbrlDataError, match="scale must be a nonblank string"):
        XbrlMappingRegistry.from_yaml(inexact)

    registry = _registry()
    with pytest.raises(XbrlDataError, match="duplicate XBRL concept mapping"):
        XbrlMappingRegistry(
            version="duplicate",
            mappings=(registry.mappings[0], registry.mappings[0]),
        )


def test_registry_fails_closed_on_missing_and_invalid_mapping_fields(tmp_path: Path) -> None:
    with pytest.raises(XbrlDataError, match="unavailable or invalid"):
        XbrlMappingRegistry.from_yaml(tmp_path / "missing.yaml")

    original = _CONFIG.read_text(encoding="utf-8")
    invalid_decimals = tmp_path / "invalid-decimals.yaml"
    invalid_decimals.write_text(
        original.replace("decimals: -6", "decimals: invalid", 1),
        encoding="utf-8",
    )
    with pytest.raises(XbrlDataError, match="decimals must be"):
        XbrlMappingRegistry.from_yaml(invalid_decimals)

    invalid_enum = tmp_path / "invalid-enum.yaml"
    invalid_enum.write_text(
        original.replace("period_type: instant", "period_type: forever", 1),
        encoding="utf-8",
    )
    with pytest.raises(XbrlDataError, match="unsupported XBRL enum"):
        XbrlMappingRegistry.from_yaml(invalid_enum)
