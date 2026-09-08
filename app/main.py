"""Punto de entrada de la API FinAI Gateway."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import FinAIError, RateLimitedError
from app.core.logging import configure_logging, request_id_ctx
from app.core.security import API_KEY_HEADER
from app.db.session import engine
from app.schemas.common import ErrorResponse
from app.services.rate_limit import RateLimiter, identity_from

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Rutas sin limitación: las sondas las consulta el orquestador constantemente y
# limitarlas provocaría reinicios en cascada justo cuando hay carga.
RATE_LIMIT_EXEMPT = ("/api/v1/health", "/docs", "/redoc", "/openapi.json")

# Coste en fichas por endpoint. Un cobro o una extracción con IA valen mucho más
# que una consulta, así que vacían el cubo antes.
ROUTE_COSTS: tuple[tuple[str, str, int], ...] = (
    ("POST", "/api/v1/receipts", 10),
    ("POST", "/api/v1/payments", 5),
    ("POST", "/api/v1/fraud/evaluate", 2),
)


def cost_for(method: str, path: str) -> int:
    for route_method, prefix, cost in ROUTE_COSTS:
        # Se compara el prefijo exacto para no cobrar de más a las subrutas
        # (p. ej. `/payments/{ref}/refund` no es una creación de pago).
        if method == route_method and path.rstrip("/") == prefix:
            return cost
    return 1

DESCRIPTION = """
API de validación de pagos, detección de fraude en tiempo real y conciliación
automatizada de comprobantes bancarios mediante visión computacional.

**Autenticación:** todas las rutas salvo `/health` y los webhooks requieren la
cabecera `X-API-Key`. El webhook de Stripe se autentica con su firma HMAC.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y parada ordenada del proceso."""
    settings = get_settings()
    configure_logging(settings.log_level)

    problems = settings.validate_runtime()
    if problems and settings.is_production:
        # En producción una configuración incompleta es un fallo de despliegue,
        # no algo que deba descubrirse con el primer pago rechazado.
        raise RuntimeError("Configuración inválida: " + " | ".join(problems))
    for problem in problems:
        logger.warning("config.incomplete", extra={"detail": problem})

    redis = redis_from_url(settings.redis_url)
    app.state.redis = redis
    app.state.rate_limiter = RateLimiter(redis, settings)

    logger.info(
        "app.startup",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "rate_limit_enabled": settings.rate_limit_enabled,
        },
    )
    yield
    await redis.aclose()
    await engine.dispose()
    logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación (factory: permite instanciarla en tests)."""
    config = settings or get_settings()

    app = FastAPI(
        title=config.project_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.include_router(api_router, prefix=config.api_v1_prefix)
    _register_middleware(app)
    _register_exception_handlers(app)
    return app


def _register_middleware(app: FastAPI) -> None:
    # El orden importa: en Starlette el último middleware registrado es el más
    # externo. Se registra primero el limitador para que `request_context` lo
    # envuelva y toda respuesta 429 lleve ya su identificador de petición.
    @app.middleware("http")
    async def rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Aplica el cubo de fichas antes de que la petición llegue a la ruta."""
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
        path = request.url.path

        if limiter is None or not limiter.enabled or path.startswith(RATE_LIMIT_EXEMPT):
            return await call_next(request)

        identity = identity_from(
            request.headers.get(API_KEY_HEADER),
            request.client.host if request.client else None,
        )
        result = await limiter.check(identity, cost=cost_for(request.method, path))

        if not result.allowed:
            logger.warning(
                "rate_limit.exceeded",
                extra={"identity": identity, "path": path, "method": request.method},
            )
            # Se devuelve la respuesta en vez de lanzar la excepción: los
            # manejadores de FastAPI viven *por dentro* de la pila de
            # middlewares, así que lo que se lance aquí los sobrevuela y acaba
            # en un 500. La excepción sigue siendo la fuente del contrato
            # (estado, código y mensaje), solo cambia cómo se entrega.
            error = RateLimitedError(
                "Se ha superado el límite de peticiones.",
                details={"limit_per_minute": result.limit},
                headers={
                    "Retry-After": result.retry_after_header,
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            return _error_response(
                error.status_code, error.code, error.message, request, error.details, error.headers
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Asigna un identificador a cada petición y mide su duración."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http.request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(elapsed_ms, 2),
                "request_id": request_id,
            },
        )
        return response


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request: Request,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        details=details or {},
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(mode="json"), headers=headers
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FinAIError)
    async def handle_domain_error(request: Request, exc: FinAIError) -> JSONResponse:
        """Traduce los errores de dominio al contrato de error de la API."""
        if exc.status_code >= 500:
            logger.exception("error.domain", extra={"code": exc.code})
        return _error_response(
            exc.status_code, exc.code, exc.message, request, exc.details, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            422,
            "validation_error",
            "La petición no supera la validación.",
            request,
            {"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Red de seguridad: nunca se filtra una traza al cliente."""
        logger.exception("error.unhandled", extra={"path": request.url.path})
        return _error_response(500, "internal_error", "Error interno del servidor.", request)


app = create_app()
