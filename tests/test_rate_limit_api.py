"""Limitación de tasa vista desde HTTP."""

from tests.conftest import AUTH_HEADERS
from tests.conftest import AUTH_HEADERS_OTRO as OTRA_CLAVE


async def test_las_respuestas_informan_del_cupo(client_limitado):
    response = await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "4"


async def test_al_agotar_el_cupo_se_devuelve_429(client_limitado):
    for _ in range(5):
        assert (await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 200

    excedido = await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert excedido.status_code == 429

    cuerpo = excedido.json()
    assert cuerpo["code"] == "rate_limited"
    assert cuerpo["details"]["limit_per_minute"] == 60
    # Se indica al cliente cuándo puede volver, nunca "0".
    assert int(excedido.headers["Retry-After"]) >= 1


async def test_la_respuesta_429_conserva_el_identificador_de_peticion(client_limitado):
    """Prueba el orden de los middlewares: el contexto envuelve al limitador."""
    for _ in range(6):
        response = await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)

    assert response.status_code == 429
    assert response.headers["X-Request-ID"]
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


async def test_cada_clave_de_api_tiene_su_propio_cupo(client_limitado):
    for _ in range(5):
        await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert (await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 429

    # Otro integrador no sufre el consumo del primero.
    otra = await client_limitado.get("/api/v1/payments", headers=OTRA_CLAVE)
    assert otra.status_code == 200


async def test_las_sondas_de_salud_no_se_limitan(client_limitado):
    for _ in range(5):
        await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)
    assert (await client_limitado.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 429

    # El orquestador debe poder sondear incluso con el cupo agotado.
    for _ in range(10):
        assert (await client_limitado.get("/api/v1/health")).status_code == 200
    assert (await client_limitado.get("/api/v1/health/ready")).status_code == 200


async def test_crear_un_pago_cuesta_mas_que_consultarlo(client_limitado):
    """Un cobro vale 5 fichas: agota la ráfaga de una sola vez."""
    cuerpo = {"customer_id": "cli_1", "amount_minor": 2_500, "currency": "EUR"}
    primero = await client_limitado.post("/api/v1/payments", json=cuerpo, headers=AUTH_HEADERS)
    assert primero.status_code == 201
    assert primero.headers["X-RateLimit-Remaining"] == "0"

    segundo = await client_limitado.post("/api/v1/payments", json=cuerpo, headers=AUTH_HEADERS)
    assert segundo.status_code == 429


async def test_subir_comprobantes_cuesta_mas_que_el_cupo_de_rafaga(client_limitado):
    """La extracción con IA cuesta 10 fichas: con ráfaga 5 nunca cabe."""
    files = {"file": ("recibo.png", b"\x89PNG\r\n\x1a\n", "image/png")}
    response = await client_limitado.post("/api/v1/receipts", files=files, headers=AUTH_HEADERS)
    assert response.status_code == 429


async def test_el_trafico_sin_clave_tambien_se_limita(client_limitado):
    """Protege del sondeo de claves: se limita por IP antes de autenticar."""
    for _ in range(5):
        assert (await client_limitado.get("/api/v1/payments")).status_code == 401

    # A la sexta ya no llega ni a evaluar la clave.
    assert (await client_limitado.get("/api/v1/payments")).status_code == 429


async def test_el_webhook_tambien_se_limita(client_limitado):
    for _ in range(5):
        await client_limitado.post("/api/v1/webhooks/stripe", content=b"{}")

    excedido = await client_limitado.post("/api/v1/webhooks/stripe", content=b"{}")
    assert excedido.status_code == 429


async def test_la_sonda_informa_del_estado_de_redis(client_limitado):
    response = await client_limitado.get("/api/v1/health/ready")
    assert response.json()["checks"]["redis"] == "ok"
