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


async def test_la_sonda_reconoce_gemini_como_proveedor(client, monkeypatch):
    """Con solo GOOGLE_API_KEY la sonda debe decir `configured`, no `stub`.

    Antes miraba únicamente `anthropic_api_key`, así que un despliegue entero
    sobre Gemini se reportaba como si no tuviera IA configurada.
    """
    from app.core.config import get_settings

    ajustes = get_settings()
    monkeypatch.setattr(ajustes, "google_api_key", "clave-de-google")
    monkeypatch.setattr(ajustes, "anthropic_api_key", None)
    monkeypatch.setattr(ajustes, "vision_provider", "auto")

    respuesta = await client.get("/api/v1/health/ready")
    assert respuesta.json()["checks"]["vision"] == "configured"


async def test_la_sonda_reconoce_anthropic_como_proveedor(client, monkeypatch):
    from app.core.config import get_settings

    ajustes = get_settings()
    monkeypatch.setattr(ajustes, "anthropic_api_key", "clave-de-anthropic")
    monkeypatch.setattr(ajustes, "google_api_key", None)
    monkeypatch.setattr(ajustes, "vision_provider", "auto")

    respuesta = await client.get("/api/v1/health/ready")
    assert respuesta.json()["checks"]["vision"] == "configured"


async def test_sin_ninguna_credencial_la_sonda_dice_stub(client):
    respuesta = await client.get("/api/v1/health/ready")
    assert respuesta.json()["checks"]["vision"] == "stub"
