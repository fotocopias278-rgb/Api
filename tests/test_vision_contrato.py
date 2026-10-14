"""Contrato con la API de Messages, verificado contra un doble local.

No hace falta clave ni red: se levanta un servidor que imita el endpoint de
Anthropic, se apunta el cliente ahí con `base_url` y se inspecciona la petición
que sale por el cable. Comprueba lo que más fácil se rompe —la forma del bloque
de contenido y del esquema— sin depender de una llamada de pago.
"""

import asyncio
import json
import socket

import anthropic
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import Settings, get_settings
from app.core.exceptions import VisionExtractionError
from app.services.vision import AnthropicVisionProvider

EXTRACCION_VALIDA = {
    "document_type": "transfer_receipt",
    "is_legible": True,
    "bank_name": "Banco Ficticio de Pruebas",
    "operation_number": "TRF-2026-0417-88231",
    "amount": "1284.50",
    "currency": "EUR",
    "transaction_date": "2026-04-17T11:42:05",
    "sender_name": "Marta Ruiz Delgado",
    "sender_account": "ES91 **** **** **** 4471",
    "receiver_name": "FinAI Gateway S.L.",
    "receiver_account": "ES68 **** **** **** 0093",
    "concept": "Pedido 4471 - suscripción anual",
    "tampering_indicators": [],
    "confidence": 0.96,
}


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def api_falsa():
    """Servidor que imita `POST /v1/messages` y guarda lo que recibe."""
    capturado: dict = {}
    respuesta: dict = {"stop_reason": "end_turn", "payload": EXTRACCION_VALIDA}

    async def messages(request):
        capturado["headers"] = dict(request.headers)
        capturado["body"] = json.loads(await request.body())
        return JSONResponse(
            {
                "id": "msg_prueba",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": json.dumps(respuesta["payload"])}],
                "stop_reason": respuesta["stop_reason"],
                "stop_sequence": None,
                "usage": {"input_tokens": 1200, "output_tokens": 180},
            }
        )

    puerto = _puerto_libre()
    app = Starlette(routes=[Route("/v1/messages", messages, methods=["POST"])])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="error")
    )
    tarea = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    yield f"http://127.0.0.1:{puerto}", capturado, respuesta

    server.should_exit = True
    await tarea


def proveedor(base_url: str) -> AnthropicVisionProvider:
    config = Settings(**{**get_settings().model_dump(exclude={"admin_api_keys", "fraud_blocked_bins"}),
                         "anthropic_api_key": "sk-ant-de-prueba"})
    cliente = anthropic.AsyncAnthropic(api_key="sk-ant-de-prueba", base_url=base_url)
    return AnthropicVisionProvider(config, client=cliente)


async def test_la_peticion_lleva_la_imagen_en_base64(api_falsa):
    base_url, capturado, _ = api_falsa
    await proveedor(base_url).extract(b"\x89PNG\r\n\x1a\n datos", "image/png")

    bloques = capturado["body"]["messages"][0]["content"]
    imagen = next(b for b in bloques if b["type"] == "image")
    assert imagen["source"]["type"] == "base64"
    assert imagen["source"]["media_type"] == "image/png"
    assert imagen["source"]["data"]
    # La instrucción va después de la imagen.
    assert bloques[-1]["type"] == "text"


async def test_un_pdf_viaja_como_documento_no_como_imagen(api_falsa):
    base_url, capturado, _ = api_falsa
    await proveedor(base_url).extract(b"%PDF-1.7", "application/pdf")

    bloques = capturado["body"]["messages"][0]["content"]
    assert bloques[0]["type"] == "document"
    assert bloques[0]["source"]["media_type"] == "application/pdf"


async def test_se_pide_salida_estructurada_con_el_esquema_cerrado(api_falsa):
    """Sin esto la respuesta sería texto libre y habría que parsearlo a mano."""
    base_url, capturado, _ = api_falsa
    await proveedor(base_url).extract(b"datos", "image/png")

    formato = capturado["body"]["output_config"]["format"]
    assert formato["type"] == "json_schema"
    esquema = formato["schema"]
    assert esquema["additionalProperties"] is False
    # Los 14 campos son obligatorios: preferimos un null explícito y auditable
    # a que el modelo omita en silencio un dato que no supo leer.
    assert len(esquema["required"]) == 14
    assert "tampering_indicators" in esquema["required"]


async def test_se_envia_el_modelo_configurado(api_falsa):
    base_url, capturado, _ = api_falsa
    await proveedor(base_url).extract(b"datos", "image/png")
    assert capturado["body"]["model"] == get_settings().vision_model
    # El prompt de sistema viaja e incluye las reglas de normalización, que son
    # lo que convierte "1.284,50 €" en "1284.50".
    assert "punto decimal" in capturado["body"]["system"]


async def test_la_respuesta_se_convierte_en_extraccion_validada(api_falsa):
    base_url, _, _ = api_falsa
    extraccion = await proveedor(base_url).extract(b"datos", "image/png")

    assert extraccion.bank_name == "Banco Ficticio de Pruebas"
    assert extraccion.operation_number == "TRF-2026-0417-88231"
    # El importe normalizado se convierte a unidades mínimas sin perder céntimos.
    assert extraccion.amount_minor() == 128_450
    assert extraccion.confidence == pytest.approx(0.96)


async def test_un_rechazo_del_modelo_no_pasa_por_extraccion_valida(api_falsa):
    """`refusal` llega como HTTP 200: hay que mirar `stop_reason`."""
    base_url, _, respuesta = api_falsa
    respuesta["stop_reason"] = "refusal"

    with pytest.raises(VisionExtractionError, match="rechazó"):
        await proveedor(base_url).extract(b"datos", "image/png")


async def test_un_tipo_no_soportado_se_rechaza_antes_de_llamar(api_falsa):
    base_url, capturado, _ = api_falsa
    with pytest.raises(VisionExtractionError, match="no soportado"):
        await proveedor(base_url).extract(b"datos", "text/plain")
    assert capturado == {}  # no se gastó ni una llamada
