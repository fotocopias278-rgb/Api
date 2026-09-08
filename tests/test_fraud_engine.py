"""Reglas del motor de riesgo."""

from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.schemas.enums import RiskDecision
from app.services.fraud import FraudContext, FraudEngine

MEDIODIA = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine():
    return FraudEngine(get_settings())


def contexto(**overrides) -> FraudContext:
    base = {
        "amount_minor": 1_000,
        "currency": "EUR",
        "customer_id": "cliente-1",
        "occurred_at": MEDIODIA,
        "is_new_customer": False,
    }
    return FraudContext(**{**base, **overrides})


def test_operacion_limpia_se_aprueba(engine):
    result = engine.evaluate(contexto())
    assert result.score == 0
    assert result.decision is RiskDecision.APPROVE
    assert result.signals == []


def test_importe_elevado_dispara_senal(engine):
    result = engine.evaluate(contexto(amount_minor=600_000))
    assert "HIGH_AMOUNT" in {s.code for s in result.signals}


def test_bin_bloqueado_fuerza_rechazo(engine):
    result = engine.evaluate(contexto(card_bin="666666"))
    assert result.decision is RiskDecision.REJECT
    assert "BLOCKED_BIN" in {s.code for s in result.signals}


def test_pais_de_tarjeta_distinto_al_de_la_ip(engine):
    result = engine.evaluate(contexto(card_country="ES", ip_country="RU"))
    assert "GEO_MISMATCH" in {s.code for s in result.signals}


def test_mismo_pais_no_dispara_senal(engine):
    result = engine.evaluate(contexto(card_country="ES", ip_country="es"))
    assert "GEO_MISMATCH" not in {s.code for s in result.signals}


def test_pais_ausente_no_dispara_senal(engine):
    result = engine.evaluate(contexto(card_country="ES", ip_country=None))
    assert "GEO_MISMATCH" not in {s.code for s in result.signals}


def test_correo_desechable(engine):
    result = engine.evaluate(contexto(email="quien@mailinator.com"))
    assert "DISPOSABLE_EMAIL" in {s.code for s in result.signals}


def test_desviacion_requiere_historial(engine):
    """Sin media previa no se puede hablar de desviación."""
    sin_media = engine.evaluate(contexto(amount_minor=100_000, average_amount_minor=None))
    assert "AMOUNT_DEVIATION" not in {s.code for s in sin_media.signals}

    con_media = engine.evaluate(contexto(amount_minor=100_000, average_amount_minor=1_000))
    assert "AMOUNT_DEVIATION" in {s.code for s in con_media.signals}


def test_velocidad_y_prueba_de_tarjetas(engine):
    result = engine.evaluate(contexto(payments_last_hour=6, distinct_cards_last_day=4))
    codes = {s.code for s in result.signals}
    assert {"VELOCITY", "CARD_TESTING"} <= codes


def test_madrugada(engine):
    result = engine.evaluate(contexto(occurred_at=datetime(2026, 9, 1, 3, 30, tzinfo=UTC)))
    assert "ODD_HOUR" in {s.code for s in result.signals}


def test_la_puntuacion_se_satura_en_cien(engine):
    result = engine.evaluate(
        contexto(
            amount_minor=900_000,
            card_bin="666666",
            card_country="ES",
            ip_country="RU",
            email="x@yopmail.com",
            payments_last_hour=99,
            distinct_cards_last_day=99,
            failed_payments_last_day=99,
            is_new_customer=True,
            occurred_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        )
    )
    assert result.score == 100
    assert result.decision is RiskDecision.REJECT


def test_umbral_de_revision(engine):
    """Una puntuación intermedia retiene el pago en lugar de rechazarlo."""
    settings = get_settings()
    result = engine.evaluate(contexto(amount_minor=600_000, payments_last_hour=6))
    assert settings.fraud_review_threshold <= result.score < settings.fraud_reject_threshold
    assert result.decision is RiskDecision.REVIEW
