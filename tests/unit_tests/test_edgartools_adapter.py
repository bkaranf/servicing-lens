from __future__ import annotations

import dataclasses
import hashlib
import importlib
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from mortgage_servicing_dashboard.edgartools_adapter.adapter import EdgarToolsAdapter
from mortgage_servicing_dashboard.edgartools_adapter.backend import (
    EdgarToolsBackend,
    FilingDateFilter,
    PublicEdgarToolsBackend,
    validate_accession,
    validate_cik,
)
from mortgage_servicing_dashboard.edgartools_adapter.bootstrap import (
    EdgarBootstrap,
    EdgarBootstrapConfig,
)
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    AcquiredContent,
    Attachment,
    AttachmentAcquisition,
    CalculationArc,
    Company,
    CompanyFactCandidate,
    CompanyFactsDiscovery,
    ContentRepresentation,
    DefinitionArc,
    Filing,
    FilingStructure,
    PresentationArc,
    RawMetadata,
    RetainedContent,
    ViewerIssueClassification,
    ViewerReport,
    ViewerValidationIssue,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlFiling,
    XbrlFootnote,
    XbrlUnit,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterConfigurationError,
    AdapterIdentityError,
    AdapterIntegrityError,
    AdapterLibraryError,
    AdapterNotFoundError,
    AdapterParsingError,
    AdapterRateLimitError,
    AdapterSelectionError,
    AdapterState,
    AdapterTransportError,
    AdapterValidationError,
    EdgarToolsAdapterError,
    map_edgar_exception,
)

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_ACCEPTANCE_TIMESTAMP = datetime(2026, 8, 6, 16, 45, 32, tzinfo=UTC)
_IDENTITY = "Servicing Lens tests contact@example.test"
_CIK = "0000092230"
_ACCESSION = "0000092230-26-000100"
_BOOTSTRAP_ENVIRONMENT = (
    "EDGAR_IDENTITY",
    "EDGAR_ACCESS_MODE",
    "EDGAR_LOCAL_DATA_DIR",
    "EDGAR_USE_LOCAL_DATA",
    "EDGAR_ALLOW_NETWORK_FALLBACK",
    "EDGARTOOLS_STRICT_ERRORS",
    "EDGAR_RATE_LIMIT_PER_SEC",
    "EDGAR_BASE_URL",
    "EDGAR_DATA_URL",
    "EDGAR_XBRL_URL",
)


class EdgarError(Exception):
    pass


class NotFoundError(EdgarError):
    pass


class AttachmentNotFoundError(NotFoundError):
    pass


class CompanyNotFoundError(NotFoundError):
    pass


class FilingNotFoundError(NotFoundError):
    pass


class TooManyRequestsError(EdgarError):
    pass


class TransportError(EdgarError):
    pass


class ParsingError(EdgarError):
    pass


class XBRLProcessingError(ParsingError):
    pass


class ValidationError(EdgarError):
    pass


class InvalidDateError(ValidationError):
    pass


class IdentityError(EdgarError):
    pass


class IdentityNotSetError(IdentityError):
    pass


class SECIdentityError(IdentityError):
    pass


def _clear_bootstrap_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "edgar", raising=False)
    for variable in _BOOTSTRAP_ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)


def _bootstrap(tmp_path: Path, *, identity: str = _IDENTITY) -> EdgarBootstrap:
    return EdgarBootstrap(
        EdgarBootstrapConfig(
            identity=SecretStr(identity),
            repository_root=tmp_path,
        )
    )


def _acquired_content(
    payload: bytes,
    *,
    source_url: str = "https://www.sec.gov/Archives/example/tfc-20260630.htm",
) -> AcquiredContent:
    return AcquiredContent(
        cik=_CIK,
        accession_number=_ACCESSION,
        document="tfc-20260630.htm",
        source_url=source_url,
        content=payload,
        media_type="text/html; charset=utf-8",
        representation=ContentRepresentation.LIBRARY_TEXT_UTF8,
        capture_method="edgartools_attachment_text_utf8",
        sha256=hashlib.sha256(payload).hexdigest(),
        retrieved_at=_NOW,
    )


def test_bootstrap_configures_crawl_and_local_storage_before_one_lazy_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    imported = ModuleType("edgar")
    calls: list[str] = []

    def import_after_configuration(name: str) -> ModuleType:
        calls.append(name)
        assert name == "edgar"
        assert os.environ["EDGAR_ACCESS_MODE"] == "CRAWL"
        assert "EDGAR_RATE_LIMIT_PER_SEC" not in os.environ
        assert os.environ["EDGAR_IDENTITY"] == _IDENTITY
        assert os.environ["EDGAR_USE_LOCAL_DATA"] == "1"
        assert os.environ["EDGAR_ALLOW_NETWORK_FALLBACK"] == "1"
        assert os.environ["EDGARTOOLS_STRICT_ERRORS"] == "1"
        expected_data = (tmp_path / ".msi" / "edgartools" / "data").resolve()
        assert Path(os.environ["EDGAR_LOCAL_DATA_DIR"]) == expected_data
        assert expected_data.is_dir()
        return imported

    monkeypatch.setattr(importlib, "import_module", import_after_configuration)
    bootstrap = _bootstrap(tmp_path)

    assert bootstrap.load() is imported
    assert bootstrap.load() is imported
    assert calls == ["edgar"]


