"""Administración de comercios y claves."""

import uuid

from tests.conftest import ADMIN_HEADERS, AUTH_HEADERS


async def test_sin_clave_de_administracion_se_rechaza(client):
    respuesta = await client.post("/api/v1/admin/merchants", json={"name": "X"})
    assert respuesta.status_code == 401


async def test_la_clave_de_un_comercio_no_sirve_para_administrar(client):
    """Un cliente no puede darse de alta comercios ni emitir claves."""
    respuesta = await client.post(
        "/api/v1/admin/merchants", json={"name": "X"}, headers=AUTH_HEADERS
    )
    assert respuesta.status_code == 401


async def test_alta_de_comercio_y_emision_de_clave(client):
    alta = await client.post(
        "/api/v1/admin/merchants", json={"name": "Tienda Nueva"}, headers=ADMIN_HEADERS
    )
    assert alta.status_code == 201
    merchant_id = alta.json()["id"]

    emision = await client.post(
        f"/api/v1/admin/merchants/{merchant_id}/keys",
        json={"name": "produccion"},
        headers=ADMIN_HEADERS,
    )
    assert emision.status_code == 201

    cuerpo = emision.json()
    secreto = cuerpo["secret"]
    assert secreto.startswith("finai_")
    assert cuerpo["prefix"] == secreto[:12]

    # La clave recién emitida funciona de inmediato.
    uso = await client.get("/api/v1/payments", headers={"X-API-Key": secreto})
    assert uso.status_code == 200
    assert uso.json()["total"] == 0  # comercio nuevo, sin datos


async def test_el_secreto_solo_se_muestra_al_emitirlo(client):
    alta = await client.post(
        "/api/v1/admin/merchants", json={"name": "Tienda"}, headers=ADMIN_HEADERS
    )
    merchant_id = alta.json()["id"]
    await client.post(
        f"/api/v1/admin/merchants/{merchant_id}/keys", json={}, headers=ADMIN_HEADERS
    )

    listado = await client.get(
        f"/api/v1/admin/merchants/{merchant_id}/keys", headers=ADMIN_HEADERS
    )
    assert listado.status_code == 200
    claves = listado.json()
    assert len(claves) == 1
    # Solo el prefijo: el secreto no se puede recuperar, únicamente revocar.
    assert "secret" not in claves[0]
    assert claves[0]["prefix"]


async def test_revocar_una_clave(client):
    alta = await client.post(
        "/api/v1/admin/merchants", json={"name": "Tienda"}, headers=ADMIN_HEADERS
    )
    merchant_id = alta.json()["id"]
    emision = await client.post(
        f"/api/v1/admin/merchants/{merchant_id}/keys", json={}, headers=ADMIN_HEADERS
    )
    secreto = emision.json()["secret"]
    key_id = emision.json()["id"]

    assert (await client.get("/api/v1/payments", headers={"X-API-Key": secreto})).status_code == 200

    revocacion = await client.delete(
        f"/api/v1/admin/merchants/{merchant_id}/keys/{key_id}", headers=ADMIN_HEADERS
    )
    assert revocacion.status_code == 200
    assert revocacion.json()["revoked_at"] is not None

    assert (await client.get("/api/v1/payments", headers={"X-API-Key": secreto})).status_code == 401


async def test_no_se_revoca_dos_veces(client):
    alta = await client.post(
        "/api/v1/admin/merchants", json={"name": "T"}, headers=ADMIN_HEADERS
    )
    merchant_id = alta.json()["id"]
    key_id = (
        await client.post(
            f"/api/v1/admin/merchants/{merchant_id}/keys", json={}, headers=ADMIN_HEADERS
        )
    ).json()["id"]

    await client.delete(
        f"/api/v1/admin/merchants/{merchant_id}/keys/{key_id}", headers=ADMIN_HEADERS
    )
    segunda = await client.delete(
        f"/api/v1/admin/merchants/{merchant_id}/keys/{key_id}", headers=ADMIN_HEADERS
    )
    assert segunda.status_code == 409


async def test_no_se_revoca_la_clave_de_otro_comercio(client):
    """El identificador de clave no basta: tiene que ser del comercio indicado."""
    primera = await client.post(
        "/api/v1/admin/merchants", json={"name": "A"}, headers=ADMIN_HEADERS
    )
    segunda = await client.post(
        "/api/v1/admin/merchants", json={"name": "B"}, headers=ADMIN_HEADERS
    )
    key_id = (
        await client.post(
            f"/api/v1/admin/merchants/{primera.json()['id']}/keys", json={},
            headers=ADMIN_HEADERS,
        )
    ).json()["id"]

    cruzada = await client.delete(
        f"/api/v1/admin/merchants/{segunda.json()['id']}/keys/{key_id}", headers=ADMIN_HEADERS
    )
    assert cruzada.status_code == 404


async def test_emitir_clave_para_un_comercio_inexistente(client):
    respuesta = await client.post(
        f"/api/v1/admin/merchants/{uuid.uuid4()}/keys", json={}, headers=ADMIN_HEADERS
    )
    assert respuesta.status_code == 404


async def test_listado_de_comercios(client):
    await client.post("/api/v1/admin/merchants", json={"name": "A"}, headers=ADMIN_HEADERS)
    listado = await client.get("/api/v1/admin/merchants", headers=ADMIN_HEADERS)
    nombres = {m["name"] for m in listado.json()}
    # Los dos sembrados por las fixtures más el recién creado.
    assert {"Comercio Uno", "Comercio Dos", "A"} <= nombres
