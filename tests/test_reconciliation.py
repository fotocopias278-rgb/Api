"""Conciliación entre comprobante y pago."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Payment
from app.schemas.enums import ReconciliationStatus
from app.schemas.vision import DocumentType, ReceiptExtraction
from app.services.reconciliation import reconcile

CREADO = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def payment() -> Payment:
    return Payment(
        reference="pay_test",
        customer_id="cliente-1",
        amount_minor=123_456,
        currency="EUR",
        created_at=CREADO,
    )


def extraccion(**overrides) -> ReceiptExtraction:
    base = {
        "document_type": DocumentType.TRANSFER_RECEIPT,
        "is_legible": True,
        "bank_name": "Banco de Prueba",
        "operation_number": "OP-001",
        "amount": "1234.56",
        "currency": "EUR",
        "transaction_date": "2026-09-01T12:30:00Z",
        "sender_name": "Ana Pérez",
        "sender_account": "ES12 **** 3456",
        "receiver_name": "FinAI",
        "receiver_account": "ES99 **** 0001",
        "concept": "Pedido 42",
        "tampering_indicators": [],
        "confidence": 0.95,
    }
    return ReceiptExtraction(**{**base, **overrides})


def test_comprobante_coincidente(payment):
    result = reconcile(extraccion(), payment)
    assert result.status is ReconciliationStatus.MATCHED


def test_ilegible_no_se_concilia(payment):
    result = reconcile(extraccion(is_legible=False), payment)
    assert result.status is ReconciliationStatus.UNREADABLE


def test_numero_de_operacion_reutilizado(payment):
    result = reconcile(extraccion(), payment, duplicate_operation=True)
    assert result.status is ReconciliationStatus.DUPLICATE
    assert "OP-001" in result.notes[0]


def test_importe_distinto(payment):
    result = reconcile(extraccion(amount="1000.00"), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert any("Importe distinto" in nota for nota in result.notes)


def test_diferencia_dentro_de_la_tolerancia(payment):
    """Un céntimo arriba o abajo no invalida el comprobante."""
    result = reconcile(extraccion(amount="1235.00"), payment)
    assert result.status is ReconciliationStatus.MATCHED


def test_divisa_distinta(payment):
    result = reconcile(extraccion(currency="USD"), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert any("Divisa distinta" in nota for nota in result.notes)


def test_fecha_fuera_de_la_ventana(payment):
    lejos = (CREADO + timedelta(days=10)).isoformat()
    result = reconcile(extraccion(transaction_date=lejos), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert any("ventana" in nota for nota in result.notes)


def test_fecha_sin_zona_horaria_se_asume_utc(payment):
    result = reconcile(extraccion(transaction_date="2026-09-01T12:30:00"), payment)
    assert result.status is ReconciliationStatus.MATCHED


def test_fecha_ilegible(payment):
    result = reconcile(extraccion(transaction_date="el martes pasado"), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert any("fecha interpretable" in nota for nota in result.notes)


def test_importe_no_numerico(payment):
    result = reconcile(extraccion(amount="mil euros"), payment)
    assert result.status is ReconciliationStatus.MISMATCHED


def test_indicios_de_manipulacion_bloquean_la_conciliacion(payment):
    """Aunque los importes cuadren, un documento retocado no se da por bueno."""
    result = reconcile(extraccion(tampering_indicators=["tipografía inconsistente"]), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert any("manipulación" in nota for nota in result.notes)


def test_campos_ausentes(payment):
    result = reconcile(extraccion(amount=None, currency=None, transaction_date=None), payment)
    assert result.status is ReconciliationStatus.MISMATCHED
    assert len(result.notes) == 3
