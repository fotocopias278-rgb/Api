"""Trabajos de conciliación: conteos, discrepancias y aislamiento."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.db.models import Payment, Receipt, ReconciliationJob, WebhookEvent
from app.schemas.enums import (
    DiscrepancyKind,
    PaymentMethod,
    PaymentStatus,
    ReceiptStatus,
    ReconciliationStatus,
)
from tests.conftest import AUTH_HEADERS, AUTH_HEADERS_OTRO, MERCHANT_ID


def _payment(reference: str, provider_id: str) -> Payment:
    return Payment(
        merchant_id=MERCHANT_ID,
        reference=reference,
        customer_id="cliente-reconciliation",
        amount_minor=5_000,
        currency="EUR",
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCEEDED,
        provider="stripe",
        provider_payment_id=provider_id,
    )


def _receipt(payment_id, reconciliation_status: ReconciliationStatus) -> Receipt:
    return Receipt(
        merchant_id=MERCHANT_ID,
        payment_id=payment_id,
        filename="recibo.png",
        content_type="image/png",
        size_bytes=100,
        content_sha256="a" * 64,
        status=ReceiptStatus.EXTRACTED,
        reconciliation_status=reconciliation_status,
    )


async def test_crea_job_conteos_y_discrepancias_por_tipo(client, session):
    matched = _payment("pay_job_matched", "provider-matched")
    mismatched = _payment("pay_job_mismatch", "provider-mismatch")
    duplicate = _payment("pay_job_duplicate", "provider-duplicate")
    pending = _payment("pay_job_pending", "provider-pending")
    session.add_all([matched, mismatched, duplicate, pending])
    await session.flush()
    session.add_all(
        [
            _receipt(matched.id, ReconciliationStatus.MATCHED),
            _receipt(mismatched.id, ReconciliationStatus.MISMATCHED),
            _receipt(duplicate.id, ReconciliationStatus.DUPLICATE),
            _receipt(None, ReconciliationStatus.PENDING),
        ]
    )
    session.add(
        WebhookEvent(
            provider="stripe",
            event_id="evt_gateway_orphan",
            event_type="payment_intent.succeeded",
            payload={
                "data": {
                    "object": {
                        "id": "unknown-provider-payment",
                        "metadata": {"merchant_id": str(MERCHANT_ID)},
                    }
                }
            },
        )
    )
    await session.commit()

    start = datetime.now(UTC) - timedelta(days=1)
    end = datetime.now(UTC) + timedelta(days=1)
    response = await client.post(
        "/api/v1/reconciliation/jobs",
        json={"period_start": start.isoformat(), "period_end": end.isoformat()},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["total_payments"] == 4
    assert body["matched_count"] == 1
    assert body["mismatched_count"] == 1
    assert body["pending_count"] == 1
    assert body["duplicate_count"] == 1
    assert body["unmatched_receipts_count"] == 1
    assert {row["kind"] for row in body["discrepancies"]} == {
        DiscrepancyKind.MISSING_RECEIPT,
        DiscrepancyKind.AMOUNT_MISMATCH,
        DiscrepancyKind.DUPLICATE_RECEIPT,
        DiscrepancyKind.ORPHAN_RECEIPT,
        DiscrepancyKind.GATEWAY_WITHOUT_PAYMENT,
    }


async def test_job_se_aísla_por_comercio(client, session):
    start = datetime.now(UTC) - timedelta(days=1)
    end = datetime.now(UTC) + timedelta(days=1)
    created = await client.post(
        "/api/v1/reconciliation/jobs",
        json={"period_start": start.isoformat(), "period_end": end.isoformat()},
        headers=AUTH_HEADERS,
    )
    job_id = created.json()["id"]

    own = await client.get(
        f"/api/v1/reconciliation/jobs/{job_id}", headers=AUTH_HEADERS
    )
    foreign = await client.get(
        f"/api/v1/reconciliation/jobs/{job_id}", headers=AUTH_HEADERS_OTRO
    )
    assert own.status_code == 200
    assert foreign.status_code == 404
    assert await session.get(ReconciliationJob, UUID(job_id))


async def test_rechaza_periodo_invalido(client):
    instant = datetime.now(UTC).isoformat()
    response = await client.post(
        "/api/v1/reconciliation/jobs",
        json={"period_start": instant, "period_end": instant},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422
