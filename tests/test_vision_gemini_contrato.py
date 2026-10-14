"""Contrato con la API de Gemini, verificado contra un doble local.

Mismo enfoque que `test_vision_contrato.py` para Anthropic: se levanta un
servidor que imita `generateContent`, se apunta el cliente ahí y se inspecciona
lo que sale por el cable. Sin clave y sin gastar una llamada.
"""

import asyncio
import base64
import json
import socket

import pytest
import uvicorn
from google import genai
from google.genai import types as genai_types
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import Settings, get_settings
from app.core.exceptions import VisionExtractionError
from app.services.vision import GeminiVisionProvider
from tests.test_vision_contrato import EXTRACCION_VALIDA


def tipo_de(inline: dict) -> str | None:
    """Lee el tipo del bloque sin casarse con la ortografía del SDK.

    google-genai serializa la clave externa en camelCase y la interna en
    snake_case, y eso ha cambiado entre versiones menores (2.22 enviaba
    `mimeType`; 2.30, `mime_type`). Lo que el contrato debe fijar es que el
    tipo viaja, no cómo lo deletrea el SDK esta semana.
    """
    return inline.get("mime_type") or inline.get("mimeType")


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def gemini_falso():
    """Servidor que imita `generateContent` y guarda lo que recibe."""
    capturado: dict = {}
    control: dict = {"finish_reason": "STOP", "payload": EXTRACCION_VALIDA, "block_reason": None}

    async def generate(request):
        capturado["ruta"] = request.url.path
        capturado["body"] = json.loads(await request.body())

        cuerpo: dict = {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps(control["payload"])}], "role": "model"},
                    "finishReason": control["finish_reason"],
                }
            ],
            "usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 200},
            "modelVersion": "gemini-2.5-flash",
        }
        if control["block_reason"]:
            cuerpo["promptFeedback"] = {"blockReason": control["block_reason"]}
        return JSONResponse(cuerpo)

    puerto = _puerto_libre()
    app = Starlette(routes=[Route("/{ruta:path}", generate, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="error"))
    tarea = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    yield f"http://127.0.0.1:{puerto}", capturado, control

    server.should_exit = True
    await tarea


def proveedor(base_url: str) -> GeminiVisionProvider:
    base = get_settings().model_dump(exclude={"admin_api_keys", "fraud_blocked_bins"})
    config = Settings(**{**base, "google_api_key": "clave-de-prueba"})
    cliente = genai.Client(
        api_key="clave-de-prueba",
        http_options=genai_types.HttpOptions(base_url=base_url),
    )
    return GeminiVisionProvider(config, client=cliente)


async def test_la_imagen_viaja_como_inline_data(gemini_falso):
    base_url, capturado, _ = gemini_falso
    await proveedor(base_url).extract(b"\x89PNG\r\n\x1a\n datos", "image/png")

    partes = capturado["body"]["contents"][0]["parts"]
    inline = next(p for p in partes if "inlineData" in p)["inlineData"]
    assert tipo_de(inline) == "image/png"
    assert base64.b64decode(inline["data"]) == b"\x89PNG\r\n\x1a\n datos"


async def test_un_pdf_tambien_se_admite(gemini_falso):
    base_url, capturado, _ = gemini_falso
    await proveedor(base_url).extract(b"%PDF-1.7", "application/pdf")

    partes = capturado["body"]["contents"][0]["parts"]
    inline = next(p for p in partes if "inlineData" in p)["inlineData"]
    assert tipo_de(inline) == "application/pdf"


async def test_se_pide_json_con_el_esquema_de_extraccion(gemini_falso):
    """El esquema de Pydantic se traduce conservando obligatorios y nulables."""
    base_url, capturado, _ = gemini_falso
    await proveedor(base_url).extract(b"datos", "image/png")

    config = capturado["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"

    esquema = config["responseSchema"]
    assert len(esquema["required"]) == 14
    # Los campos que el modelo puede no encontrar deben admitir null explícito.
    assert esquema["properties"]["bank_name"]["nullable"] is True
    assert esquema["properties"]["document_type"]["enum"]


async def test_el_prompt_de_sistema_viaja(gemini_falso):
    base_url, capturado, _ = gemini_falso
    await proveedor(base_url).extract(b"datos", "image/png")

    sistema = json.dumps(capturado["body"]["systemInstruction"])
    assert "punto decimal" in sistema


async def test_se_usa_el_modelo_configurado(gemini_falso):
    base_url, capturado, _ = gemini_falso
    await proveedor(base_url).extract(b"datos", "image/png")
    assert get_settings().gemini_model in capturado["ruta"]


async def test_la_respuesta_se_convierte_en_extraccion_validada(gemini_falso):
    base_url, _, _ = gemini_falso
    extraccion = await proveedor(base_url).extract(b"datos", "image/png")

    assert extraccion.bank_name == "Banco Ficticio de Pruebas"
    assert extraccion.amount_minor() == 128_450
    assert extraccion.confidence == pytest.approx(0.96)


async def test_un_corte_por_seguridad_no_pasa_por_extraccion_valida(gemini_falso):
    """Gemini corta con `finishReason`, no con un error HTTP."""
    base_url, _, control = gemini_falso
    control["finish_reason"] = "SAFETY"

    with pytest.raises(VisionExtractionError, match="rechazó"):
        await proveedor(base_url).extract(b"datos", "image/png")


async def test_una_peticion_bloqueada_de_entrada_tampoco(gemini_falso):
    base_url, _, control = gemini_falso
    control["block_reason"] = "PROHIBITED_CONTENT"

    with pytest.raises(VisionExtractionError, match="rechazó"):
        await proveedor(base_url).extract(b"datos", "image/png")


async def test_un_tipo_no_soportado_se_rechaza_antes_de_llamar(gemini_falso):
    base_url, capturado, _ = gemini_falso
    with pytest.raises(VisionExtractionError, match="no soportado"):
        await proveedor(base_url).extract(b"datos", "text/plain")
    assert capturado == {}
