"""Ciclo de vida de un pago a través de la API."""

import pytest
from sqlalchemy import select

from app.db.models import Payment
from app.schemas.enums import PaymentStatus, RiskDecision
from tests.conftest import AUTH_HEADERS


def cuerpo(**overrides) -> dict:
    base = {
        "customer_id": "cliente-1",
        "amount_minor": 2_500,
        "currency": "EUR",
        "method": "card",
    }
    return {**base, **overrides}


async def crear(client, **overrides):
    return await client.post("/api/v1/payments", json=cuerpo(**overrides), headers=AUTH_HEADERS)


async def test_pago_de_bajo_riesgo_se_envia_a_la_pasarela(client):
    response = await crear(client)
    assert response.status_code == 201

    body = response.json()
    assert body["reference"].startswith("pay_")
    assert body["risk_decision"] == RiskDecision.APPROVE
    # No se fija una puntuación exacta: alguna regla depende de la hora real
    # (ODD_HOUR). El scoring exacto se cubre en tests/test_fraud_engine.py.
    assert body["risk_score"] < 40
    # La pasarela simulada deja el cobro en curso, no liquidado.
    assert body["status"] == PaymentStatus.PROCESSING
    assert body["provider_payment_id"].startswith("pi_sim_")
    assert body["amount"] == "25.00"
    assert "id" not in body  # el UUID interno no se expone


async def test_pago_de_alto_riesgo_se_rechaza_sin_cobrar(client):
    response = await crear(client, card_bin="666666", amount_minor=900_000)
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == PaymentStatus.REJECTED
    assert body["risk_decision"] == RiskDecision.REJECT
    # Un pago rechazado nunca llega a la pasarela.
    assert body["provider_payment_id"] is None


async def test_pago_de_riesgo_medio_queda_en_revision(client):
    """Importe alto + velocidad: puntuación intermedia, se retiene."""
    for _ in range(6):
        await crear(client, customer_id="cliente-veloz")

    response = await crear(client, customer_id="cliente-veloz", amount_minor=600_000)
    body = response.json()
    assert body["status"] == PaymentStatus.UNDER_REVIEW
    assert body["risk_decision"] == RiskDecision.REVIEW
    assert body["provider_payment_id"] is None


async def test_la_clave_de_idempotencia_no_duplica_el_cobro(client):
    headers = {**AUTH_HEADERS, "Idempotency-Key": "clave-unica-1"}
    primera = await client.post("/api/v1/payments", json=cuerpo(), headers=headers)
    segunda = await client.post("/api/v1/payments", json=cuerpo(), headers=headers)

    assert primera.status_code == segunda.status_code == 201
    assert primera.json()["reference"] == segunda.json()["reference"]


async def test_reusar_la_clave_con_otro_importe_es_conflicto(client):
    headers = {**AUTH_HEADERS, "Idempotency-Key": "clave-unica-2"}
    await client.post("/api/v1/payments", json=cuerpo(), headers=headers)
    conflicto = await client.post(
        "/api/v1/payments", json=cuerpo(amount_minor=9_999), headers=headers
    )

    assert conflicto.status_code == 409
    assert conflicto.json()["code"] == "conflict"


@pytest.mark.parametrize(
    "invalido",
    [
        {"amount_minor": 0},
        {"amount_minor": -100},
        {"currency": "EURO"},
        {"customer_id": ""},
        {"card_bin": "no-numerico"},
    ],
)
async def test_peticiones_invalidas(client, invalido):
    response = await crear(client, **invalido)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_consultar_pago_incluye_el_historial_de_riesgo(client):
    reference = (await crear(client)).json()["reference"]

    response = await client.get(f"/api/v1/payments/{reference}", headers=AUTH_HEADERS)
    assert response.status_code == 200

    body = response.json()
    assert body["reference"] == reference
    assert len(body["assessments"]) == 1
    assert body["assessments"][0]["engine_version"] == "rules-v1"


async def test_consultar_pago_inexistente(client):
    response = await client.get("/api/v1/payments/pay_no_existe", headers=AUTH_HEADERS)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_listado_filtra_y_pagina(client):
    for _ in range(3):
        await crear(client, customer_id="cliente-a")
    await crear(client, customer_id="cliente-b")

    todos = await client.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert todos.json()["total"] == 4

    filtrado = await client.get(
        "/api/v1/payments", params={"customer_id": "cliente-a"}, headers=AUTH_HEADERS
    )
    assert filtrado.json()["total"] == 3

    pagina = await client.get(
        "/api/v1/payments", params={"limit": 2, "offset": 0}, headers=AUTH_HEADERS
    )
    assert len(pagina.json()["items"]) == 2
    assert pagina.json()["total"] == 4


async def test_filtrar_por_estado(client):
    await crear(client)
    await crear(client, card_bin="666666", amount_minor=900_000)

    response = await client.get(
        "/api/v1/payments", params={"status": "rejected"}, headers=AUTH_HEADERS
    )
    assert response.json()["total"] == 1


async def test_revision_manual_aprueba_el_cobro(client):
    for _ in range(6):
        await crear(client, customer_id="cliente-revisado")
    reference = (
        await crear(client, customer_id="cliente-revisado", amount_minor=600_000)
    ).json()["reference"]

    response = await client.post(
        f"/api/v1/payments/{reference}/review", json={"approve": True}, headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.PROCESSING


async def test_revision_manual_rechaza(client):
    for _ in range(6):
        await crear(client, customer_id="cliente-vetado")
    reference = (
        await crear(client, customer_id="cliente-vetado", amount_minor=600_000)
    ).json()["reference"]

    response = await client.post(
        f"/api/v1/payments/{reference}/review", json={"approve": False}, headers=AUTH_HEADERS
    )
    assert response.json()["status"] == PaymentStatus.REJECTED


async def test_no_se_puede_revisar_un_pago_que_no_esta_en_revision(client):
    reference = (await crear(client)).json()["reference"]
    response = await client.post(
        f"/api/v1/payments/{reference}/review", json={"approve": True}, headers=AUTH_HEADERS
    )
    assert response.status_code == 409


async def test_devolucion_solo_sobre_pagos_cobrados(client, session):
    reference = (await crear(client)).json()["reference"]

    # Recién creado está en curso, no liquidado.
    en_curso = await client.post(f"/api/v1/payments/{reference}/refund", headers=AUTH_HEADERS)
    assert en_curso.status_code == 409

    payment = await session.scalar(select(Payment).where(Payment.reference == reference))
    payment.status = PaymentStatus.SUCCEEDED
    await session.commit()

    devuelto = await client.post(
        f"/api/v1/payments/{reference}/refund",
        json={"reason": "requested_by_customer"},
        headers=AUTH_HEADERS,
    )
    assert devuelto.status_code == 200
    assert devuelto.json()["status"] == PaymentStatus.REFUNDED


async def test_no_se_devuelve_mas_de_lo_cobrado(client, session):
    reference = (await crear(client)).json()["reference"]
    payment = await session.scalar(select(Payment).where(Payment.reference == reference))
    payment.status = PaymentStatus.SUCCEEDED
    await session.commit()

    response = await client.post(
        f"/api/v1/payments/{reference}/refund",
        json={"amount_minor": 999_999},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409


async def test_evaluar_riesgo_sin_crear_pago(client):
    response = await client.post(
        "/api/v1/fraud/evaluate",
        json={
            "customer_id": "cliente-nuevo",
            "amount_minor": 900_000,
            "currency": "EUR",
            "card_bin": "666666",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["decision"] == RiskDecision.REJECT

    # No se ha persistido nada.
    listado = await client.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert listado.json()["total"] == 0
