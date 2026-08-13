"""Typed, host-locked client for the hosted EdgarTools REST API."""

# Exception messages in this boundary are deliberately constructed at the call site so
# they can include safe schema field names while never incorporating response bodies or
# credentials.
# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import PureWindowsPath
from types import TracebackType
from typing import Final, Generic, NoReturn, Self, TypeVar, cast
from urllib.parse import unquote

import httpx
from pydantic import SecretStr

EDGAR_TOOLS_BASE_URL: Final = "https://api.edgar.tools/v1/"
EDGAR_TOOLS_ADAPTER_VERSION: Final = "1.0.0"
_EDGAR_TOOLS_HOST: Final = "api.edgar.tools"
_MAX_RESPONSE_BYTES: Final = 25_000_000
_MAX_ATTEMPTS: Final = 4
_MAX_PAGES: Final = 20
_MAX_ITEMS: Final = 2_000
_MAX_RETRY_AFTER_SECONDS: Final = 30.0
_MAX_FILENAME_LENGTH: Final = 255
_MAX_SEARCH_QUERY_LENGTH: Final = 200
_MAX_PAGE_SIZE: Final = 100
_MAX_DISCLOSURE_RESULTS: Final = 25
_ASCII_CONTROL_LIMIT: Final = 32
_ASCII_DELETE: Final = 127
_DOCUMENT_CONTENT_TYPES: Final = frozenset(
    {
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/xhtml+xml",
        "application/xml",
        "text/html",
        "text/plain",
        "text/xml",
    }
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderResponseMetadata:
    """Safe metadata captured for one provider response."""

    endpoint: str
    safe_params: tuple[tuple[str, str], ...]
    retrieved_at: datetime
    status_code: int
    content_type: str
    byte_length: int
    sha256: str
    rate_limit_limit: str | None
    rate_limit_remaining: str | None
    rate_limit_reset: str | None
    retry_after: str | None
    tier: str | None
    pagination: tuple[tuple[str, str], ...]
    adapter_version: str = EDGAR_TOOLS_ADAPTER_VERSION


@dataclass(frozen=True, slots=True)
class ProviderResponse(Generic[T]):
    """Typed value plus exact provider bytes and safe response metadata."""

    value: T
    raw_bytes: bytes
    metadata: ProviderResponseMetadata


@dataclass(frozen=True, slots=True)
class CompanySummary:
    """Typed company identity returned by EdgarTools."""

    cik: str
    name: str
    ticker: str | None
    entity_type: str | None
    fiscal_year_end: str | None


@dataclass(frozen=True, slots=True)
class FilingSummary:
    """Typed filing discovery row."""

    cik: str
    accession_number: str
    form: str
    filing_date: str
    acceptance_datetime: str | None
    description: str | None
    provider_reported_sec_url: str | None


@dataclass(frozen=True, slots=True)
class FilingPage:
    """One bounded page of company filings."""

    filings: tuple[FilingSummary, ...]
    page: int
    limit: int
    total: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class FilingCollection:
    """Bounded multi-page filing result with per-page response metadata."""

    filings: tuple[FilingSummary, ...]
    page_metadata: tuple[ProviderResponseMetadata, ...]


@dataclass(frozen=True, slots=True)
class FilingDetail:
    """Typed filing detail returned by EdgarTools."""

    cik: str
    accession_number: str
    filing_date: str
    ticker: str | None
    provider_reported_sec_url: str | None


@dataclass(frozen=True, slots=True)
class FilingDocument:
    """Typed document metadata within a filing."""

    filename: str
    document_type: str | None
    sequence: int | None
    size: int | None
    provider_reported_sec_url: str | None


class EdgarToolsError(RuntimeError):
    """Base error containing only safe request and response metadata."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        status_code: int | None = None,
        metadata: ProviderResponseMetadata | None = None,
    ) -> None:
        """Retain only request-safe context for callers and logs."""
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.metadata = metadata


class EdgarToolsUnsafeRequestError(EdgarToolsError):
    """An endpoint, host, path, or filename violated the source boundary."""


class EdgarToolsAuthenticationError(EdgarToolsError):
    """The provider rejected authentication."""


class EdgarToolsTierBlockedError(EdgarToolsError):
    """The authenticated account cannot access the requested capability."""


class EdgarToolsQuotaBlockedError(EdgarToolsError):
    """The account quota is exhausted."""


class EdgarToolsRateLimitError(EdgarToolsError):
    """The request remained rate-limited after bounded retry."""


class EdgarToolsNotFoundError(EdgarToolsError):
    """The requested provider resource does not exist or is not exposed."""


class EdgarToolsTransportError(EdgarToolsError):
    """A transient transport failure exhausted bounded retry."""


class EdgarToolsProviderUnavailableError(EdgarToolsError):
    """The provider remained unavailable after bounded retry."""


class EdgarToolsSchemaError(EdgarToolsError):
    """A successful response did not match the documented schema."""


class EdgarToolsContentError(EdgarToolsError):
    """A response had unsafe size, encoding, or content type."""


class EdgarToolsRedirectError(EdgarToolsError):
    """The provider attempted a redirect, which is never followed."""


def validate_provider_filename(filename: str) -> str:
    """Validate a provider filename as one inert path segment.

    Args:
        filename: Provider-returned filing document name.

    Returns:
        The unchanged safe filename.

    Raises:
        EdgarToolsUnsafeRequestError: If the filename can escape a retention root.
    """
    decoded = unquote(filename)
    windows_path = PureWindowsPath(decoded)
    unsafe = (
        not filename
        or len(filename) > _MAX_FILENAME_LENGTH
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or windows_path.drive != ""
        or len(windows_path.parts) != 1
        or any(
            ord(character) < _ASCII_CONTROL_LIMIT or ord(character) == _ASCII_DELETE
            for character in decoded
        )
    )
    if unsafe:
        raise EdgarToolsUnsafeRequestError(
            "EdgarTools document filename is unsafe",
            endpoint="/filings/{cik}/{accession}/{filename}",
        )
    return filename


def _required_mapping(value: object, *, field: str, endpoint: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise EdgarToolsSchemaError(
            f"EdgarTools response field is not an object: {field}",
            endpoint=endpoint,
        )
    return cast("Mapping[str, object]", value)


def _required_list(value: object, *, field: str, endpoint: str) -> list[object]:
    if not isinstance(value, list):
        raise EdgarToolsSchemaError(
            f"EdgarTools response field is not an array: {field}",
            endpoint=endpoint,
        )
    return cast("list[object]", value)


def _required_string(value: object, *, field: str, endpoint: str) -> str:
    if not isinstance(value, str) or not value:
        raise EdgarToolsSchemaError(
            f"EdgarTools response field is not a non-empty string: {field}",
            endpoint=endpoint,
        )
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_int(value: object, *, field: str, endpoint: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise EdgarToolsSchemaError(
            f"EdgarTools response field is not a bounded integer: {field}",
            endpoint=endpoint,
        )
    return value


def _safe_parameter_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if not isinstance(value, str | int):
        raise TypeError("EdgarTools safe parameter has an unsupported type")
    return str(value)


def _tier_from_payload(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("your_tier", "user_tier", "tier", "required_tier", "tier_required"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for candidate in value.values():
            found = _tier_from_payload(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value[:3]:
            found = _tier_from_payload(candidate)
            if found is not None:
                return found
    return None


class EdgarToolsClient:
    """Synchronous, bounded client for documented hosted EdgarTools endpoints."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        api_key: SecretStr,
        base_url: str = EDGAR_TOOLS_BASE_URL,
        timeout: httpx.Timeout | None = None,
        max_attempts: int = 3,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_retry_after_seconds: float = _MAX_RETRY_AFTER_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure the single hosted-API transport boundary."""
        if base_url != EDGAR_TOOLS_BASE_URL:
            raise EdgarToolsUnsafeRequestError(
                "EdgarTools base URL is not the canonical hosted API",
                endpoint="/",
            )
        if (
            not 1 <= max_attempts <= _MAX_ATTEMPTS
            or not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES
            or not math.isfinite(max_retry_after_seconds)
            or not 0 <= max_retry_after_seconds <= _MAX_RETRY_AFTER_SECONDS
        ):
            raise ValueError("EdgarTools retry and response bounds are invalid")
        secret_value = api_key.get_secret_value()
        if not secret_value:
            raise ValueError("EdgarTools API key is empty")
        self._max_attempts = max_attempts
        self._max_response_bytes = max_response_bytes
        self._max_retry_after_seconds = max_retry_after_seconds
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {secret_value}"},
            timeout=timeout or httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> Self:
        """Return the open client."""
        return self

    def __exit__(
        self,
        _: type[BaseException] | None,
        __: BaseException | None,
        ___: TracebackType | None,
    ) -> None:
        """Close the client when leaving a context manager."""
        self.close()

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        decoded = unquote(endpoint)
        parts = decoded.replace("\\", "/").split("/")
        if (
            not endpoint
            or endpoint.startswith(("/", "\\"))
            or "://" in decoded
            or decoded.startswith("//")
            or "?" in decoded
            or "#" in decoded
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise EdgarToolsUnsafeRequestError(
                "EdgarTools endpoint is not a safe relative API path",
                endpoint="/",
            )
        return endpoint

    @staticmethod
    def _validate_built_request(request: httpx.Request, *, endpoint: str) -> None:
        url = request.url
        if (
            url.scheme != "https"
            or url.host != _EDGAR_TOOLS_HOST
            or bool(url.username)
            or bool(url.password)
            or not url.path.startswith("/v1/")
        ):
            raise EdgarToolsUnsafeRequestError(
                "EdgarTools request escaped the canonical API boundary",
                endpoint=endpoint,
            )

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                seconds = float(Decimal(retry_after))
            except (InvalidOperation, ValueError):
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
                    seconds = float((parsed - self._now()).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    seconds = -1.0
            if math.isfinite(seconds) and seconds >= 0:
                return min(seconds, self._max_retry_after_seconds)
        return min(0.5 * float(2**attempt), 4.0, self._max_retry_after_seconds)

    def _read_bounded(self, response: httpx.Response, *, endpoint: str) -> bytes:
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                raise EdgarToolsContentError(
                    "EdgarTools Content-Length is invalid",
                    endpoint=endpoint,
                    status_code=response.status_code,
                ) from None
            if declared_bytes < 0 or declared_bytes > self._max_response_bytes:
                raise EdgarToolsContentError(
                    "EdgarTools response exceeds the configured byte bound",
                    endpoint=endpoint,
                    status_code=response.status_code,
                )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self._max_response_bytes:
                raise EdgarToolsContentError(
                    "EdgarTools response exceeds the configured byte bound",
                    endpoint=endpoint,
                    status_code=response.status_code,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_json(
        content: bytes,
        *,
        endpoint: str,
        status_code: int,
    ) -> object:
        try:
            text = content.decode("utf-8")
            return json.loads(text, parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EdgarToolsSchemaError(
                "EdgarTools response is not valid UTF-8 JSON",
                endpoint=endpoint,
                status_code=status_code,
            ) from None

    @staticmethod
    def _pagination_metadata(payload: object) -> tuple[tuple[str, str], ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("pagination"), dict):
            return ()
        pagination = cast("dict[str, object]", payload["pagination"])
        safe = []
        for key, value in sorted(pagination.items()):
            if isinstance(value, str | int | bool) and not isinstance(value, float):
                safe.append((str(key), _safe_parameter_value(value)))
        return tuple(safe)

    def _metadata(  # noqa: PLR0913
        self,
        *,
        endpoint: str,
        params: tuple[tuple[str, str], ...],
        response: httpx.Response,
        content: bytes,
        content_type: str,
        payload: object | None,
    ) -> ProviderResponseMetadata:
        return ProviderResponseMetadata(
            endpoint=f"/{endpoint}",
            safe_params=params,
            retrieved_at=self._now(),
            status_code=response.status_code,
            content_type=content_type,
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            rate_limit_limit=response.headers.get("x-ratelimit-limit"),
            rate_limit_remaining=response.headers.get("x-ratelimit-remaining"),
            rate_limit_reset=response.headers.get("x-ratelimit-reset"),
            retry_after=response.headers.get("retry-after"),
            tier=_tier_from_payload(payload),
            pagination=self._pagination_metadata(payload),
        )

    @staticmethod
    def _raise_status_error(
        *,
        endpoint: str,
        status_code: int,
        payload: object | None,
        metadata: ProviderResponseMetadata,
    ) -> NoReturn:
        if status_code == httpx.codes.UNAUTHORIZED:
            error_type: type[EdgarToolsError] = EdgarToolsAuthenticationError
            message = "EdgarTools authentication failed"
        elif status_code == httpx.codes.FORBIDDEN:
            error_type = EdgarToolsTierBlockedError
            message = "EdgarTools capability is tier-blocked"
        elif status_code == httpx.codes.NOT_FOUND:
            error_type = EdgarToolsNotFoundError
            message = "EdgarTools resource was not found"
        elif status_code == httpx.codes.TOO_MANY_REQUESTS:
            rendered = json.dumps(payload, default=str).lower() if payload is not None else ""
            if "quota" in rendered:
                error_type = EdgarToolsQuotaBlockedError
                message = "EdgarTools quota is exhausted"
            else:
                error_type = EdgarToolsRateLimitError
                message = "EdgarTools rate limit remained active"
        elif status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            error_type = EdgarToolsProviderUnavailableError
            message = "EdgarTools provider is unavailable"
        else:
            error_type = EdgarToolsSchemaError
            message = "EdgarTools returned an unsupported HTTP status"
        raise error_type(
            message,
            endpoint=f"/{endpoint}",
            status_code=status_code,
            metadata=metadata,
        )

    def _request(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str | int | bool | None] | None = None,
        expected_content_types: frozenset[str],
        expect_json: bool,
    ) -> ProviderResponse[object]:
        safe_endpoint = self._validate_endpoint(endpoint)
        safe_params = tuple(
            sorted(
                (key, _safe_parameter_value(value))
                for key, value in (params or {}).items()
                if value is not None
            )
        )
        for attempt in range(self._max_attempts):
            request = self._client.build_request("GET", safe_endpoint, params=dict(safe_params))
            self._validate_built_request(request, endpoint=safe_endpoint)
            try:
                response = self._client.send(request, stream=True)
            except httpx.TransportError:
                if attempt + 1 < self._max_attempts:
                    self._sleep(self._retry_delay(attempt, None))
                    continue
                raise EdgarToolsTransportError(
                    "EdgarTools transport failed after bounded retry",
                    endpoint=f"/{safe_endpoint}",
                ) from None
            try:
                if response.is_redirect:
                    raise EdgarToolsRedirectError(
                        "EdgarTools redirect was rejected",
                        endpoint=f"/{safe_endpoint}",
                        status_code=response.status_code,
                    )
                content = self._read_bounded(response, endpoint=f"/{safe_endpoint}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                payload = (
                    self._decode_json(
                        content,
                        endpoint=f"/{safe_endpoint}",
                        status_code=response.status_code,
                    )
                    if "json" in content_type
                    else None
                )
                metadata = self._metadata(
                    endpoint=safe_endpoint,
                    params=safe_params,
                    response=response,
                    content=content,
                    content_type=content_type,
                    payload=payload,
                )
                retryable = response.status_code == httpx.codes.TOO_MANY_REQUESTS or (
                    response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
                )
                if retryable and attempt + 1 < self._max_attempts:
                    self._sleep(self._retry_delay(attempt, response.headers.get("retry-after")))
                    continue
                if not response.is_success:
                    self._raise_status_error(
                        endpoint=safe_endpoint,
                        status_code=response.status_code,
                        payload=payload,
                        metadata=metadata,
                    )
                if content_type not in expected_content_types:
                    raise EdgarToolsContentError(
                        "EdgarTools response content type is not allowed for this endpoint",
                        endpoint=f"/{safe_endpoint}",
                        status_code=response.status_code,
                        metadata=metadata,
                    )
                if expect_json and payload is None:
                    raise EdgarToolsSchemaError(
                        "EdgarTools JSON endpoint returned no JSON object",
                        endpoint=f"/{safe_endpoint}",
                        status_code=response.status_code,
                        metadata=metadata,
                    )
                return ProviderResponse(
                    value=payload if expect_json else content,
                    raw_bytes=content,
                    metadata=metadata,
                )
            finally:
                response.close()
        raise AssertionError("bounded EdgarTools request loop did not terminate")

    def _request_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str | int | bool | None] | None = None,
    ) -> ProviderResponse[Mapping[str, object]]:
        response = self._request(
            endpoint,
            params=params,
            expected_content_types=frozenset({"application/json"}),
            expect_json=True,
        )
        payload = _required_mapping(response.value, field="root", endpoint=f"/{endpoint}")
        return ProviderResponse(payload, response.raw_bytes, response.metadata)

    def health(self) -> ProviderResponse[Mapping[str, object]]:
        """Read the unauthenticated-compatible provider health resource."""
        return self._request_json("health")

    def search_companies(self, query: str) -> ProviderResponse[tuple[CompanySummary, ...]]:
        """Search companies by public name, ticker, or CIK."""
        endpoint = "search"
        if not query.strip() or len(query) > _MAX_SEARCH_QUERY_LENGTH:
            raise ValueError("EdgarTools company search query is invalid")
        response = self._request_json(endpoint, params={"q": query.strip()})
        entities = _required_list(
            response.value.get("entities"), field="entities", endpoint=endpoint
        )
        companies = tuple(
            CompanySummary(
                cik=_required_string(
                    _required_mapping(row, field="entities[]", endpoint=endpoint).get("cik"),
                    field="cik",
                    endpoint=endpoint,
                ),
                name=_required_string(
                    _required_mapping(row, field="entities[]", endpoint=endpoint).get("name"),
                    field="name",
                    endpoint=endpoint,
                ),
                ticker=_optional_string(
                    _required_mapping(row, field="entities[]", endpoint=endpoint).get("ticker")
                ),
                entity_type=_optional_string(
                    _required_mapping(row, field="entities[]", endpoint=endpoint).get("entity_type")
                ),
                fiscal_year_end=_optional_string(
                    _required_mapping(row, field="entities[]", endpoint=endpoint).get(
                        "fiscal_year_end"
                    )
                ),
            )
            for row in entities
        )
        return ProviderResponse(companies, response.raw_bytes, response.metadata)

    def get_company(self, identifier: str) -> ProviderResponse[CompanySummary]:
        """Retrieve one company profile."""
        endpoint = f"companies/{identifier}"
        response = self._request_json(endpoint)
        entity = _required_mapping(response.value.get("entity"), field="entity", endpoint=endpoint)
        company = _required_mapping(
            entity.get("company"), field="entity.company", endpoint=endpoint
        )
        ticker = _optional_string(company.get("ticker"))
        if ticker is None and isinstance(entity.get("tickers"), list):
            for row in cast("list[object]", entity["tickers"]):
                if isinstance(row, str) and row:
                    ticker = row
                    break
                if isinstance(row, dict):
                    ticker = _optional_string(row.get("ticker"))
                    if ticker is not None:
                        break
        value = CompanySummary(
            cik=_required_string(company.get("cik"), field="cik", endpoint=endpoint),
            name=_required_string(company.get("name"), field="name", endpoint=endpoint),
            ticker=ticker,
            entity_type=_optional_string(company.get("entity_type")),
            fiscal_year_end=_optional_string(company.get("fiscal_year_end")),
        )
        return ProviderResponse(value, response.raw_bytes, response.metadata)

    def list_company_filings(
        self,
        cik: str,
        *,
        page: int = 1,
        limit: int = 100,
        form_type: str | None = None,
    ) -> ProviderResponse[FilingPage]:
        """Retrieve one bounded filings page."""
        if not 1 <= page <= _MAX_PAGES or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError("EdgarTools filings pagination is out of bounds")
        endpoint = f"companies/{cik}/filings"
        response = self._request_json(
            endpoint,
            params={"page": page, "limit": limit, "form_type": form_type},
        )
        rows = _required_list(response.value.get("filings"), field="filings", endpoint=endpoint)
        pagination = _required_mapping(
            response.value.get("pagination"), field="pagination", endpoint=endpoint
        )
        filings = []
        for row in rows:
            item = _required_mapping(row, field="filings[]", endpoint=endpoint)
            filings.append(
                FilingSummary(
                    cik=cik,
                    accession_number=_required_string(
                        item.get("accession_number"),
                        field="accession_number",
                        endpoint=endpoint,
                    ),
                    form=_required_string(item.get("form"), field="form", endpoint=endpoint),
                    filing_date=_required_string(
                        item.get("filing_date"), field="filing_date", endpoint=endpoint
                    ),
                    acceptance_datetime=_optional_string(item.get("acceptance_datetime")),
                    description=_optional_string(item.get("description")),
                    provider_reported_sec_url=_optional_string(item.get("sec_url")),
                )
            )
        has_more = pagination.get("has_more")
        if not isinstance(has_more, bool):
            raise EdgarToolsSchemaError(
                "EdgarTools pagination.has_more is not boolean",
                endpoint=f"/{endpoint}",
            )
        value = FilingPage(
            filings=tuple(filings),
            page=_bounded_int(
                pagination.get("page"), field="pagination.page", endpoint=endpoint, minimum=1
            ),
            limit=_bounded_int(
                pagination.get("limit"), field="pagination.limit", endpoint=endpoint, minimum=1
            ),
            total=_bounded_int(
                pagination.get("total"), field="pagination.total", endpoint=endpoint
            ),
            has_more=has_more,
        )
        return ProviderResponse(value, response.raw_bytes, response.metadata)

    def list_all_company_filings(
        self,
        cik: str,
        *,
        form_type: str | None = None,
        max_pages: int = _MAX_PAGES,
        max_items: int = _MAX_ITEMS,
    ) -> FilingCollection:
        """Follow filings pagination within explicit page and item bounds."""
        if not 1 <= max_pages <= _MAX_PAGES or not 1 <= max_items <= _MAX_ITEMS:
            raise ValueError("EdgarTools aggregate pagination bounds are invalid")
        filings: list[FilingSummary] = []
        metadata: list[ProviderResponseMetadata] = []
        seen_pages: set[tuple[str, ...]] = set()
        for page_number in range(1, max_pages + 1):
            page = self.list_company_filings(
                cik,
                page=page_number,
                limit=min(_MAX_PAGE_SIZE, max_items - len(filings)),
                form_type=form_type,
            )
            signature = tuple(item.accession_number for item in page.value.filings)
            if signature in seen_pages or (page.value.has_more and not signature):
                raise EdgarToolsSchemaError(
                    "EdgarTools filings pagination repeated or stalled",
                    endpoint=f"/companies/{cik}/filings",
                )
            seen_pages.add(signature)
            filings.extend(page.value.filings)
            metadata.append(page.metadata)
            if len(filings) > max_items:
                raise EdgarToolsSchemaError(
                    "EdgarTools filings exceeded the configured item bound",
                    endpoint=f"/companies/{cik}/filings",
                )
            if not page.value.has_more:
                return FilingCollection(tuple(filings), tuple(metadata))
            if len(filings) == max_items:
                raise EdgarToolsSchemaError(
                    "EdgarTools filings require more than the configured item bound",
                    endpoint=f"/companies/{cik}/filings",
                )
        raise EdgarToolsSchemaError(
            "EdgarTools filings require more than the configured page bound",
            endpoint=f"/companies/{cik}/filings",
        )

    def get_filing(self, cik: str, accession_number: str) -> ProviderResponse[FilingDetail]:
        """Retrieve one filing detail record."""
        endpoint = f"filings/{cik}/{accession_number}"
        response = self._request_json(endpoint)
        filing = _required_mapping(response.value.get("filing"), field="filing", endpoint=endpoint)
        value = FilingDetail(
            cik=_required_string(filing.get("cik"), field="cik", endpoint=endpoint),
            accession_number=_required_string(
                filing.get("accession_number"), field="accession_number", endpoint=endpoint
            ),
            filing_date=_required_string(
                filing.get("filing_date"), field="filing_date", endpoint=endpoint
            ),
            ticker=_optional_string(filing.get("ticker")),
            provider_reported_sec_url=_optional_string(filing.get("sec_url")),
        )
        return ProviderResponse(value, response.raw_bytes, response.metadata)

    def list_filing_documents(
        self,
        cik: str,
        accession_number: str,
    ) -> ProviderResponse[tuple[FilingDocument, ...]]:
        """List all documents exposed for one filing."""
        endpoint = f"filings/{cik}/{accession_number}/documents"
        response = self._request_json(endpoint)
        rows = _required_list(response.value.get("documents"), field="documents", endpoint=endpoint)
        documents = []
        for row in rows:
            item = _required_mapping(row, field="documents[]", endpoint=endpoint)
            filename = validate_provider_filename(
                _required_string(item.get("filename"), field="filename", endpoint=endpoint)
            )
            sequence_value = item.get("sequence")
            sequence = (
                _bounded_int(sequence_value, field="sequence", endpoint=endpoint)
                if sequence_value is not None
                else None
            )
            size_value = item.get("size")
            size = (
                _bounded_int(size_value, field="size", endpoint=endpoint)
                if size_value is not None
                else None
            )
            documents.append(
                FilingDocument(
                    filename=filename,
                    document_type=_optional_string(item.get("type")),
                    sequence=sequence,
                    size=size,
                    provider_reported_sec_url=_optional_string(item.get("sec_url")),
                )
            )
        return ProviderResponse(tuple(documents), response.raw_bytes, response.metadata)

    def fetch_filing_document(
        self,
        cik: str,
        accession_number: str,
        filename: str,
    ) -> ProviderResponse[bytes]:
        """Fetch exact document bytes through the hosted provider only."""
        safe_filename = validate_provider_filename(filename)
        endpoint = f"filings/{cik}/{accession_number}/{safe_filename}"
        response = self._request(
            endpoint,
            expected_content_types=_DOCUMENT_CONTENT_TYPES,
            expect_json=False,
        )
        return ProviderResponse(
            cast("bytes", response.value), response.raw_bytes, response.metadata
        )

    def get_structured_financials(
        self,
        identifier: str,
        *,
        period_type: str = "quarterly",
        include_segments: bool = True,
    ) -> ProviderResponse[Mapping[str, object]]:
        """Retrieve provider XBRL-derived financial statements without float conversion."""
        if period_type not in {"quarterly", "annual"}:
            raise ValueError("EdgarTools financial period_type is invalid")
        return self._request_json(
            f"companies/{identifier}/financial-data",
            params={"period_type": period_type, "include_segments": include_segments},
        )

    def search_full_text(
        self,
        *,
        query: str,
        ciks: str,
        forms: str,
        size: int = 10,
    ) -> ProviderResponse[Mapping[str, object]]:
        """Run bounded provider full-text discovery search."""
        if not query or not 1 <= size <= _MAX_PAGE_SIZE:
            raise ValueError("EdgarTools full-text search bounds are invalid")
        return self._request_json(
            "search/full-text",
            params={"q": query, "ciks": ciks, "forms": forms, "size": size},
        )

    def search_disclosures(
        self,
        identifier: str,
        *,
        query: str,
        max_results: int = 10,
    ) -> ProviderResponse[Mapping[str, object]]:
        """Run bounded narrative disclosure discovery search."""
        if not query or not 1 <= max_results <= _MAX_DISCLOSURE_RESULTS:
            raise ValueError("EdgarTools disclosure search bounds are invalid")
        return self._request_json(
            f"companies/{identifier}/disclosures/search",
            params={"q": query, "max_results": max_results},
        )