@pytest.mark.parametrize("rate", ["1", "4", "9"])
def test_bootstrap_accepts_only_conservative_integer_rate_overrides(
    rate: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    monkeypatch.setenv("EDGAR_RATE_LIMIT_PER_SEC", rate)
    monkeypatch.setattr(importlib, "import_module", lambda _: ModuleType("edgar"))

    _bootstrap(tmp_path).load()

    assert os.environ["EDGAR_RATE_LIMIT_PER_SEC"] == rate


@pytest.mark.parametrize(
    "rate",
    [
        "",
        "invalid",
        "NaN",
        "Infinity",
        "0",
        "-1",
        "+1",
        "01",
        "4.5",
        "9.01",
        "10",
        "\N{ARABIC-INDIC DIGIT ONE}",
    ],
)
def test_bootstrap_rejects_noncanonical_or_above_default_rate_before_import(
    rate: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    monkeypatch.setenv("EDGAR_RATE_LIMIT_PER_SEC", rate)
    imported = False

    def unexpected_import(_: str) -> ModuleType:
        nonlocal imported
        imported = True
        return ModuleType("edgar")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    with pytest.raises(AdapterConfigurationError) as captured:
        _bootstrap(tmp_path).load()

    assert captured.value.state is AdapterState.CONFIGURATION_ERROR
    assert captured.value.operation == "bootstrap"
    assert imported is False


@pytest.mark.parametrize(
    "identity",
    ["", "   ", "contact@example.test", "Servicing Lens", "Servicing Lens contact@"],
)
def test_bootstrap_rejects_missing_or_malformed_identity_before_import(
    identity: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    imported = False

    def unexpected_import(_: str) -> ModuleType:
        nonlocal imported
        imported = True
        return ModuleType("edgar")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    with pytest.raises(AdapterIdentityError) as captured:
        _bootstrap(tmp_path, identity=identity).load()

    assert captured.value.state is AdapterState.IDENTITY_NOT_CONFIGURED
    assert captured.value.operation == "bootstrap"
    assert imported is False


@pytest.mark.parametrize(
    "identity",
    [
        "Servicing Lens\rcontact@example.test",
        "Servicing Lens\ncontact@example.test",
        "Servicing Lens\x00 contact@example.test",
    ],
)
def test_bootstrap_rejects_identity_control_characters_as_configuration_errors(
    identity: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    imported = False

    def unexpected_import(_: str) -> ModuleType:
        nonlocal imported
        imported = True
        return ModuleType("edgar")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    with pytest.raises(AdapterIdentityError) as captured:
        _bootstrap(tmp_path, identity=identity).load()

    assert captured.value.state is AdapterState.CONFIGURATION_ERROR
    assert captured.value.operation == "bootstrap"
    assert imported is False


@pytest.mark.parametrize("variable", ["EDGAR_BASE_URL", "EDGAR_DATA_URL", "EDGAR_XBRL_URL"])
def test_bootstrap_prohibits_every_custom_mirror_even_when_empty(
    variable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    monkeypatch.setenv(variable, "")
    imported = False

    def unexpected_import(_: str) -> ModuleType:
        nonlocal imported
        imported = True
        return ModuleType("edgar")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    with pytest.raises(AdapterConfigurationError) as captured:
        _bootstrap(tmp_path).load()

    assert captured.value.state is AdapterState.CONFIGURATION_ERROR
    assert captured.value.operation == "bootstrap"
    assert imported is False


def test_bootstrap_rejects_prior_edgar_import_before_environment_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    monkeypatch.setitem(sys.modules, "edgar", ModuleType("edgar"))
    imported = False

    def unexpected_import(_: str) -> ModuleType:
        nonlocal imported
        imported = True
        return ModuleType("edgar")

    monkeypatch.setattr(importlib, "import_module", unexpected_import)

    with pytest.raises(AdapterConfigurationError) as captured:
        _bootstrap(tmp_path).load()

    assert captured.value.state is AdapterState.CONFIGURATION_ERROR
    assert captured.value.operation == "bootstrap"
    assert imported is False
    assert "EDGAR_IDENTITY" not in os.environ
    assert not (tmp_path / ".msi").exists()


def test_bootstrap_configuration_repr_does_not_expose_identity(tmp_path: Path) -> None:
    config = EdgarBootstrapConfig(identity=SecretStr(_IDENTITY), repository_root=tmp_path)

    assert _IDENTITY not in repr(config)


@pytest.mark.parametrize(
    ("library_error", "expected_type", "expected_state"),
    [
        (
            TooManyRequestsError("secret rate detail"),
            AdapterRateLimitError,
            AdapterState.RATE_LIMITED,
        ),
        (
            IdentityError("secret identity detail"),
            AdapterIdentityError,
            AdapterState.IDENTITY_NOT_CONFIGURED,
        ),
        (
            IdentityNotSetError("secret identity detail"),
            AdapterIdentityError,
            AdapterState.IDENTITY_NOT_CONFIGURED,
        ),
        (
            SECIdentityError("secret identity detail"),
            AdapterIdentityError,
            AdapterState.IDENTITY_NOT_CONFIGURED,
        ),
        (
            ValidationError("secret selector detail"),
            AdapterValidationError,
            AdapterState.INVALID_REQUEST,
        ),
        (
            InvalidDateError("secret date detail"),
            AdapterValidationError,
            AdapterState.INVALID_REQUEST,
        ),
        (NotFoundError("secret missing detail"), AdapterNotFoundError, AdapterState.NOT_FOUND),
        (
            AttachmentNotFoundError("secret missing detail"),
            AdapterNotFoundError,
            AdapterState.NOT_FOUND,
        ),
        (
            CompanyNotFoundError("secret missing detail"),
            AdapterNotFoundError,
            AdapterState.NOT_FOUND,
        ),
        (
            FilingNotFoundError("secret missing detail"),
            AdapterNotFoundError,
            AdapterState.NOT_FOUND,
        ),
        (
            TransportError("secret transport detail"),
            AdapterTransportError,
            AdapterState.TRANSPORT_ERROR,
        ),
        (ParsingError("secret parser detail"), AdapterParsingError, AdapterState.PARSING_ERROR),
        (
            XBRLProcessingError("secret XBRL detail"),
            AdapterParsingError,
            AdapterState.PARSING_ERROR,
        ),
        (EdgarError("secret library detail"), AdapterLibraryError, AdapterState.LIBRARY_ERROR),
    ],
)
def test_edgar_failures_map_exactly_without_leaking_library_messages(
    library_error: EdgarError,
    expected_type: type[EdgarToolsAdapterError],
    expected_state: AdapterState,
) -> None:
    mapped = map_edgar_exception(library_error, operation="get_filing")

    assert isinstance(mapped, expected_type)
    assert mapped.state is expected_state
    assert mapped.operation == "get_filing"
    assert "secret" not in str(mapped)


def test_error_mapper_rejects_foreign_exceptions() -> None:
    with pytest.raises(TypeError, match="edgartools domain exceptions"):
        map_edgar_exception(RuntimeError("foreign"), operation="get_filing")


@dataclasses.dataclass(frozen=True, slots=True)
class _LibraryFiling:
    cik: object
    accession_number: object
    company: object
    form: object
    filing_date: object
    acceptance_datetime: object
    report_date: object
    primary_document: object
    is_xbrl: object
    is_inline_xbrl: object
    size: object
    homepage_url: object
    text_url: object


def _library_filing() -> _LibraryFiling:
    return _LibraryFiling(
        cik=92230,
        accession_number=_ACCESSION,
        company="Truist Financial Corporation",
        form="10-Q/A",
        filing_date="2026-08-06",
        acceptance_datetime=_ACCEPTANCE_TIMESTAMP,
        report_date="2026-06-30",
        primary_document="tfc-20260630.htm",
        is_xbrl=True,
        is_inline_xbrl=True,
        size=12_345_678,
        homepage_url="https://www.sec.gov/Archives/example-index.html",
        text_url="https://www.sec.gov/Archives/example.txt",
    )


def _expected_filing() -> Filing:
    return Filing(
        cik=_CIK,
        accession_number=_ACCESSION,
        company_name="Truist Financial Corporation",
        form="10-Q/A",
        filing_date=date(2026, 8, 6),
        acceptance_timestamp=_ACCEPTANCE_TIMESTAMP,
        report_period=date(2026, 6, 30),
        primary_document="tfc-20260630.htm",
        amendment=True,
        is_xbrl=True,
        is_inline_xbrl=True,
        size=12_345_678,
        homepage_url="https://www.sec.gov/Archives/example-index.html",
        text_url="https://www.sec.gov/Archives/example.txt",
    )


class _FakeFilings:
    def __init__(self, module: _FakeEdgarModule, result: object | None) -> None:
        self._module = module
        self._result = result

    def __bool__(self) -> bool:
        return self._result is not None

    def __len__(self) -> int:
        return int(self._result is not None)

    def __getitem__(self, index: int) -> object:
        self._module.index_calls.append(index)
        if index != 0 or self._result is None:
            raise IndexError(index)
        return self._result

    def get(self, accession_number: str) -> object | None:
        self._module.filing_get_calls.append(accession_number)
        return self._result


class _FakeCompany:
    def __init__(self, module: _FakeEdgarModule) -> None:
        self._module = module

    def get_filings(self, **kwargs: object) -> _FakeFilings:
        self._module.filing_query_calls.append(kwargs)
        if isinstance(self._module.outcome, BaseException):
            raise self._module.outcome
        return _FakeFilings(self._module, self._module.outcome)


class _FakeEdgarModule(ModuleType):
    def __init__(self, outcome: object | BaseException | None) -> None:
        super().__init__("edgar")
        self.outcome = outcome
        self.company_calls: list[object] = []
        self.filing_query_calls: list[dict[str, object]] = []
        self.filing_get_calls: list[str] = []
        self.index_calls: list[int] = []
        self.global_lookup_calls: list[str] = []
        self.Company = self._company
        self.EdgarError = EdgarError
        self.NotFoundError = NotFoundError
        self.TooManyRequestsError = TooManyRequestsError
        self.TransportError = TransportError
        self.ParsingError = ParsingError
        self.ValidationError = ValidationError

    def _company(self, cik: object) -> _FakeCompany:
        self.company_calls.append(cik)
        return _FakeCompany(self)

    def get_by_accession_number(self, accession_number: str) -> object | None:
        self.global_lookup_calls.append(accession_number)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _FakeBootstrap:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.load_calls = 0

    def load(self) -> ModuleType:
        self.load_calls += 1
        return self.module


def _public_backend(module: ModuleType) -> tuple[PublicEdgarToolsBackend, _FakeBootstrap]:
    bootstrap = _FakeBootstrap(module)
    backend = PublicEdgarToolsBackend(cast("EdgarBootstrap", bootstrap))
    return backend, bootstrap


def test_get_filing_uses_one_exact_company_query_and_preserves_rich_metadata() -> None:
    module = _FakeEdgarModule(_library_filing())
    backend, bootstrap = _public_backend(module)

    result = backend.get_filing(_ACCESSION, expected_cik="92230")

    assert result == _expected_filing()
    assert result.is_amendment is True
    assert result.period_of_report == date(2026, 6, 30)
    assert bootstrap.load_calls == 1
    assert module.company_calls == [_CIK]
    assert module.filing_query_calls == [
        {"accession_number": _ACCESSION, "trigger_full_load": False}
    ]
    assert module.filing_get_calls == [_ACCESSION]
    assert module.index_calls == []
    assert module.global_lookup_calls == []


def test_get_filing_without_expected_cik_uses_one_global_exact_lookup() -> None:
    module = _FakeEdgarModule(_library_filing())
    backend, bootstrap = _public_backend(module)

    result = backend.get_filing(_ACCESSION)

    assert result == _expected_filing()
    assert bootstrap.load_calls == 1
    assert module.company_calls == []
    assert module.filing_query_calls == []
    assert module.filing_get_calls == []
    assert module.global_lookup_calls == [_ACCESSION]


def test_exact_company_filing_absence_is_confirmed_by_one_full_load_without_fallback() -> None:
    module = _FakeEdgarModule(None)
    backend, bootstrap = _public_backend(module)

    with pytest.raises(AdapterNotFoundError) as captured:
        backend.get_filing(_ACCESSION, expected_cik=_CIK)

    assert captured.value.state is AdapterState.NOT_FOUND
    assert captured.value.operation == "get_filing"
    assert bootstrap.load_calls == 1
    assert module.company_calls == [_CIK]
    assert module.filing_query_calls == [
        {"accession_number": _ACCESSION, "trigger_full_load": False},
        {"accession_number": _ACCESSION, "trigger_full_load": True},
    ]
    assert module.filing_get_calls == [_ACCESSION, _ACCESSION]
    assert module.global_lookup_calls == []


@pytest.mark.parametrize(
    ("library_error", "expected_type", "expected_state"),
    [
        (
            TooManyRequestsError("secret rate detail"),
            AdapterRateLimitError,
            AdapterState.RATE_LIMITED,
        ),
        (NotFoundError("secret not-found detail"), AdapterNotFoundError, AdapterState.NOT_FOUND),
        (
            TransportError("secret transport detail"),
            AdapterTransportError,
            AdapterState.TRANSPORT_ERROR,
        ),
        (ParsingError("secret parser detail"), AdapterParsingError, AdapterState.PARSING_ERROR),
        (
            ValidationError("secret selector detail"),
            AdapterValidationError,
            AdapterState.INVALID_REQUEST,
        ),
        (EdgarError("secret library detail"), AdapterLibraryError, AdapterState.LIBRARY_ERROR),
    ],
)
def test_get_filing_classifies_failure_once_without_retry_or_fallback(
    library_error: EdgarError,
    expected_type: type[EdgarToolsAdapterError],
    expected_state: AdapterState,
) -> None:
    module = _FakeEdgarModule(library_error)
    backend, bootstrap = _public_backend(module)

    with pytest.raises(expected_type) as captured:
        backend.get_filing(_ACCESSION, expected_cik=_CIK)

    assert captured.value.state is expected_state
    assert captured.value.operation == "get_filing"
    assert "secret" not in str(captured.value)
    assert bootstrap.load_calls == 1
    assert module.company_calls == [_CIK]
    assert module.filing_query_calls == [
        {"accession_number": _ACCESSION, "trigger_full_load": False}
    ]
    assert module.filing_get_calls == []
    assert module.index_calls == []
    assert module.global_lookup_calls == []


@pytest.mark.parametrize(
    ("accession_number", "expected_cik", "operation"),
    [
        ("not-an-accession", None, "validate_accession"),
        (_ACCESSION, "TFC", "validate_cik"),
    ],
)
def test_get_filing_rejects_invalid_selectors_before_bootstrap(
    accession_number: str,
    expected_cik: str | None,
    operation: str,
) -> None:
    module = _FakeEdgarModule(_library_filing())
    backend, bootstrap = _public_backend(module)

    with pytest.raises(AdapterValidationError) as captured:
        backend.get_filing(accession_number, expected_cik=expected_cik)

    assert captured.value.state is AdapterState.INVALID_REQUEST
    assert captured.value.operation == operation
    assert bootstrap.load_calls == 0
    assert module.company_calls == []
    assert module.global_lookup_calls == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", "0000000001"), ("92230", _CIK), (_CIK, _CIK)],
)
def test_cik_validation_normalizes_only_ascii_digits(value: str, expected: str) -> None:
    assert validate_cik(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "TFC",
        "00000922300",
        "+92230",
        "92 230",
        (
            "\N{ARABIC-INDIC DIGIT NINE}\N{ARABIC-INDIC DIGIT TWO}"
            "\N{ARABIC-INDIC DIGIT TWO}\N{ARABIC-INDIC DIGIT THREE}"
            "\N{ARABIC-INDIC DIGIT ZERO}"
        ),
    ],
)
def test_cik_validation_rejects_fuzzy_or_non_ascii_identifiers(value: str) -> None:
    with pytest.raises(AdapterValidationError) as captured:
        validate_cik(value)

    assert captured.value.state is AdapterState.INVALID_REQUEST
    assert captured.value.operation == "validate_cik"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "000009223026000100",
        "0000092230-2-000100",
        "0000092230-26-00100",
        "0000092230/26/000100",
        (
            "\N{ARABIC-INDIC DIGIT ZERO}\N{ARABIC-INDIC DIGIT ZERO}"
            "\N{ARABIC-INDIC DIGIT ZERO}\N{ARABIC-INDIC DIGIT ZERO}"
            "\N{ARABIC-INDIC DIGIT ZERO}\N{ARABIC-INDIC DIGIT NINE}"
            "\N{ARABIC-INDIC DIGIT TWO}\N{ARABIC-INDIC DIGIT TWO}"
            "\N{ARABIC-INDIC DIGIT THREE}\N{ARABIC-INDIC DIGIT ZERO}-26-000100"
        ),
    ],
)
def test_accession_validation_rejects_noncanonical_identifiers(value: str) -> None:
    with pytest.raises(AdapterValidationError) as captured:
        validate_accession(value)

    assert captured.value.state is AdapterState.INVALID_REQUEST
    assert captured.value.operation == "validate_accession"


def test_accession_validation_preserves_exact_canonical_identifier() -> None:
    assert validate_accession(_ACCESSION) == _ACCESSION


@pytest.mark.parametrize(
    "returned",
    [
        dataclasses.replace(_library_filing(), accession_number="0000092230-26-000101"),
        dataclasses.replace(_library_filing(), cik=1745916),
        dataclasses.replace(_library_filing(), cik=True),
    ],
)
def test_get_filing_rejects_library_selection_mismatches(returned: _LibraryFiling) -> None:
    module = _FakeEdgarModule(returned)
    backend, _ = _public_backend(module)

    with pytest.raises(AdapterSelectionError) as captured:
        backend.get_filing(_ACCESSION, expected_cik=_CIK)

    assert captured.value.state is AdapterState.SELECTION_MISMATCH
    assert captured.value.operation == "get_filing"
    assert module.global_lookup_calls == []


def test_filing_exposes_explicit_amendment_and_acceptance_metadata() -> None:
    filing = _expected_filing()

    assert filing.amendment is True
    assert filing.is_amendment is True
    assert filing.form == "10-Q/A"
    assert filing.acceptance_timestamp == _ACCEPTANCE_TIMESTAMP
    assert filing.report_period == filing.period_of_report == date(2026, 6, 30)
    assert filing.primary_document == "tfc-20260630.htm"
    assert filing.is_xbrl is True
    assert filing.is_inline_xbrl is True


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(cast("Any", value)):
            _assert_no_float(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_no_float(item)


def test_xbrl_preserves_raw_fact_context_unit_dimensions_and_decimals_without_float() -> None:
    raw_value = "00012345678901234567890.0100"
    dimensions = (
        XbrlDimension(axis="tfc:BusinessSegmentsAxis", member="tfc:ConsumerBankingMember"),
        XbrlDimension(
            axis="us-gaap:StatementScenarioAxis",
            member="us-gaap:ActualMember",
        ),
    )
    context = XbrlContext(
        context_id="D2026Q2Consumer",
        entity_identifier=_CIK,
        entity_scheme="http://www.sec.gov/CIK",
        period_type="duration",
        period_start="2026-04-01",
        period_end="2026-06-30",
        period_instant=None,
        dimensions=dimensions,
    )
    unit = XbrlUnit(
        unit_ref="USD-per-share",
        unit_type="divide",
        measure=None,
        numerator=("iso4217:USD",),
        denominator=("xbrli:shares",),
    )
    fact = XbrlFact(
        taxonomy="us-gaap",
        concept="EarningsPerShareDiluted",
        original_label="Diluted earnings per common share",
        raw_value=raw_value,
        context_ref=context.context_id,
        context=context,
        unit_ref=unit.unit_ref,
        unit=unit,
        decimals="-6",
        scale="3",
        precision="INF",
        fact_id="fact-1",
        instance_id="instance-1",
    )
    filing = XbrlFiling(
        cik=_CIK,
        accession_number=_ACCESSION,
        source_document="tfc-20260630.htm",
        source_url="https://www.sec.gov/Archives/example/tfc-20260630.htm",
        facts=(fact,),
        contexts=(context,),
        units=(unit,),
    )

    assert filing.facts[0].raw_value == raw_value
    assert isinstance(filing.facts[0].raw_value, str)
    assert filing.facts[0].element_id == "us-gaap:EarningsPerShareDiluted"
    assert filing.facts[0].context is filing.contexts[0]
    assert filing.facts[0].dimensions == dimensions
    assert filing.facts[0].unit is filing.units[0]
    assert filing.facts[0].decimals == "-6"
    assert filing.facts[0].scale == "3"
    assert filing.facts[0].precision == "INF"
    assert all(
        field.name not in {"numeric_value", "standardized_value"}
        for field in dataclasses.fields(XbrlFact)
    )
    _assert_no_float(filing)


def test_footnotes_and_signed_linkbase_arcs_preserve_raw_text_and_non_authority() -> None:
    footnote = XbrlFootnote(
        fact_id="fact-1",
        footnote_id="footnote-7",
        raw_text="Includes servicing rights measured at fair value.",
        language="en-US",
        role="http://www.xbrl.org/2003/role/footnote",
    )
    presentation = PresentationArc(
        role_uri="https://example.test/role/msr",
        parent_element_id="us-gaap:ServicingAssets",
        child_element_id="tfc:MortgageServicingRights",
        order="2.500",
        preferred_label="http://www.xbrl.org/2003/role/totalLabel",
    )
    calculation = CalculationArc(
        role_uri="https://example.test/role/msr",
        parent_element_id="us-gaap:ServicingAssets",
        child_element_id="tfc:MortgageServicingRights",
        order="2.500",
        weight="-1.0000",
    )
    definition = DefinitionArc(
        role_uri="https://example.test/role/segments",
        arcrole="http://xbrl.org/int/dim/arcrole/domain-member",
        source_element_id="tfc:BusinessSegmentsDomain",
        target_element_id="tfc:ConsumerBankingMember",
        order="1.000",
        context_element="segment",
        closed="false",
        usable="true",
    )
    issue = ViewerValidationIssue(
        classification=ViewerIssueClassification.EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY,
        severity="warning",
        code="viewer-total-mismatch",
        message="Viewer total differs from filing-specific facts.",
        raw_metadata=(RawMetadata(key="reported_weight", raw_value="-1.0000"),),
    )
    report = ViewerReport(
        short_name="MSR",
        long_name="Mortgage servicing rights",
        category="Notes",
        role="https://example.test/role/msr",
        html_file_name="R42.htm",
        position="42",
        group_type="document",
        concepts=("us-gaap:ServicingAssets", "tfc:MortgageServicingRights"),
        period_headers=("Jun. 30, 2026",),
    )
    structure = FilingStructure(
        cik=_CIK,
        accession_number=_ACCESSION,
        presentation_arcs=(presentation,),
        calculation_arcs=(calculation,),
        definition_arcs=(definition,),
        footnotes=(footnote,),
        viewer_reports=(report,),
        viewer_issues=(issue,),
    )

    assert structure.publication_authority is False
    assert structure.footnotes[0].raw_text == footnote.raw_text
    assert structure.calculation_arcs[0].weight == "-1.0000"
    assert isinstance(structure.calculation_arcs[0].weight, str)
    assert [arc.kind for arc in structure.linkbase_arcs] == [
        "presentation",
        "calculation",
        "definition",
    ]
    assert structure.linkbase_arcs[1].weight == "-1.0000"
    assert structure.viewer_issues[0].raw_metadata[0].raw_value == "-1.0000"
    _assert_no_float(structure)


def test_company_facts_are_raw_discovery_candidates_without_publication_authority() -> None:
    dimensions = (
        XbrlDimension(axis="tfc:BusinessSegmentsAxis", member="tfc:ConsumerBankingMember"),
    )
    candidate = CompanyFactCandidate(
        concept="ServicingAssetsAtFairValue",
        taxonomy="us-gaap",
        raw_value="001234567890.0100",
        unit="USD",
        period_start=None,
        period_end=date(2026, 6, 30),
        filing_date=date(2026, 8, 6),
        form="10-Q/A",
        accession_number=_ACCESSION,
        fiscal_year="2026",
        fiscal_period="Q2",
        dimensions=dimensions,
    )
    discovery = CompanyFactsDiscovery(
        cik=_CIK,
        company_name="Truist Financial Corporation",
        facts=(candidate,),
    )

    assert discovery.publication_authority is False
    assert discovery.facts[0].raw_value == "001234567890.0100"
    assert discovery.facts[0].dimensions == dimensions
    _assert_no_float(discovery)


def test_canonical_text_bytes_are_labeled_utf8_not_original_wire_bytes() -> None:
    text = "caf\N{LATIN SMALL LETTER E WITH ACUTE} \N{EURO SIGN}"
    payload = text.encode("utf-8")
    content = _acquired_content(payload)

    assert content.content.decode("utf-8") == text
    assert content.representation is ContentRepresentation.LIBRARY_TEXT_UTF8
    assert content.representation.value == "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
    assert "ORIGINAL" not in content.representation.value
    assert content.capture_method == "edgartools_attachment_text_utf8"
    assert content.sha256 == hashlib.sha256(payload).hexdigest()
    assert content.byte_length == len(payload)


def test_duplicate_retention_is_idempotent_with_stable_hash_and_location(tmp_path: Path) -> None:
    from mortgage_servicing_dashboard.edgartools_adapter.retention import (  # noqa: PLC0415
        GeneralEvidenceStore,
    )

    payload = "canonical filing text \N{EURO SIGN}".encode("utf-8")
    first_content = _acquired_content(payload)
    second_content = _acquired_content(
        payload,
        source_url="https://www.sec.gov/Archives/example/duplicate.htm",
    )
    store = GeneralEvidenceStore(tmp_path / "evidence")

    first = store.retain(first_content)
    second = store.retain(second_content)

    digest = hashlib.sha256(payload).hexdigest()
    assert first.content_sha256 == second.content_sha256 == digest
    assert first.byte_length == second.byte_length == len(payload)
    assert first.retention_location == second.retention_location == f"content-sha256://{digest}"
    assert first.representation is second.representation is ContentRepresentation.LIBRARY_TEXT_UTF8
    assert first.capture_method == second.capture_method == "edgartools_attachment_text_utf8"
    retained_files = tuple((tmp_path / "evidence").rglob("*.bin"))
    assert len(retained_files) == 1
    assert retained_files[0].read_bytes() == payload
    assert first.retained_at.tzinfo is not None
    assert second.retained_at.tzinfo is not None


def test_retention_rejects_content_hash_mismatch_without_writing_bytes(tmp_path: Path) -> None:
    from mortgage_servicing_dashboard.edgartools_adapter.retention import (  # noqa: PLC0415
        GeneralEvidenceStore,
    )

    content = dataclasses.replace(_acquired_content(b"filing"), sha256="0" * 64)
    root = tmp_path / "evidence"

    with pytest.raises(AdapterIntegrityError) as captured:
        GeneralEvidenceStore(root).retain(content)

    assert captured.value.state is AdapterState.INTEGRITY_ERROR
    assert captured.value.operation == "retain_attachment"
    assert tuple(root.rglob("*.bin")) == ()


def _attachment() -> Attachment:
    return Attachment(
        cik=_CIK,
        accession_number=_ACCESSION,
        document="earnings-release.htm",
        sequence="2",
        description="Quarterly earnings release",
        attachment_type="EX-99.1",
        size=321,
        source_url="https://www.sec.gov/Archives/example/earnings-release.htm",
        is_primary=False,
        is_binary=False,
    )


def _attachment_acquisition() -> AttachmentAcquisition:
    content = dataclasses.replace(
        _acquired_content(b"<html>reported filing text</html>"),
        document="earnings-release.htm",
        source_url="https://www.sec.gov/Archives/example/earnings-release.htm",
    )
    return AttachmentAcquisition(
        attachment=_attachment(),
        content=content,
        retained=None,
    )


def _empty_xbrl_filing() -> XbrlFiling:
    return XbrlFiling(
        cik=_CIK,
        accession_number=_ACCESSION,
        source_document="tfc-20260630.htm",
        source_url="https://www.sec.gov/Archives/example/tfc-20260630.htm",
        facts=(),
        contexts=(),
        units=(),
    )


def _empty_structure() -> FilingStructure:
    return FilingStructure(
        cik=_CIK,
        accession_number=_ACCESSION,
        presentation_arcs=(),
        calculation_arcs=(),
        definition_arcs=(),
        footnotes=(),
        viewer_reports=(),
        viewer_issues=(),
    )


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.acquisition = _attachment_acquisition()

    def resolve_company(self, cik_or_ticker: str) -> Company:
        self.calls.append(("resolve_company", cik_or_ticker))
        return Company(cik=_CIK, name="Truist Financial Corporation", tickers=("TFC",))

    def list_filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: FilingDateFilter = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        self.calls.append(
            (
                "list_filings",
                (cik, forms, filing_date, include_amendments),
            )
        )
        return (_expected_filing(),)

    def get_filing(self, accession_number: str, *, expected_cik: str | None = None) -> Filing:
        self.calls.append(("get_filing", (accession_number, expected_cik)))
        return _expected_filing()

    def list_attachments(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        self.calls.append(("list_attachments", (accession_number, expected_cik)))
        return (_attachment(),)

    def acquire_attachment(
        self,
        accession_number: str,
        document: str,
        *,
        expected_cik: str | None = None,
    ) -> AttachmentAcquisition:
        self.calls.append(
            (
                "acquire_attachment",
                (accession_number, document, expected_cik),
            )
        )
        return self.acquisition

    def get_filing_xbrl(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> XbrlFiling | None:
        self.calls.append(("get_filing_xbrl", (accession_number, expected_cik)))
        return _empty_xbrl_filing()

    def get_company_facts(self, cik: str) -> CompanyFactsDiscovery | None:
        self.calls.append(("get_company_facts", cik))
        return CompanyFactsDiscovery(cik=_CIK, company_name="Truist", facts=())

    def get_filing_structure(
        self,
        accession_number: str,
        *,
        expected_cik: str | None = None,
    ) -> FilingStructure | None:
        self.calls.append(("get_filing_structure", (accession_number, expected_cik)))
        return _empty_structure()


class _RecordingEvidenceStore:
    def __init__(self) -> None:
        self.contents: list[AcquiredContent] = []

    def retain(self, content: AcquiredContent) -> RetainedContent:
        self.contents.append(content)
        return RetainedContent(
            content_sha256=content.sha256,
            byte_length=content.byte_length,
            retention_location=f"content-sha256://{content.sha256}",
            retained_at=_NOW,
            representation=content.representation,
            capture_method=content.capture_method,
            media_type=content.media_type,
            source_url=content.source_url,
        )


def test_facade_delegates_every_read_operation_with_exact_typed_selectors() -> None:
    backend = _RecordingBackend()
    adapter = EdgarToolsAdapter(cast("EdgarToolsBackend", backend))
    filing_dates = (date(2026, 1, 1), date(2026, 8, 13))

    assert adapter.company("TFC").cik == _CIK
    assert adapter.filings(
        _CIK,
        forms=("10-Q", "10-K"),
        filing_date=filing_dates,
        include_amendments=False,
    ) == (_expected_filing(),)
    assert adapter.filing(_ACCESSION, expected_cik=_CIK) == _expected_filing()
    assert adapter.attachments(_ACCESSION, expected_cik=_CIK) == (_attachment(),)
    assert adapter.filing_xbrl(_ACCESSION, expected_cik=_CIK) == _empty_xbrl_filing()
    assert adapter.company_facts(_CIK) == CompanyFactsDiscovery(
        cik=_CIK,
        company_name="Truist",
        facts=(),
    )
    assert adapter.filing_structure(_ACCESSION, expected_cik=_CIK) == _empty_structure()
    assert backend.calls == [
        ("resolve_company", "TFC"),
        ("list_filings", (_CIK, ("10-Q", "10-K"), filing_dates, False)),
        ("get_filing", (_ACCESSION, _CIK)),
        ("list_attachments", (_ACCESSION, _CIK)),
        ("get_filing_xbrl", (_ACCESSION, _CIK)),
        ("get_company_facts", _CIK),
        ("get_filing_structure", (_ACCESSION, _CIK)),
    ]


def test_facade_acquisition_skips_retention_only_when_explicitly_requested() -> None:
    backend = _RecordingBackend()
    adapter = EdgarToolsAdapter(cast("EdgarToolsBackend", backend))

    result = adapter.acquire_attachment(
        _ACCESSION,
        "earnings-release.htm",
        expected_cik=_CIK,
        retain=False,
    )

    assert result is backend.acquisition
    assert result.retained is None


def test_facade_acquisition_retains_successful_content_once() -> None:
    backend = _RecordingBackend()
    store = _RecordingEvidenceStore()
    adapter = EdgarToolsAdapter(
        cast("EdgarToolsBackend", backend),
        evidence_store=store,
    )

    result = adapter.acquire_attachment(
        _ACCESSION,
        "earnings-release.htm",
        expected_cik=_CIK,
    )

    assert store.contents == [backend.acquisition.content]
    assert result.retained is not None
    assert result.retained.content_sha256 == backend.acquisition.content.sha256


def test_facade_rejects_default_retention_without_an_evidence_store() -> None:
    adapter = EdgarToolsAdapter(cast("EdgarToolsBackend", _RecordingBackend()))

    with pytest.raises(AdapterConfigurationError) as captured:
        adapter.acquire_attachment(_ACCESSION, "earnings-release.htm")

    assert captured.value.operation == "acquire_attachment"
    assert captured.value.state is AdapterState.CONFIGURATION_ERROR


def test_production_facade_construction_remains_lazy_and_identity_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bootstrap_state(monkeypatch)
    config = EdgarBootstrapConfig(identity=SecretStr(_IDENTITY), repository_root=tmp_path)

    adapter = EdgarToolsAdapter.from_config(config)

    assert "edgar" not in sys.modules
    with pytest.raises(AdapterValidationError):
        adapter.company("")
    assert "edgar" not in sys.modules


@dataclasses.dataclass(slots=True)
class _LibraryAttachment:
    sequence_number: str
    description: str
    document: str
    document_type: str
    size: int
    url: str
    binary: bool
    payload: str | bytes | BaseException
    download_calls: int = 0

    def is_binary(self) -> bool:
        return self.binary

    def download(self) -> str | bytes:
        self.download_calls += 1
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _LibraryAttachments:
    def __init__(
        self,
        attachments: tuple[_LibraryAttachment, ...],
        *,
        primary_documents: tuple[_LibraryAttachment, ...],
    ) -> None:
        self._attachments = attachments
        self.primary_documents = primary_documents

    def __iter__(self) -> Any:
        return iter(self._attachments)


class _LibraryFilingBundle:
    def __init__(
        self,
        *,
        attachments: object | None = None,
        xbrl: object | None = None,
        viewer: object | BaseException | None = None,
    ) -> None:
        filing = _library_filing()
        for field in dataclasses.fields(filing):
            setattr(self, field.name, getattr(filing, field.name))
        self._attachments = attachments
        self._xbrl = xbrl
        self._viewer = viewer
        self.filing_url = "https://www.sec.gov/Archives/example/tfc-20260630.htm"

    @property
    def attachments(self) -> object | None:
        if isinstance(self._attachments, BaseException):
            raise self._attachments
        return self._attachments

    @property
    def viewer(self) -> object | None:
        if isinstance(self._viewer, BaseException):
            raise self._viewer
        return self._viewer

    def xbrl(self) -> object | None:
        if isinstance(self._xbrl, BaseException):
            raise self._xbrl
        return self._xbrl


class _CapabilityFilings:
    def __init__(self, filings: tuple[object, ...]) -> None:
        self._filings = filings

    def __len__(self) -> int:
        return len(self._filings)

    def __getitem__(self, index: int) -> object:
        return self._filings[index]

    def get(self, accession_number: str) -> object | None:
        return next(
            (
                filing
                for filing in self._filings
                if getattr(filing, "accession_number", None) == accession_number
            ),
            None,
        )


class _CapabilityCompany:
    def __init__(
        self,
        *,
        filings: tuple[object, ...] | BaseException = (),
        facts: object | BaseException | None = None,
        not_found: bool = False,
    ) -> None:
        self.cik = 92230
        self.name = "Truist Financial Corporation"
        self.tickers = ["TFC", "TFC-PQ"]
        self.not_found = not_found
        self._filings = filings
        self._facts = facts
        self.filing_queries: list[dict[str, object]] = []

    def get_filings(self, **kwargs: object) -> _CapabilityFilings:
        self.filing_queries.append(kwargs)
        if isinstance(self._filings, BaseException):
            raise self._filings
        return _CapabilityFilings(self._filings)

    def get_facts(self) -> object | None:
        if isinstance(self._facts, BaseException):
            raise self._facts
        return self._facts


class _CapabilityEdgarModule(ModuleType):
    def __init__(
        self,
        company: _CapabilityCompany | BaseException,
        filing: object | BaseException | None,
    ) -> None:
        super().__init__("edgar")
        self._company_result = company
        self._filing_result = filing
        self.company_calls: list[object] = []
        self.global_lookup_calls: list[str] = []
        self.Company = self._company
        self.EdgarError = EdgarError

    def _company(self, selector: object) -> _CapabilityCompany:
        self.company_calls.append(selector)
        if isinstance(self._company_result, BaseException):
            raise self._company_result
        return self._company_result

    def get_by_accession_number(self, accession_number: str) -> object | None:
        self.global_lookup_calls.append(accession_number)
        if isinstance(self._filing_result, BaseException):
            raise self._filing_result
        return self._filing_result


def _capability_backend(
    company: _CapabilityCompany | BaseException,
    filing: object | BaseException | None,
    *,
    clock: Any | None = None,
) -> tuple[PublicEdgarToolsBackend, _CapabilityEdgarModule]:
    module = _CapabilityEdgarModule(company, filing)
    bootstrap = _FakeBootstrap(module)
    backend = PublicEdgarToolsBackend(
        cast("EdgarBootstrap", bootstrap),
        clock=clock,
    )
    return backend, module


@pytest.mark.parametrize(
    ("selector", "library_selector"),
    [("92230", _CIK), ("tfc", "TFC")],
)
def test_public_backend_resolves_exact_company_selector_to_stable_cik(
    selector: str,
    library_selector: str,
) -> None:
    company = _CapabilityCompany()
    backend, module = _capability_backend(company, None)

    result = backend.resolve_company(selector)

    assert result == Company(
        cik=_CIK,
        name="Truist Financial Corporation",
        tickers=("TFC", "TFC-PQ"),
    )
    assert module.company_calls == [library_selector]


def test_public_backend_lists_filings_with_explicit_filters_and_exact_mapping() -> None:
    filing = _LibraryFilingBundle()
    company = _CapabilityCompany(filings=(filing,))
    backend, module = _capability_backend(company, None)
    filing_dates = (date(2026, 1, 1), date(2026, 8, 13))

    results = backend.list_filings(
        "92230",
        forms=("10-Q", "8-K"),
        filing_date=filing_dates,
        include_amendments=False,
    )

    assert results == (_expected_filing(),)
    assert module.company_calls == [_CIK]
    assert company.filing_queries == [
        {
            "form": ["10-Q", "8-K"],
            "filing_date": ("2026-01-01", "2026-08-13"),
            "amendments": False,
            "trigger_full_load": True,
        }
    ]


def _library_attachments() -> tuple[
    _LibraryAttachments,
    _LibraryAttachment,
    _LibraryAttachment,
]:
    primary = _LibraryAttachment(
        sequence_number="1",
        description="Quarterly report",
        document="tfc-20260630.htm",
        document_type="10-Q",
        size=123,
        url="https://www.sec.gov/Archives/example/tfc-20260630.htm",
        binary=False,
        payload="<html>quarterly filing</html>",
    )
    exhibit = _LibraryAttachment(
        sequence_number="2",
        description="Quarterly earnings release",
        document="earnings-release.pdf",
        document_type="EX-99.1",
        size=456,
        url="https://www.sec.gov/Archives/example/earnings-release.pdf",
        binary=True,
        payload=b"%PDF reduced synthetic fixture",
    )
    return (
        _LibraryAttachments((primary, exhibit), primary_documents=(primary,)),
        primary,
        exhibit,
    )


def test_public_backend_enumerates_primary_document_and_exhibit_without_fetching() -> None:
    attachments, _, _ = _library_attachments()
    filing = _LibraryFilingBundle(attachments=attachments)
    backend, _ = _capability_backend(_CapabilityCompany(filings=(filing,)), filing)

    results = backend.list_attachments(_ACCESSION, expected_cik=_CIK)

    assert [(item.document, item.attachment_type, item.is_primary) for item in results] == [
        ("tfc-20260630.htm", "10-Q", True),
        ("earnings-release.pdf", "EX-99.1", False),
    ]
    assert results[1].is_binary is True
    assert all(item.url.startswith("https://www.sec.gov/") for item in results)


@pytest.mark.parametrize(
    ("document", "expected_representation", "expected_capture_method"),
    [
        (
            "tfc-20260630.htm",
            ContentRepresentation.LIBRARY_TEXT_UTF8,
            "edgartools_attachment_text_utf8",
        ),
        (
            "earnings-release.pdf",
            ContentRepresentation.LIBRARY_BINARY,
            "edgartools_attachment_binary",
        ),
    ],
)
def test_public_backend_acquires_exact_text_or_binary_attachment_with_hash(
    document: str,
    expected_representation: ContentRepresentation,
    expected_capture_method: str,
) -> None:
    attachments, primary, exhibit = _library_attachments()
    filing = _LibraryFilingBundle(attachments=attachments)
    backend, _ = _capability_backend(
        _CapabilityCompany(filings=(filing,)),
        filing,
        clock=lambda: _NOW,
    )

    result = backend.acquire_attachment(_ACCESSION, document, expected_cik=_CIK)

    selected = primary if document == primary.document else exhibit
    expected_bytes = (
        selected.payload.encode("utf-8") if isinstance(selected.payload, str) else selected.payload
    )
    assert isinstance(expected_bytes, bytes)
    assert result.attachment.document == document
    assert result.content.content == expected_bytes
    assert result.content.representation is expected_representation
    assert result.content.capture_method == expected_capture_method
    assert result.content.sha256 == hashlib.sha256(expected_bytes).hexdigest()
    assert result.content.retrieved_at == _NOW
    assert result.retained is None
    assert selected.download_calls == 1


class _RawXbrlFact:
    element_id = "us-gaap:EarningsPerShareDiluted"
    context_ref = "D2026Q2Consumer"
    value = "00012345678901234567890.0100"
    unit_ref = "USD-per-share"
    decimals = "-6"
    scale = "3"
    precision = "INF"
    fact_id = "fact-1"
    instance_id = "instance-1"

    @property
    def numeric_value(self) -> float:
        raise AssertionError


def _library_raw_xbrl() -> SimpleNamespace:
    context = SimpleNamespace(
        context_id="D2026Q2Consumer",
        entity={"identifier": _CIK, "scheme": "http://www.sec.gov/CIK"},
        period={
            "type": "duration",
            "startDate": "2026-04-01",
            "endDate": "2026-06-30",
        },
        dimensions={
            "tfc:BusinessSegmentsAxis": "tfc:ConsumerBankingMember",
            "us-gaap:StatementScenarioAxis": "us-gaap:ActualMember",
        },
    )
    unit = {
        "type": "divide",
        "numerator": ["iso4217:USD"],
        "denominator": ["xbrli:shares"],
    }
    element = SimpleNamespace(
        labels={"http://www.xbrl.org/2003/role/label": ("Diluted earnings per common share")}
    )
    return SimpleNamespace(
        contexts={context.context_id: context},
        units={"USD-per-share": unit},
        parser=SimpleNamespace(facts={"fact-1": _RawXbrlFact()}),
        element_catalog={_RawXbrlFact.element_id: element},
    )


def test_public_backend_maps_filing_xbrl_from_raw_value_with_exact_context() -> None:
    library_xbrl = _library_raw_xbrl()
    filing = _LibraryFilingBundle(xbrl=library_xbrl)
    backend, _ = _capability_backend(
        _CapabilityCompany(filings=(filing,)),
        filing,
    )

    result = backend.get_filing_xbrl(_ACCESSION, expected_cik=_CIK)

    assert result is not None
    assert result.cik == _CIK
    assert result.source_document == "tfc-20260630.htm"
    assert result.facts[0].raw_value == _RawXbrlFact.value
    assert result.facts[0].original_label == "Diluted earnings per common share"
    assert result.facts[0].decimals == "-6"
    assert result.facts[0].scale == "3"
    assert result.facts[0].precision == "INF"
    assert result.facts[0].context is result.contexts[0]
    assert result.facts[0].unit is result.units[0]
    assert result.contexts[0].dimensions == (
        XbrlDimension(
            axis="tfc:BusinessSegmentsAxis",
            member="tfc:ConsumerBankingMember",
        ),
        XbrlDimension(
            axis="us-gaap:StatementScenarioAxis",
            member="us-gaap:ActualMember",
        ),
    )
    _assert_no_float(result)


class _CompanyFactsCollection:
    cik = 92230
    name = "Truist Financial Corporation"

    def __init__(self, facts: tuple[object, ...]) -> None:
        self._facts = facts

    def __iter__(self) -> Any:
        return iter(self._facts)


def test_public_backend_keeps_company_facts_discovery_only() -> None:
    raw_value = "0012345678901234567890.0100"
    library_fact = SimpleNamespace(
        concept="us-gaap:ServicingAssetsAtFairValue",
        taxonomy="us-gaap",
        value=raw_value,
        unit="USD",
        period_start=None,
        period_end=date(2026, 6, 30),
        filing_date=date(2026, 8, 6),
        form_type="10-Q/A",
        accession=_ACCESSION,
        fiscal_year=2026,
        fiscal_period="Q2",
        dimensions={
            "tfc:BusinessSegmentsAxis": "tfc:ConsumerBankingMember",
        },
    )
    library_facts = _CompanyFactsCollection((library_fact,))
    company = _CapabilityCompany(facts=library_facts)
    backend, module = _capability_backend(company, None)

    result = backend.get_company_facts("92230")

    assert result is not None
    assert module.company_calls == [_CIK]
    assert result.publication_authority is False
    assert result.facts[0].raw_value == raw_value
    assert result.facts[0].fiscal_year == "2026"
    assert result.facts[0].dimensions == (
        XbrlDimension(
            axis="tfc:BusinessSegmentsAxis",
            member="tfc:ConsumerBankingMember",
        ),
    )
    _assert_no_float(result)


class _LibraryViewer:
    def __init__(self) -> None:
        self.all_reports = [
            SimpleNamespace(
                short_name="Balance Sheet",
                long_name="Consolidated Balance Sheets",
                category="Statements",
                role="https://example.test/role/balance-sheet",
                html_file_name="R2.htm",
                position=2,
                group_type="document",
                concepts=["us-gaap:Assets", "us-gaap:Liabilities"],
                period_headers=["Jun. 30, 2026"],
            )
        ]

    def validate(self) -> list[dict[str, object]]:
        return [
            {
                "parent": SimpleNamespace(id="us-gaap:Assets"),
                "role": "Balance Sheet",
                "expected": 100.0,
                "computed": 101.0,
                "difference": -1.0,
                "valid": False,
            }
        ]

    def compare(self, _xbrl: object) -> SimpleNamespace:
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    match=False,
                    xbrl_value=99.0,
                    concept_id="us-gaap:Assets",
                    period="Jun. 30, 2026",
                    report="Balance Sheet",
                )
            ]
        )


def _library_structural_xbrl() -> SimpleNamespace:
    presentation_parent = SimpleNamespace(
        children=["tfc:MortgageServicingRights"],
        child_preferred_labels=["http://www.xbrl.org/2003/role/totalLabel"],
    )
    presentation_child = SimpleNamespace(
        children=[],
        child_preferred_labels=[],
        order=2.5,
        preferred_label=None,
    )
    calculation_parent = SimpleNamespace(children=["tfc:MortgageServicingRights"])
    calculation_child = SimpleNamespace(children=[], order=2.5, weight=-1.0)
    table = SimpleNamespace(
        element_id="tfc:ServicingTable",
        context_element="segment",
        closed=False,
        line_items=["tfc:ServicingLineItems"],
        axes=["tfc:BusinessSegmentsAxis"],
    )
    axis = SimpleNamespace(
        domain_id="tfc:BusinessSegmentsDomain",
        default_member_id="tfc:AllSegmentsMember",
    )
    domain = SimpleNamespace(members=["tfc:ConsumerBankingMember"])
    footnote = SimpleNamespace(
        footnote_id="footnote-7",
        text="Includes servicing rights measured at fair value.",
        lang="en-US",
        role="http://www.xbrl.org/2003/role/footnote",
        related_fact_ids=["fact-1"],
    )
    return SimpleNamespace(
        presentation_trees={
            "https://example.test/role/msr": SimpleNamespace(
                all_nodes={
                    "us-gaap:ServicingAssets": presentation_parent,
                    "tfc:MortgageServicingRights": presentation_child,
                }
            )
        },
        calculation_trees={
            "https://example.test/role/msr": SimpleNamespace(
                all_nodes={
                    "us-gaap:ServicingAssets": calculation_parent,
                    "tfc:MortgageServicingRights": calculation_child,
                }
            )
        },
        tables={"https://example.test/role/segments": [table]},
        axes={"tfc:BusinessSegmentsAxis": axis},
        domains={"tfc:BusinessSegmentsDomain": domain},
        footnotes={"footnote-7": footnote},
    )


def test_public_backend_maps_signed_structure_and_validation_only_viewer_issues() -> None:
    library_xbrl = _library_structural_xbrl()
    viewer = _LibraryViewer()
    filing = _LibraryFilingBundle(xbrl=library_xbrl, viewer=viewer)
    backend, _ = _capability_backend(
        _CapabilityCompany(filings=(filing,)),
        filing,
    )

    result = backend.get_filing_structure(_ACCESSION, expected_cik=_CIK)

    assert result is not None
    assert result.publication_authority is False
    assert result.presentation_arcs[0].order == "2.5"
    assert result.calculation_arcs[0].weight == "-1.0"
    assert result.calculation_arcs[0].order == "2.5"
    assert {arc.arcrole for arc in result.definition_arcs} == {
        "http://xbrl.org/int/dim/arcrole/all",
        "http://xbrl.org/int/dim/arcrole/hypercube-dimension",
        "http://xbrl.org/int/dim/arcrole/dimension-domain",
        "http://xbrl.org/int/dim/arcrole/domain-member",
        "http://xbrl.org/int/dim/arcrole/dimension-default",
    }
    assert result.footnotes[0].raw_text == ("Includes servicing rights measured at fair value.")
    assert result.viewer_reports[0].concepts == (
        "us-gaap:Assets",
        "us-gaap:Liabilities",
    )
    assert [issue.code for issue in result.viewer_issues] == [
        "viewer-calculation-mismatch",
        "viewer-xbrl-mismatch",
    ]
    assert result.viewer_issues[1].raw_metadata == (
        RawMetadata(key="concept", raw_value="us-gaap:Assets"),
        RawMetadata(key="period", raw_value="Jun. 30, 2026"),
        RawMetadata(key="report", raw_value="Balance Sheet"),
    )
    assert all(
        metadata.key not in {"expected", "computed", "difference", "xbrl_value"}
        for issue in result.viewer_issues
        for metadata in issue.raw_metadata
    )
    _assert_no_float(result)


@pytest.mark.parametrize(
    "selector",
    [cast("Any", None), " TFC", "TFC ", "T_FC", "TFC\n"],
)
def test_resolve_company_rejects_nonexact_selectors_before_bootstrap(selector: object) -> None:
    backend, module = _capability_backend(_CapabilityCompany(), None)

    with pytest.raises(AdapterValidationError) as captured:
        backend.resolve_company(cast("str", selector))

    assert captured.value.operation == "resolve_company"
    assert captured.value.state is AdapterState.INVALID_REQUEST
    assert module.company_calls == []


@pytest.mark.parametrize(
    ("selector", "not_found", "returned_cik", "tickers"),
    [
        ("TFC", True, 92230, ["TFC"]),
        ("92230", False, 1745916, ["TFC"]),
        ("TFC", False, 92230, ["OTHER"]),
    ],
)
def test_resolve_company_fails_closed_on_absence_or_identity_mismatch(
    selector: str,
    not_found: object,
    returned_cik: object,
    tickers: object,
) -> None:
    company = _CapabilityCompany(not_found=cast("bool", not_found))
    company.cik = returned_cik
    company.tickers = cast("Any", tickers)
    backend, module = _capability_backend(company, None)
    expected_type = AdapterNotFoundError if not_found else AdapterSelectionError

    with pytest.raises(expected_type) as captured:
        backend.resolve_company(selector)

    assert captured.value.operation == "resolve_company"
    assert captured.value.state in {AdapterState.NOT_FOUND, AdapterState.SELECTION_MISMATCH}
    assert module.company_calls == [selector.upper() if not selector.isdecimal() else _CIK]


@pytest.mark.parametrize("tickers", ["TFC", [cast("Any", object())], ["T_FC"]])
def test_resolve_company_rejects_malformed_library_tickers(tickers: object) -> None:
    company = _CapabilityCompany()
    company.tickers = cast("Any", tickers)
    backend, _ = _capability_backend(company, None)

    with pytest.raises(AdapterParsingError) as captured:
        backend.resolve_company("92230")

    assert captured.value.operation == "resolve_company"
    assert captured.value.state is AdapterState.PARSING_ERROR


def test_resolve_company_deduplicates_case_normalized_tickers_and_allows_absence() -> None:
    company = _CapabilityCompany()
    company.tickers = ["tfc", "TFC", "TFC-PQ"]
    backend, _ = _capability_backend(company, None)

    result = backend.resolve_company("92230")

    assert result.tickers == ("TFC", "TFC-PQ")

    company.tickers = None
    assert backend.resolve_company("92230").tickers == ()


@pytest.mark.parametrize(
    ("library_error", "expected_type", "expected_state"),
    [
        (
            TransportError("secret transport detail"),
            AdapterTransportError,
            AdapterState.TRANSPORT_ERROR,
        ),
        (EdgarError("secret library detail"), AdapterLibraryError, AdapterState.LIBRARY_ERROR),
    ],
)
def test_resolve_company_maps_one_library_failure_without_retry(
    library_error: EdgarError,
    expected_type: type[EdgarToolsAdapterError],
    expected_state: AdapterState,
) -> None:
    backend, module = _capability_backend(library_error, None)

    with pytest.raises(expected_type) as captured:
        backend.resolve_company("TFC")

    assert captured.value.operation == "resolve_company"
    assert captured.value.state is expected_state
    assert "secret" not in str(captured.value)
    assert module.company_calls == ["TFC"]


def test_resolve_company_preserves_foreign_programming_errors() -> None:
    failure = RuntimeError("local fake defect")
    backend, module = _capability_backend(failure, None)

    with pytest.raises(RuntimeError) as captured:
        backend.resolve_company("TFC")

    assert captured.value is failure
    assert module.company_calls == ["TFC"]


def test_list_filings_preserves_default_filters_and_normalizes_duplicates() -> None:
    company = _CapabilityCompany()
    backend, _ = _capability_backend(company, None)

    assert backend.list_filings("92230") == ()
    assert (
        backend.list_filings(
            "92230",
            forms=("10-Q", "10-Q"),
            filing_date=date(2026, 8, 6),
        )
        == ()
    )
    assert company.filing_queries == [
        {
            "form": None,
            "filing_date": None,
            "amendments": True,
            "trigger_full_load": True,
        },
        {
            "form": ["10-Q"],
            "filing_date": "2026-08-06",
            "amendments": True,
            "trigger_full_load": True,
        },
    ]


@pytest.mark.parametrize(
    "forms",
    [
        cast("Any", ["10-Q"]),
        ("",),
        ("10 Q",),
        (" 10-Q",),
        ("10-Q ",),
        ("\N{ARABIC LETTER AIN}",),
        (cast("Any", 10),),
    ],
)
def test_list_filings_rejects_malformed_forms_before_bootstrap(forms: object) -> None:
    backend, module = _capability_backend(_CapabilityCompany(), None)

    with pytest.raises(AdapterValidationError) as captured:
        backend.list_filings(_CIK, forms=cast("tuple[str, ...]", forms))

    assert captured.value.operation == "list_filings"
    assert module.company_calls == []


@pytest.mark.parametrize(
    "filing_date",
    [
        datetime(2026, 8, 6, tzinfo=UTC),
        (date(2026, 8, 6),),
        (date(2026, 8, 7), date(2026, 8, 6)),
        (datetime(2026, 8, 6, tzinfo=UTC), date(2026, 8, 7)),
        (cast("Any", "2026-08-06"), date(2026, 8, 7)),
    ],
)
def test_list_filings_rejects_ambiguous_date_filters_before_bootstrap(
    filing_date: object,
) -> None:
    backend, module = _capability_backend(_CapabilityCompany(), None)

    with pytest.raises(AdapterValidationError) as captured:
        backend.list_filings(_CIK, filing_date=cast("FilingDateFilter", filing_date))

    assert captured.value.operation == "list_filings"
    assert module.company_calls == []


def test_list_filings_rejects_nonboolean_amendment_filter_before_bootstrap() -> None:
    backend, module = _capability_backend(_CapabilityCompany(), None)

    with pytest.raises(AdapterValidationError) as captured:
        backend.list_filings(_CIK, include_amendments=cast("bool", 1))

    assert captured.value.operation == "list_filings"
    assert module.company_calls == []


def test_list_filings_maps_one_transport_failure_without_retry() -> None:
    company = _CapabilityCompany(filings=TransportError("secret transport detail"))
    backend, module = _capability_backend(company, None)

    with pytest.raises(AdapterTransportError) as captured:
        backend.list_filings(_CIK)

    assert captured.value.operation == "list_filings"
    assert company.filing_queries == [
        {
            "form": None,
            "filing_date": None,
            "amendments": True,
            "trigger_full_load": True,
        }
    ]
    assert module.company_calls == [_CIK]


def test_list_filings_rejects_noncanonical_returned_accession() -> None:
    returned = dataclasses.replace(_library_filing(), accession_number="not-an-accession")
    company = _CapabilityCompany(filings=(returned,))
    backend, _ = _capability_backend(company, None)

    with pytest.raises(AdapterParsingError) as captured:
        backend.list_filings(_CIK)

    assert captured.value.operation == "list_filings"
    assert captured.value.state is AdapterState.PARSING_ERROR


@pytest.mark.parametrize(
    "operation",
    ["list_attachments", "acquire_attachment", "get_filing_xbrl", "get_filing_structure"],
)
def test_filing_capabilities_confirm_absence_once_without_global_fallback(
    operation: str,
) -> None:
    company = _CapabilityCompany()
    backend, module = _capability_backend(company, _LibraryFilingBundle())

    def invoke() -> object:
        if operation == "list_attachments":
            return backend.list_attachments(_ACCESSION, expected_cik=_CIK)
        if operation == "acquire_attachment":
            return backend.acquire_attachment(
                _ACCESSION,
                "filing.htm",
                expected_cik=_CIK,
            )
        if operation == "get_filing_xbrl":
            return backend.get_filing_xbrl(_ACCESSION, expected_cik=_CIK)
        return backend.get_filing_structure(_ACCESSION, expected_cik=_CIK)

    with pytest.raises(AdapterNotFoundError) as captured:
        invoke()

    assert captured.value.operation == operation
    assert company.filing_queries == [
        {"accession_number": _ACCESSION, "trigger_full_load": False},
        {"accession_number": _ACCESSION, "trigger_full_load": True},
    ]
    assert module.global_lookup_calls == []


@pytest.mark.parametrize(
    "document",
    [
        cast("Any", None),
        "",
        " filing.htm",
        "filing.htm ",
        "dir/filing.htm",
        "dir\\filing.htm",
        "bad\x00.htm",
    ],
)
def test_acquire_attachment_rejects_nonexact_document_names_before_bootstrap(
    document: object,
) -> None:
    backend, module = _capability_backend(_CapabilityCompany(), None)

    with pytest.raises(AdapterValidationError) as captured:
        backend.acquire_attachment(_ACCESSION, cast("str", document))

    assert captured.value.operation == "acquire_attachment"
    assert module.company_calls == []


def test_acquire_attachment_distinguishes_absence_from_duplicate_names() -> None:
    attachments, primary, _ = _library_attachments()
    missing_filing = _LibraryFilingBundle(attachments=attachments)
    missing_backend, _ = _capability_backend(
        _CapabilityCompany(filings=(missing_filing,)),
        missing_filing,
    )

    with pytest.raises(AdapterNotFoundError) as absent:
        missing_backend.acquire_attachment(_ACCESSION, "missing.htm", expected_cik=_CIK)

    assert absent.value.state is AdapterState.NOT_FOUND
    assert primary.download_calls == 0

    duplicate = dataclasses.replace(primary)
    duplicate_attachments = _LibraryAttachments(
        (primary, duplicate),
        primary_documents=(primary,),
    )
    duplicate_filing = _LibraryFilingBundle(attachments=duplicate_attachments)
    duplicate_backend, _ = _capability_backend(
        _CapabilityCompany(filings=(duplicate_filing,)),
        duplicate_filing,
    )

    with pytest.raises(AdapterSelectionError) as duplicated:
        duplicate_backend.acquire_attachment(_ACCESSION, primary.document, expected_cik=_CIK)

    assert duplicated.value.state is AdapterState.SELECTION_MISMATCH
    assert primary.download_calls == duplicate.download_calls == 0


@pytest.mark.parametrize(
    ("payload", "clock", "expected_type", "expected_state"),
    [
        (cast("Any", 123), lambda: _NOW, AdapterParsingError, AdapterState.PARSING_ERROR),
        (
            "canonical text",
            lambda: _NOW.replace(tzinfo=None),
            AdapterConfigurationError,
            AdapterState.CONFIGURATION_ERROR,
        ),
    ],
)
def test_acquire_attachment_rejects_invalid_content_or_naive_clock(
    payload: object,
    clock: Any,
    expected_type: type[EdgarToolsAdapterError],
    expected_state: AdapterState,
) -> None:
    attachment = _LibraryAttachment(
        sequence_number="1",
        description="Filing",
        document="filing.htm",
        document_type="10-Q",
        size=10,
        url="https://www.sec.gov/Archives/example/filing.htm",
        binary=False,
        payload=cast("Any", payload),
    )
    attachments = _LibraryAttachments((attachment,), primary_documents=(attachment,))
    filing = _LibraryFilingBundle(attachments=attachments)
    backend, _ = _capability_backend(
        _CapabilityCompany(filings=(filing,)),
        filing,
        clock=clock,
    )

    with pytest.raises(expected_type) as captured:
        backend.acquire_attachment(_ACCESSION, attachment.document, expected_cik=_CIK)

    assert captured.value.operation == "acquire_attachment"
    assert captured.value.state is expected_state
    assert attachment.download_calls == 1


@pytest.mark.parametrize("stage", ["enumerate", "download"])
def test_acquire_attachment_maps_library_failures_at_each_fetch_stage(stage: str) -> None:
    if stage == "enumerate":
        filing = _LibraryFilingBundle(
            attachments=TransportError("secret attachment enumeration detail")
        )
        selected: _LibraryAttachment | None = None
    else:
        selected = _LibraryAttachment(
            sequence_number="1",
            description="Filing",
            document="filing.htm",
            document_type="10-Q",
            size=10,
            url="https://www.sec.gov/Archives/example/filing.htm",
            binary=False,
            payload=TransportError("secret download detail"),
        )
        filing = _LibraryFilingBundle(
            attachments=_LibraryAttachments((selected,), primary_documents=(selected,))
        )
    backend, _ = _capability_backend(
        _CapabilityCompany(filings=(filing,)),
        filing,
        clock=lambda: _NOW,
    )

    with pytest.raises(AdapterTransportError) as captured:
        backend.acquire_attachment(_ACCESSION, "filing.htm", expected_cik=_CIK)

    assert captured.value.operation == "acquire_attachment"
    assert "secret" not in str(captured.value)
    if selected is not None:
        assert selected.download_calls == 1
