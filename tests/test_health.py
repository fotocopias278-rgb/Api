"""Sondas de salud y contrato de autenticación."""

from tests.conftest import AUTH_HEADERS


async def test_liveness_no_requiere_autenticacion(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_comprueba_la_base_de_datos(client):
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["database"] == "ok"
    # Sin credenciales configuradas los integradores externos van en modo simulado.
    assert body["checks"]["stripe"] == "simulated"
    assert body["checks"]["vision"] == "stub"


async def test_cada_respuesta_lleva_identificador_de_peticion(client):
    response = await client.get("/api/v1/health")
    assert response.headers["X-Request-ID"]


async def test_se_respeta_el_identificador_entrante(client):
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_sin_clave_de_api_se_rechaza(client):
    response = await client.get("/api/v1/payments")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_con_clave_invalida_se_rechaza(client):
    response = await client.get("/api/v1/payments", headers={"X-API-Key": "no-valida"})
    assert response.status_code == 401


async def test_con_clave_valida_se_acepta(client):
    response = await client.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert response.status_code == 200
