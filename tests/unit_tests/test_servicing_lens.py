from __future__ import annotations

import json
import shutil
import struct
import subprocess
import zlib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

from fastapi import Request
from fastapi.routing import APIRoute

from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.database import create_database_engine
from mortgage_servicing_dashboard.presentation import (
    CompanyIdentity,
    EarningsIdentity,
    ScaleAssessment,
    normalize_companies,
    normalize_earnings,
    serialize_cards,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    ObservationRecord,
    seed_stage_a,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunks(payload: bytes) -> list[tuple[bytes, bytes]]:
    assert payload.startswith(_PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(_PNG_SIGNATURE)
    while offset < len(payload):
        assert offset + 12 <= len(payload)
        length = int(struct.unpack(">I", payload[offset : offset + 4])[0])
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        assert chunk_end <= len(payload)
        chunk_payload = payload[offset + 8 : offset + 8 + length]
        expected_crc = int(struct.unpack(">I", payload[offset + 8 + length : chunk_end])[0])
        assert zlib.crc32(chunk_type + chunk_payload) & 0xFFFFFFFF == expected_crc
        chunks.append((chunk_type, chunk_payload))
        offset = chunk_end
    assert offset == len(payload)
    return chunks


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png_rgb(payload: bytes) -> tuple[int, int, bytes, list[tuple[bytes, bytes]]]:
    chunks = _png_chunks(payload)
    chunk_types = [chunk_type for chunk_type, _ in chunks]
    assert chunk_types[0] == b"IHDR"
    assert chunk_types[-1] == b"IEND"
    assert chunk_types.count(b"IHDR") == chunk_types.count(b"IEND") == 1
    header = struct.unpack(">IIBBBBB", chunks[0][1])
    width, height = int(header[0]), int(header[1])
    assert header[2:] == (8, 2, 0, 0, 0)  # RGB8, standard compression/filter, no interlace.

    compressed = b"".join(chunk for chunk_type, chunk in chunks if chunk_type == b"IDAT")
    filtered = zlib.decompress(compressed)
    bytes_per_pixel = 3
    stride = width * bytes_per_pixel
    assert len(filtered) == height * (stride + 1)
    previous = bytearray(stride)
    decoded = bytearray()
    offset = 0
    for _ in range(height):
        filter_type = filtered[offset]
        assert filter_type in range(5)
        encoded = filtered[offset + 1 : offset + 1 + stride]
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                predictor = _paeth_predictor(left, above, upper_left)
            row[index] = (value + predictor) & 0xFF
        decoded.extend(row)
        previous = row
        offset += stride + 1
    return width, height, bytes(decoded), chunks


def _repository(tmp_path: Path) -> IntelligenceRepository:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'servicing-lens.db').as_posix()}")
    seed_stage_a(engine)
    return IntelligenceRepository(engine)


