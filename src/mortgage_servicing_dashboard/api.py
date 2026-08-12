"""FastAPI read surface and server-rendered Stage A dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from mortgage_servicing_dashboard.database import create_database_engine, default_database_url
from mortgage_servicing_dashboard.repository import IntelligenceRepository, seed_stage_a


class HealthResponse(BaseModel):
    """Deterministic readiness response."""

    model_config = ConfigDict(extra="forbid")
    status: str
    database: str
    companies: int
    latest_period_end: str | None
    model_calls_enabled: bool


def _asset_root() -> Path:
    return Path(__file__).resolve().parent


def create_app(  # noqa: C901, PLR0915
    *,
    database_url: str | None = None,
    repository: IntelligenceRepository | None = None,
) -> FastAPI:
    """Create an app with dependency-injectable persistence."""
    active_repository = repository
    if active_repository is None:
        engine = create_database_engine(database_url or default_database_url())
        seed_stage_a(engine)
        active_repository = IntelligenceRepository(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="Public Mortgage Servicing Intelligence",
        version="0.1.0",
        description="Source-traceable public financial intelligence for two U.S. servicers.",
        lifespan=lifespan,
    )
    app.state.repository = active_repository
    templates = Jinja2Templates(directory=_asset_root() / "templates")
    app.mount("/static", StaticFiles(directory=_asset_root() / "static"), name="static")

    def get_repository(request: Request) -> IntelligenceRepository:
        return cast("IntelligenceRepository", request.app.state.repository)

    repository_dependency = Annotated[IntelligenceRepository, Depends(get_repository)]

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health(repo: repository_dependency) -> HealthResponse:
        with repo.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        latest = repo.latest_period_end()
        return HealthResponse(
            status="ready",
            database="reachable",
            companies=len(repo.companies()),
            latest_period_end=latest.isoformat() if latest is not None else None,
            model_calls_enabled=False,
        )

    @app.get("/api/v1/companies", tags=["catalog"])
    def companies(repo: repository_dependency) -> list[dict[str, object]]:
        return repo.companies()

    @app.get("/api/v1/companies/{company_id}", tags=["catalog"])
    def company(company_id: str, repo: repository_dependency) -> dict[str, object]:
        result = next((item for item in repo.companies() if item["id"] == company_id), None)
        if result is None:
            raise HTTPException(status_code=404, detail="company not found")
        return result

    @app.get("/api/v1/metrics", tags=["catalog"])
    def metrics(repo: repository_dependency) -> list[dict[str, object]]:
        return repo.metrics()

    @app.get("/api/v1/observations", tags=["observations"])
    def observations(  # noqa: PLR0913, PLR0917
        repo: repository_dependency,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        as_of: datetime | None = None,
        include_missing: bool = True,  # noqa: FBT001, FBT002
    ) -> list[dict[str, object]]:
        return [
            row.as_dict()
            for row in repo.observations(
                as_of=as_of,
                company_id=company_id,
                metric_id=metric_id,
                period_end=period_end,
                include_missing=include_missing,
            )
        ]

    @app.get("/api/v1/observations/{observation_id}", tags=["observations"])
    def observation(observation_id: str, repo: repository_dependency) -> dict[str, object]:
        row = repo.observation(observation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="observation not found")
        return row.as_dict()

    @app.get("/api/v1/comparisons", tags=["analysis"])
    def comparison(
        repo: repository_dependency,
        metric_id: str,
        period_end: date,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        result = repo.compare(metric_id=metric_id, period_end=period_end, as_of=as_of)
        if result is None:
            raise HTTPException(status_code=404, detail="comparison inputs not found")
        return result.as_dict()

    @app.get("/api/v1/coverage", tags=["quality"])
    def coverage(
        repo: repository_dependency,
        as_of: datetime | None = None,
    ) -> list[dict[str, object]]:
        return repo.coverage(as_of=as_of)

    @app.get("/api/v1/evidence/{evidence_id}", tags=["evidence"])
    def evidence(evidence_id: str, repo: repository_dependency) -> dict[str, object]:
        result = repo.evidence(evidence_id)
        if result is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        return result

    @app.get("/api/v1/earnings-events", tags=["events"])
    def earnings_events(repo: repository_dependency) -> list[dict[str, object]]:
        return repo.earnings_events()

    @app.get("/api/v1/pipeline/freshness", tags=["quality"])
    def freshness(repo: repository_dependency) -> dict[str, object]:
        return repo.freshness()

    def render(  # noqa: PLR0913
        request: Request,
        repo: IntelligenceRepository,
        *,
        page: str,
        title: str,
        company_id: str | None = None,
        metric_id: str | None = None,
    ) -> HTMLResponse:
        latest = repo.latest_period_end()
        rows = repo.observations(
            company_id=company_id,
            metric_id=metric_id,
            include_missing=page in {"quality", "methodology"},
        )
        featured_ids = {
            "total_servicing_upb",
            "servicing_for_others_upb",
            "owned_msr_upb",
            "servicing_revenue",
            "servicing_pretax_income",
            "weighted_average_servicing_fee_bps",
        }
        featured = [row for row in rows if row.metric_id in featured_ids]
        comparisons = []
        if latest is not None:
            for selected_metric in ("total_servicing_upb", "servicing_revenue"):
                result = repo.compare(metric_id=selected_metric, period_end=latest)
                if result is not None:
                    comparisons.append(result)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": page,
                "title": title,
                "companies": repo.companies(),
                "metrics": repo.metrics(),
                "rows": rows,
                "featured": featured,
                "coverage": repo.coverage(),
                "comparisons": comparisons,
                "latest": latest.isoformat() if latest is not None else None,
                "model_calls_enabled": False,
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def overview(request: Request, repo: repository_dependency) -> HTMLResponse:
        return render(request, repo, page="overview", title="Portfolio overview")

    @app.get("/companies/{company_id}", response_class=HTMLResponse, include_in_schema=False)
    def company_page(
        company_id: str,
        request: Request,
        repo: repository_dependency,
    ) -> HTMLResponse:
        if company_id not in {str(item["id"]) for item in repo.companies()}:
            raise HTTPException(status_code=404, detail="company not found")
        return render(
            request,
            repo,
            page="company",
            title=f"{company_id.upper()} disclosure profile",
            company_id=company_id,
        )

    @app.get("/metrics/{metric_id}", response_class=HTMLResponse, include_in_schema=False)
    def metric_page(
        metric_id: str,
        request: Request,
        repo: repository_dependency,
    ) -> HTMLResponse:
        if metric_id not in {str(item["id"]) for item in repo.metrics()}:
            raise HTTPException(status_code=404, detail="metric not found")
        return render(
            request,
            repo,
            page="metric",
            title=metric_id.replace("_", " ").title(),
            metric_id=metric_id,
        )

    @app.get("/comparison", response_class=HTMLResponse, include_in_schema=False)
    def comparison_page(request: Request, repo: repository_dependency) -> HTMLResponse:
        return render(request, repo, page="comparison", title="Pairwise comparability")

    @app.get("/data-quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(request: Request, repo: repository_dependency) -> HTMLResponse:
        return render(request, repo, page="quality", title="Disclosure coverage & quality")

    @app.get("/methodology", response_class=HTMLResponse, include_in_schema=False)
    def methodology_page(request: Request, repo: repository_dependency) -> HTMLResponse:
        return render(request, repo, page="methodology", title="Methods & metric catalog")

    return app
