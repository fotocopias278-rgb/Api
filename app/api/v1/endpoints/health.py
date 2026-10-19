"""Sondas de salud para orquestadores."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep, SettingsDep
from app.schemas.common import HealthResponse
from app.services.vision import StubVisionProvider, get_vision_provider

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
    request: Request, session: SessionDep, settings: SettingsDep, response: Response
) -> HealthResponse:
    """Comprueba las dependencias externas antes de recibir tráfico."""
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("health.database_unavailable", extra={"error": str(exc)})
        checks["database"] = "error"

    redis = getattr(request.app.state, "redis", None)
    if not settings.rate_limit_enabled:
        checks["redis"] = "disabled"
    elif redis is None:
        checks["redis"] = "unavailable"
    else:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            logger.warning("health.redis_unavailable", extra={"error": str(exc)})
            checks["redis"] = "error"

    checks["stripe"] = "configured" if settings.stripe_api_key else "simulated"
    # Se pregunta a la fábrica en vez de mirar una sola credencial: así la
    # sonda refleja el proveedor que de verdad se usaría —Gemini incluido— y no
    # vuelve a quedarse obsoleta cada vez que se añada uno nuevo.
    proveedor = get_vision_provider(settings)
    checks["vision"] = (
        "stub" if isinstance(proveedor, StubVisionProvider) else "configured"
    )

    healthy = checks["database"] == "ok" and checks["redis"] in {"ok", "disabled"}
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )
