"""Motor de puntuación de riesgo basado en reglas ponderadas.

El motor es una función pura sobre un `FraudContext`: no consulta la base de
datos ni la red. Quien lo invoca es responsable de reunir el contexto (ver
`build_context`), lo que hace el scoring reproducible y trivial de testear.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.schemas.enums import RiskDecision
from app.schemas.fraud import FraudEvaluationResponse, FraudSignalRead

# Dominios de correo desechable frecuentes en el fraude con tarjeta.
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
        "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
        "getnada.com", "dispostable.com",
    }
)


@dataclass(frozen=True, slots=True)
class FraudContext:
    """Todo lo que el motor necesita saber sobre una operación."""

    amount_minor: int
    currency: str
    customer_id: str
    email: str | None = None
    card_bin: str | None = None
    card_country: str | None = None
    ip_country: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Agregados históricos del cliente.
    is_new_customer: bool = True
    payments_last_hour: int = 0
    failed_payments_last_day: int = 0
    distinct_cards_last_day: int = 0
    average_amount_minor: int | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    code: str
    description: str
    weight: int
    predicate: Callable[[FraudContext, Settings], bool]


def _high_amount(ctx: FraudContext, cfg: Settings) -> bool:
    return ctx.amount_minor >= cfg.fraud_high_amount_minor


def _amount_deviation(ctx: FraudContext, _: Settings) -> bool:
    # Solo tiene sentido con historial: un cliente sin media no "se desvía".
    if not ctx.average_amount_minor:
        return False
    return ctx.amount_minor >= ctx.average_amount_minor * 5


def _velocity(ctx: FraudContext, cfg: Settings) -> bool:
    return ctx.payments_last_hour >= cfg.fraud_velocity_max_per_hour


def _card_testing(ctx: FraudContext, cfg: Settings) -> bool:
    return ctx.distinct_cards_last_day >= cfg.fraud_max_distinct_cards_per_day


def _failed_attempts(ctx: FraudContext, _: Settings) -> bool:
    return ctx.failed_payments_last_day >= 3


def _geo_mismatch(ctx: FraudContext, _: Settings) -> bool:
    if not ctx.card_country or not ctx.ip_country:
        return False
    return ctx.card_country.upper() != ctx.ip_country.upper()


def _disposable_email(ctx: FraudContext, _: Settings) -> bool:
    if not ctx.email or "@" not in ctx.email:
        return False
    return ctx.email.rsplit("@", 1)[1].lower() in DISPOSABLE_EMAIL_DOMAINS


def _blocked_bin(ctx: FraudContext, cfg: Settings) -> bool:
    if not ctx.card_bin:
        return False
    return any(ctx.card_bin.startswith(blocked) for blocked in cfg.fraud_blocked_bins)


def _new_customer_high_amount(ctx: FraudContext, cfg: Settings) -> bool:
    return ctx.is_new_customer and ctx.amount_minor >= cfg.fraud_high_amount_minor // 2


def _odd_hour(ctx: FraudContext, _: Settings) -> bool:
    return 0 <= ctx.occurred_at.astimezone(UTC).hour < 5


RULES: tuple[Rule, ...] = (
    # Peso máximo: una tarjeta en lista negra debe rechazarse por sí sola,
    # sin depender de que otras señales la acompañen hasta el umbral.
    Rule("BLOCKED_BIN", "El BIN de la tarjeta está en lista negra.", 100, _blocked_bin),
    Rule("CARD_TESTING", "Varias tarjetas distintas en 24 h.", 30, _card_testing),
    Rule("HIGH_AMOUNT", "Importe por encima del umbral configurado.", 25, _high_amount),
    Rule("VELOCITY", "Demasiados pagos en la última hora.", 25, _velocity),
    Rule("AMOUNT_DEVIATION", "Importe muy superior a la media del cliente.", 20, _amount_deviation),
    Rule("FAILED_ATTEMPTS", "Varios pagos fallidos en 24 h.", 20, _failed_attempts),
    Rule("GEO_MISMATCH", "País de la tarjeta distinto al de la IP.", 20, _geo_mismatch),
    Rule("DISPOSABLE_EMAIL", "Correo de dominio desechable.", 15, _disposable_email),
    Rule(
        "NEW_CUSTOMER_HIGH_AMOUNT",
        "Cliente nuevo con importe elevado.",
        15,
        _new_customer_high_amount,
    ),
    Rule("ODD_HOUR", "Operación en horario de madrugada (UTC).", 5, _odd_hour),
)


class FraudEngine:
    """Aplica las reglas y traduce la puntuación en una decisión."""

    VERSION = "rules-v1"

    def __init__(self, config: Settings | None = None, rules: Iterable[Rule] | None = None) -> None:
        self._settings = config or default_settings
        self._rules = tuple(rules) if rules is not None else RULES

    def evaluate(self, ctx: FraudContext) -> FraudEvaluationResponse:
        triggered = [rule for rule in self._rules if rule.predicate(ctx, self._settings)]
        # La puntuación se satura en 100: sumar más señales no hace la decisión
        # "más rechazable", y un techo estable mantiene los umbrales legibles.
        score = min(100, sum(rule.weight for rule in triggered))

        return FraudEvaluationResponse(
            score=score,
            decision=self._decide(score),
            engine_version=self.VERSION,
            signals=[
                FraudSignalRead(code=r.code, description=r.description, weight=r.weight)
                for r in triggered
            ],
        )

    def _decide(self, score: int) -> RiskDecision:
        if score >= self._settings.fraud_reject_threshold:
            return RiskDecision.REJECT
        if score >= self._settings.fraud_review_threshold:
            return RiskDecision.REVIEW
        return RiskDecision.APPROVE


fraud_engine = FraudEngine()