def _rendered_route(
    repository: IntelligenceRepository,
    path: str,
    *arguments: object,
) -> str:
    app = create_app(repository=repository)
    route = next(item for item in app.routes if isinstance(item, APIRoute) and item.path == path)
    request = Request(
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
    response = route.endpoint(*arguments, request, repository)
    return bytes(response.body).decode()


def test_live_normalization_uses_exact_derived_values_and_honest_gaps(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    companies = normalize_companies(
        cast("list[CompanyIdentity]", repository.companies()),
        repository.observations(include_missing=True),
    )
    by_id = {company.id: company for company in companies}

    assert by_id["pfsi"].upb.display == "$731B"
    assert by_id["pfsi"].growth.value == (Decimal(11) / Decimal(720) * Decimal(100))
    assert by_id["pfsi"].owned_mix.value == (Decimal(488) / Decimal(731) * Decimal(100))
    assert by_id["pfsi"].owned_mix.label == "Owned MSR mix"
    assert len(by_id["pfsi"].upb.inputs) == 1
    assert len(by_id["pfsi"].growth.inputs) == 2
    assert len(by_id["pfsi"].owned_mix.inputs) == 2
    assert all(item.observation_id and item.evidence_id for item in by_id["pfsi"].growth.inputs)
    assert all(
        item.locator_url and "#cited-source-locator" in item.locator_url
        for item in by_id["pfsi"].owned_mix.inputs
    )
    assert by_id["tfc"].owned_mix.label == "Bank-owned share"
    assert "not an owned-MSR measure" in by_id["tfc"].owned_mix.note
    assert all(company.customer_loans.value is None for company in companies)
    assert all(company.platform is None for company in companies)


def test_four_company_presentation_fixture_has_no_client_selection_state(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    real_companies = repository.companies()
    real_rows = repository.observations(include_missing=True)
    template_company = real_companies[0]
    template_rows = [row for row in real_rows if row.company_id == template_company["id"]]
    synthetic_companies: list[CompanyIdentity] = []
    synthetic_rows: list[ObservationRecord] = []

    for index in range(4):
        company_id = f"synthetic-{index + 1}"
        ticker = f"S{index + 1}"
        synthetic_companies.append(
            {
                "id": company_id,
                "legal_name": f"Synthetic Servicer {index + 1}",
                "ticker": ticker,
                "classification": "synthetic presentation fixture",
            }
        )
        synthetic_rows.extend(
            replace(
                row,
                id=f"{row.id}:{company_id}",
                company_id=company_id,
                company_name=f"Synthetic Servicer {index + 1}",
                ticker=ticker,
                value=(str(Decimal(row.value) + index) if row.value is not None else None),
            )
            for row in template_rows
        )

    cards = serialize_cards(
        normalize_companies(synthetic_companies, synthetic_rows),
        scale_assessment=ScaleAssessment("insufficient_information", ("Synthetic fixture",)),
    )
    assert len(cards) == 4
    assert len({card["id"] for card in cards}) == 4
    package = Path(__file__).parents[2] / "src" / "mortgage_servicing_dashboard"
    state_source = (package / "static" / "servicing_lens_state.js").read_text(encoding="utf-8")
    dashboard_source = (package / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "toggleCompany" not in state_source
    assert "sessionStorage" not in dashboard_source


def test_growth_and_mix_fail_closed_for_period_and_semantic_mismatches(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    identities = cast("list[CompanyIdentity]", repository.companies())
    pfsi_identity = [item for item in identities if item["id"] == "pfsi"]
    pfsi_rows = [
        row for row in repository.observations(include_missing=True) if row.company_id == "pfsi"
    ]

    no_q1_rows = [row for row in pfsi_rows if row.period_end != "2026-03-31"]
    non_adjacent = normalize_companies(
        pfsi_identity,
        no_q1_rows,
        target_periods={"pfsi": "2026-06-30"},
    )[0]
    assert non_adjacent.growth.value is None
    assert "not an adjacent quarter" in non_adjacent.growth.note

    mismatched_scope_rows = [
        replace(row, reporting_scope_id="changed_scope")
        if row.metric_id == "total_servicing_upb" and row.period_end == "2026-03-31"
        else row
        for row in pfsi_rows
    ]
    mismatched_growth = normalize_companies(
        pfsi_identity,
        mismatched_scope_rows,
        target_periods={"pfsi": "2026-06-30"},
    )[0]
    assert mismatched_growth.growth.value is None
    assert "matching semantics" in mismatched_growth.growth.note

    missing_q2_numerator = [
        row
        for row in pfsi_rows
        if not (row.metric_id == "owned_msr_upb" and row.period_end == "2026-06-30")
    ]
    stale_mix = normalize_companies(
        pfsi_identity,
        missing_q2_numerator,
        target_periods={"pfsi": "2026-06-30"},
    )[0]
    assert stale_mix.owned_mix.value is None
    assert "2026-06-30" in stale_mix.owned_mix.note

    mismatched_mix_rows = [
        replace(row, reporting_scope_id="changed_scope")
        if row.metric_id == "owned_msr_upb" and row.period_end == "2026-06-30"
        else row
        for row in pfsi_rows
    ]
    mismatched_mix = normalize_companies(
        pfsi_identity,
        mismatched_mix_rows,
        target_periods={"pfsi": "2026-06-30"},
    )[0]
    assert mismatched_mix.owned_mix.value is None
    assert "compatible period, entity, and scope" in mismatched_mix.owned_mix.note


def test_scale_fails_closed_on_authoritative_not_comparable_result(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identities = cast("list[CompanyIdentity]", repository.companies())
    cards = normalize_companies(identities, repository.observations(include_missing=True))
    comparison = repository.compare(metric_id="total_servicing_upb", period_end=date(2026, 6, 30))
    assert comparison is not None
    payload = serialize_cards(
        cards,
        scale_assessment=ScaleAssessment(comparison.status, comparison.reasons),
    )
    assert comparison.status == "not_comparable"
    assert all(card["relative_scale"] is None for card in payload)

    heterogeneous = [
        replace(
            cards[1],
            upb=replace(
                cards[1].upb,
                label="Owned MSR UPB",
                source_metric_id="owned_msr_upb",
            ),
        ),
        cards[0],
    ]
    guarded = serialize_cards(
        heterogeneous,
        scale_assessment=ScaleAssessment("comparable", ()),
    )
    assert [item["id"] for item in guarded] == [card.id for card in heterogeneous]
    assert all(item["relative_scale"] is None for item in guarded)
    assert {item["scale_status"] for item in guarded} == {"insufficient_information"}
    assert all(card["scale_reasons"] == comparison.reasons for card in payload)


def test_earnings_is_bound_to_event_period_and_exact_option_mapping(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    identities = cast("list[CompanyIdentity]", repository.companies())
    observations = repository.observations(include_missing=True)
    events = cast("list[EarningsIdentity]", repository.earnings_events())
    later_event: EarningsIdentity = {
        **next(item for item in events if item["company_id"] == "pfsi"),
        "id": "earnings:pfsi:2026q3",
        "fiscal_quarter": 3,
        "event_at": "2026-10-30T12:00:00+00:00",
        "source_url": "https://example.test/pfsi-q3-official",
    }
    briefs = normalize_earnings(identities, observations, [*events, later_event])
    pfsi = next(item for item in briefs if item.company_id == "pfsi")
    assert pfsi.reporting_period == "Q3 2026"
    assert "No governed servicing observations" in pfsi.summary
    assert "$731B" not in pfsi.summary
    assert all(signal.value is None for signal in pfsi.signals)
    assert pfsi.source_url == later_event["source_url"]

    state_module = (
        Path(__file__).parents[2]
        / "src"
        / "mortgage_servicing_dashboard"
        / "static"
        / "servicing_lens_state.js"
    )
    node = shutil.which("node")
    assert node is not None
    script = (
        f"const state = require({json.dumps(str(state_module))});\n"
        """
const result = state.resolveEarningsCompany(
  "PFSI · PennyMac Financial Services, Inc.",
  [{ value: "PFSI · PennyMac Financial Services, Inc.", companyId: "pfsi" }],
  [{ companyId: "tfc", searchText: "tfc truist financial corporation" }]
);
process.stdout.write(result);
"""
    )
    completed = subprocess.run(  # noqa: S603
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "pfsi"


def test_servicing_lens_template_has_search_kpis_earnings_and_social_contract() -> None:
    package = Path(__file__).parents[2] / "src" / "mortgage_servicing_dashboard"
    template = (package / "templates" / "dashboard.html").read_text(encoding="utf-8")
    javascript = (package / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert 'id="company-search"' in template
    assert 'id="company-sort"' not in template
    assert "data-sort-upb" not in template
    assert "companyRows.sort" not in javascript
    assert "stable repository order" in template
    assert template.count('class="kpi-selector"') == 1  # One loop, rendered three times.
    assert "range(3)" in template
    assert 'class="compare-checkbox"' not in template
    assert 'class="remove-company"' not in template
    assert 'method="get" action="/comparison"' in template
    assert 'name="company_id"' in template
    assert 'name="third_company_id"' in template
    assert "{{ company.upb.label }} · Scope {{ company.upb.reporting_scope" in template
    assert "Total servicing UPB · {{ company.period_label }}" not in template
    assert "sessionStorage" not in javascript
    assert "toggleCompany" not in javascript
    assert "Earnings brief" in template
    assert 'id="earnings-search"' in template
    assert 'data-company-id="{{ brief.company_id }}"' in template
    assert "Open official earnings source" in template
    assert 'property="og:image"' in template
    assert "BigInt(" not in javascript
    assert "parseFloat" not in javascript
    assert "Number(" not in javascript


def test_social_image_is_canonical_valid_and_visually_nonempty() -> None:
    image_path = (
        Path(__file__).parents[2] / "src" / "mortgage_servicing_dashboard" / "static" / "og.png"
    )
    width, height, pixels, chunks = _decode_png_rgb(image_path.read_bytes())

    assert (width, height) == (1200, 630)
    chunk_types = [chunk_type for chunk_type, _ in chunks]
    assert set(chunk_types) <= {b"IHDR", b"sRGB", b"gAMA", b"pHYs", b"IDAT", b"IEND"}
    metadata = {chunk_type: payload for chunk_type, payload in chunks if chunk_type != b"IDAT"}
    assert metadata[b"sRGB"] == b"\x00"
    assert struct.unpack(">I", metadata[b"gAMA"]) == (45455,)
    pixels_per_unit_x, pixels_per_unit_y, unit = struct.unpack(">IIB", metadata[b"pHYs"])
    assert pixels_per_unit_x == pixels_per_unit_y
    assert 3700 <= pixels_per_unit_x <= 3800
    assert unit == 1

    rgb_pixels = [pixels[index : index + 3] for index in range(0, len(pixels), 3)]
    dark_pixels = sum(max(pixel) < 80 for pixel in rgb_pixels)
    chromatic_pixels = sum(max(pixel) - min(pixel) > 25 for pixel in rgb_pixels)
    light_pixels = sum(min(pixel) > 225 for pixel in rgb_pixels)
    sampled_colors = set(rgb_pixels[::97])
    assert dark_pixels > 5_000
    assert chromatic_pixels > 25_000
    assert light_pixels > 300_000
    assert len(sampled_colors) > 1_000


def test_rendered_routes_preserve_specific_governed_content_and_table_semantics(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    overview = _rendered_route(repository, "/")
    company = _rendered_route(repository, "/companies/{company_id}", "tfc")
    metric = _rendered_route(
        repository,
        "/metrics/{metric_id}",
        "total_servicing_upb",
    )
    comparison = _rendered_route(repository, "/comparison")
    earnings = _rendered_route(repository, "/earnings")

    assert overview.count('role="rowgroup"') >= 2
    assert overview.count('role="columnheader"') == 5
    assert overview.count('role="cell"') == 10
    assert "2 evidence inputs" in overview
    assert "observation%3Apfsi" in overview
    assert "TFC disclosure profile" in company
    assert "Reported disclosure highlights" in company
    assert "Bank-owned loans serviced / total UPB" in company
    assert "Governed semantic contract" in metric
    assert "Issuer-defined period-end total servicing portfolio UPB" in metric
    assert "Governed verdicts and reasons" in comparison
    assert "Relative portfolio scale unavailable" in comparison
    assert "not comparable" in comparison
    assert "no cross-company ranking is drawn" in comparison
    assert 'value="PFSI · PennyMac Financial Services, Inc." data-company-id="pfsi"' in earnings
    assert "Observation <code>observation:" in earnings
