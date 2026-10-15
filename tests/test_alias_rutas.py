"""Alias del contrato maestro.

Comparten manejador con la ruta original, así que no pueden divergir: lo que se
comprueba aquí es que existen, que se comportan igual y —importante— que no
sirven para saltarse el cupo del limitador.
"""

from tests.conftest import AUTH_HEADERS
from tests.test_receipts_api import PNG_1PX

PAGO = {"customer_id": "cli", "amount_minor": 2_500, "currency": "EUR"}
RIESGO = {"customer_id": "cli", "amount_minor": 900_000, "currency": "EUR", "card_bin": "666666"}


async def test_receipts_validate_es_alias_de_receipts(client):
    respuesta = await client.post(
        "/api/v1/receipts/validate",
        files={"file": ("r.png", PNG_1PX, "image/png")},
        headers=AUTH_HEADERS,
    )
    assert respuesta.status_code == 202
    assert respuesta.json()["status"] == "pending"


async def test_fraud_score_es_alias_de_fraud_evaluate(client):
    por_alias = await client.post("/api/v1/fraud/score", json=RIESGO, headers=AUTH_HEADERS)
    por_original = await client.post("/api/v1/fraud/evaluate", json=RIESGO, headers=AUTH_HEADERS)

    assert por_alias.status_code == 200
    assert por_alias.json() == por_original.json()


async def test_los_alias_tambien_exigen_autenticacion(client):
    assert (await client.post("/api/v1/fraud/score", json=RIESGO)).status_code == 401
    assert (
        await client.post(
            "/api/v1/receipts/validate", files={"file": ("r.png", PNG_1PX, "image/png")}
        )
    ).status_code == 401


def test_el_alias_cuesta_lo_mismo_que_la_ruta_original():
    """Si costara menos, bastaría con llamar por el otro nombre."""
    from app.main import cost_for

    assert cost_for("POST", "/api/v1/receipts/validate") == cost_for("POST", "/api/v1/receipts")
    assert cost_for("POST", "/api/v1/fraud/score") == cost_for("POST", "/api/v1/fraud/evaluate")


async def test_el_alias_comparte_cupo_con_la_ruta_original(client_limitado):
    """Agotar el cupo por una ruta lo agota para su alias."""
    for _ in range(5):
        await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)

    respuesta = await client_limitado.post(
        "/api/v1/fraud/score", json=RIESGO, headers=AUTH_HEADERS
    )
    assert respuesta.status_code == 429
