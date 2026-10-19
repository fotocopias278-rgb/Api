"""Adaptador de Mercado Pago.

Misma filosofía que el de Stripe: sin credenciales opera en modo simulado
—para poder desarrollar y testear el flujo entero— dejando siempre traza en el
log de que no se ha movido dinero real.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import GatewayError, WebhookVerificationError
from app.services.gateway import GatewayCharge

logger = logging.getLogger(__name__)

API_BASE = "https://api.mercadopago.com"

class MercadoPagoGateway:
    def __init__(
        self, config: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = config or default_settings
        self._token = self._settings.mercadopago_access_token
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def _a_decimal(self, amount_minor: int, currency: str) -> float:
        """Convierte a la unidad que espera la API del proveedor."""
        from app.core.money import to_decimal

        return float(to_decimal(amount_minor, currency))

    def _http(self) -> tuple[httpx.AsyncClient, bool]:
        """Devuelve el cliente y si hay que cerrarlo al terminar."""
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(
            base_url=API_BASE,
            timeout=self._settings.mercadopago_timeout_seconds,
            headers={"Authorization": f"Bearer {self._token}"},
        ), True

    async def create_payment_intent(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference: str,
        customer_id: str,
        idempotency_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> GatewayCharge:
        if not self.enabled:
            return self._simulate(reference, "pending")

        cuerpo = {
            "transaction_amount": self._a_decimal(amount_minor, currency),
            "currency_id": currency.upper(),
            "description": f"Pago {reference}",
            "external_reference": reference,
            "metadata": {"customer_id": customer_id, **(metadata or {})},
        }
        cliente, propio = self._http()
        try:
            respuesta = await cliente.post(
                "/v1/payments",
                json=cuerpo,
                # Mercado Pago deduplica por esta cabecera: un reintento de red
                # no vuelve a cobrar.
                headers={
                    "Authorization": "Bearer " + self._token,
                    "X-Idempotency-Key": idempotency_key or reference,
                },
            )
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.exception("mercadopago.create_failed", extra={"reference": reference})
            raise GatewayError(
                f"Mercado Pago rechazó el cobro ({exc.response.status_code}).",
                details={"reference": reference},
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayError("No se pudo contactar con Mercado Pago.") from exc
        finally:
            if propio:
                await cliente.aclose()

        datos = respuesta.json()
        return GatewayCharge(
            provider_payment_id=str(datos.get("id")),
            status=str(datos.get("status", "pending")),
            raw=datos,
        )

    async def refund(
        self,
        *,
        provider_payment_id: str,
        amount_minor: int | None = None,
        currency: str = "EUR",
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> GatewayCharge:
        if not self.enabled:
            return self._simulate(provider_payment_id, "refunded")

        cliente, propio = self._http()
        try:
            respuesta = await cliente.post(
                f"/v1/payments/{provider_payment_id}/refunds",
                json={}
                if amount_minor is None
                else {"amount": self._a_decimal(amount_minor, currency)},
                headers={
                    "Authorization": "Bearer " + self._token,
                    "X-Idempotency-Key": idempotency_key or f"refund-{provider_payment_id}"
                },
            )
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GatewayError(
                f"Mercado Pago rechazó la devolución ({exc.response.status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayError("No se pudo contactar con Mercado Pago.") from exc
        finally:
            if propio:
                await cliente.aclose()

        return GatewayCharge(
            provider_payment_id=provider_payment_id, status="refunded", raw=respuesta.json()
        )

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        """Valida la cabecera `x-signature` (`ts=...,v1=...`).

        Sin secreto configurado se rechaza: aceptar avisos sin firmar dejaría
        que cualquiera marcase pagos como aprobados.
        """
        import json

        secreto = self._settings.mercadopago_webhook_secret
        if not secreto:
            raise GatewayError("MERCADOPAGO_WEBHOOK_SECRET no está configurado.")
        if not signature:
            raise WebhookVerificationError("Falta la cabecera x-signature.")

        partes = dict(
            trozo.split("=", 1) for trozo in signature.split(",") if "=" in trozo
        )
        ts, recibida = partes.get("ts"), partes.get("v1")
        if not ts or not recibida:
            raise WebhookVerificationError("Cabecera x-signature malformada.")

        esperada = hmac.new(
            secreto.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(recibida, esperada):
            raise WebhookVerificationError("Firma del webhook inválida.")

        # La tolerancia temporal evita que un aviso capturado se reproduzca.
        tolerancia = self._settings.webhook_signature_tolerance_seconds
        try:
            timestamp = int(ts)
        except ValueError as exc:
            raise WebhookVerificationError(
                "Marca de tiempo inválida en x-signature."
            ) from exc
        if abs(time.time() - timestamp) > tolerancia:
            raise WebhookVerificationError("La firma del webhook ha caducado.")

        try:
            return json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("Cuerpo del webhook malformado.") from exc

    def _simulate(self, reference: str, status: str) -> GatewayCharge:
        simulado = f"mp_sim_{uuid.uuid4().hex[:24]}"
        logger.warning(
            "mercadopago.simulated_call",
            extra={"reference": reference, "simulated_id": simulado, "status": status},
        )
        return GatewayCharge(
            provider_payment_id=simulado,
            status=status,
            raw={"simulated": True, "status": status},
        )


mercadopago_gateway = MercadoPagoGateway()
