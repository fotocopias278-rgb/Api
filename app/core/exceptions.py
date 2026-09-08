"""Excepciones de dominio y su traducción a respuestas HTTP."""

from __future__ import annotations

from typing import Any


class FinAIError(Exception):
    """Error base del dominio."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        # Algunos errores llevan cabeceras propias (p. ej. `Retry-After`).
        self.headers = headers or {}


class NotFoundError(FinAIError):
    status_code = 404
    code = "not_found"


class ConflictError(FinAIError):
    """Colisión de estado: recurso duplicado o transición inválida."""

    status_code = 409
    code = "conflict"


class ValidationError(FinAIError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(FinAIError):
    status_code = 401
    code = "unauthorized"


class PaymentRejectedError(FinAIError):
    """El motor de fraude ha bloqueado la operación."""

    status_code = 402
    code = "payment_rejected"


class GatewayError(FinAIError):
    """Fallo al comunicarse con la pasarela de pago."""

    status_code = 502
    code = "gateway_error"


class RateLimitedError(FinAIError):
    """Se ha superado el límite de peticiones."""

    status_code = 429
    code = "rate_limited"


class WebhookVerificationError(FinAIError):
    """Firma del webhook ausente o inválida."""

    status_code = 400
    code = "invalid_signature"


class VisionExtractionError(FinAIError):
    """El modelo multimodal no pudo extraer datos del comprobante."""

    status_code = 422
    code = "vision_extraction_failed"
