"""Sondas de salud para orquestadores."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep, SettingsDep
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["salud"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health(settings: SettingsDep) -> HealthResponse:
    """El proceso está vivo. No comprueba dependencias."""
    return HealthResponse(
        status="ok", version=__version__, environment=settings.environment
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness")
async def readiness(
    session: SessionDep, settings: SettingsDep, response: Response
) -> HealthResponse:
    """Comprueba las dependencias externas antes de recibir tráfico."""
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("health.database_unavailable", extra={"error": str(exc)})
        checks["database"] = "error"

    checks["stripe"] = "configured" if settings.stripe_api_key else "simulated"
    checks["vision"] = "configured" if settings.anthropic_api_key else "stub"

    healthy = checks["database"] == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )
