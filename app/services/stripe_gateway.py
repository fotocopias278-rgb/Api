"""Adaptador de la pasarela de pago (Stripe).

Sin `STRIPE_API_KEY` el adaptador entra en modo simulado: permite desarrollar y
testear el flujo completo sin credenciales, dejando siempre traza en el log de
que no se ha movido dinero real.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import stripe

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import GatewayError, WebhookVerificationError
from app.services.gateway import GatewayCharge

logger = logging.getLogger(__name__)


class StripeGateway:
    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings
        self._api_key = self._settings.stripe_api_key

    @property
    def enabled(self) -> bool:
        """`True` cuando hay credenciales y se habla con Stripe de verdad."""
        return bool(self._api_key)

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
            return self._simulate(reference, "requires_payment_method")

        try:
            intent = await stripe.PaymentIntent.create_async(
                api_key=self._api_key,
                amount=amount_minor,
                currency=currency.lower(),
                metadata={
                    "reference": reference,
                    "customer_id": customer_id,
                    **(metadata or {}),
                },
                # Stripe deduplica por esta clave: un reintento de red no cobra dos veces.
                idempotency_key=idempotency_key or reference,
            )
        except stripe.StripeError as exc:
            logger.exception(
                "stripe.create_payment_intent_failed", extra={"reference": reference}
            )
            raise GatewayError(
                f"Stripe rechazó la creación del cobro: {exc.user_message or exc}",
                details={"reference": reference},
            ) from exc

        return GatewayCharge(
            provider_payment_id=intent.id,
            status=intent.status,
            raw=intent.to_dict(),
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

        try:
            refund = await stripe.Refund.create_async(
                api_key=self._api_key,
                payment_intent=provider_payment_id,
                **({"amount": amount_minor} if amount_minor is not None else {}),
                # Stripe solo admite este conjunto cerrado de motivos.
                **(
                    {"reason": reason}
                    if reason in {"duplicate", "fraudulent", "requested_by_customer"}
                    else {}
                ),
                idempotency_key=idempotency_key,
            )
        except stripe.StripeError as exc:
            logger.exception("stripe.refund_failed", extra={"payment_intent": provider_payment_id})
            raise GatewayError(f"Stripe rechazó la devolución: {exc.user_message or exc}") from exc

        return GatewayCharge(
            provider_payment_id=provider_payment_id,
            status=refund.status or "pending",
            raw=refund.to_dict(),
        )

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        """Valida la firma del webhook y devuelve el evento.

        Sin secreto configurado se rechaza el evento: aceptar cuerpos sin firmar
        permitiría a cualquiera marcar pagos como cobrados.
        """
        secret = self._settings.stripe_webhook_secret
        if not secret:
            raise GatewayError("STRIPE_WEBHOOK_SECRET no está configurado.")
        if not signature:
            raise WebhookVerificationError("Falta la cabecera Stripe-Signature.")

        try:
            event = stripe.Webhook.construct_event(payload, signature, secret)
        except ValueError as exc:
            raise WebhookVerificationError("Cuerpo del webhook malformado.") from exc
        except stripe.SignatureVerificationError as exc:
            raise WebhookVerificationError("Firma del webhook inválida.") from exc

        # `to_dict()` devuelve diccionarios anidados y serializables a JSON.
        return event.to_dict()

    def _simulate(self, reference: str, status: str) -> GatewayCharge:
        simulated_id = f"pi_sim_{uuid.uuid4().hex[:24]}"
        logger.warning(
            "stripe.simulated_call",
            extra={"reference": reference, "simulated_id": simulated_id, "status": status},
        )
        return GatewayCharge(
            provider_payment_id=simulated_id,
            status=status,
            raw={"simulated": True, "status": status},
        )


stripe_gateway = StripeGateway()
