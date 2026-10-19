"""Contrato común de las pasarelas de pago."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GatewayCharge:
    """Resultado normalizado de un cobro, independiente del proveedor.

    Que todas las pasarelas devuelvan esto es lo que permite añadir una nueva
    sin tocar `PaymentService`: el dominio no sabe de Stripe ni de Mercado Pago.
    """

    provider_payment_id: str
    status: str
    raw: dict[str, Any]

    @property
    def succeeded(self) -> bool:
        return self.status in {"succeeded", "approved"}


@runtime_checkable
class PaymentGatewayProtocol(Protocol):
    """Lo que el dominio espera de cualquier pasarela."""

    @property
    def enabled(self) -> bool:
        """`True` cuando hay credenciales y se habla con el proveedor real."""
        ...

    async def create_payment_intent(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference: str,
        customer_id: str,
        idempotency_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> GatewayCharge: ...

    async def refund(
        self,
        *,
        provider_payment_id: str,
        amount_minor: int | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> GatewayCharge: ...

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]: ...
