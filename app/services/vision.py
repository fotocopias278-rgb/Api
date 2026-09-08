"""Extracción de datos de comprobantes bancarios con IA multimodal.

Hay dos proveedores intercambiables —Claude y Gemini— tras el mismo `Protocol`.
Ambos usan salida estructurada contra `ReceiptExtraction`, de modo que la
respuesta viene validada y no hay que parsear texto libre ni tolerar JSON
malformado. El resto del sistema no sabe cuál de los dos respondió.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import anthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import VisionExtractionError
from app.schemas.vision import DocumentType, ReceiptExtraction

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
SUPPORTED_DOCUMENT_TYPES = frozenset({"application/pdf"})
SUPPORTED_MEDIA_TYPES = SUPPORTED_IMAGE_TYPES | SUPPORTED_DOCUMENT_TYPES

SYSTEM_PROMPT = (
    "Eres un analista de conciliación bancaria. Extraes datos de comprobantes "
    "de pago (transferencias, ingresos, capturas de app bancaria).\n"
    "Reglas:\n"
    "- Transcribe únicamente lo que aparece en el documento. No infieras ni "
    "completes datos ausentes: si un campo no está, devuelve null.\n"
    "- Normaliza el importe con punto decimal y sin separador de miles.\n"
    "- Normaliza la fecha a ISO-8601; si solo hay fecha, omite la hora.\n"
    "- Señala en `tampering_indicators` cualquier indicio de edición digital.\n"
    "- `confidence` refleja tu certeza real sobre el conjunto de la extracción."
)

USER_INSTRUCTION = (
    "Extrae los datos de este comprobante bancario siguiendo el esquema. "
    "Si la imagen es ilegible, marca `is_legible` como false."
)


class VisionProvider(Protocol):
    """Contrato de un extractor de comprobantes."""

    async def extract(self, content: bytes, media_type: str) -> ReceiptExtraction: ...


def _content_block(content: bytes, media_type: str) -> dict:
    """Construye el bloque de contenido según el tipo de fichero."""
    encoded = base64.standard_b64encode(content).decode("utf-8")
    block_type = "document" if media_type in SUPPORTED_DOCUMENT_TYPES else "image"
    return {
        "type": block_type,
        "source": {"type": "base64", "media_type": media_type, "data": encoded},
    }


class AnthropicVisionProvider:
    """Extractor respaldado por la API de Claude."""

    def __init__(
        self,
        config: Settings | None = None,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._settings = config or default_settings
        self._client = client or anthropic.AsyncAnthropic(
            api_key=self._settings.anthropic_api_key,
            timeout=self._settings.vision_timeout_seconds,
        )

    async def extract(self, content: bytes, media_type: str) -> ReceiptExtraction:
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise VisionExtractionError(
                f"Tipo de fichero no soportado: {media_type}",
                details={"supported": sorted(SUPPORTED_MEDIA_TYPES)},
            )

        try:
            # `thinking` se omite a propósito: en Claude Opus 5 el razonamiento
            # adaptativo ya está activo por defecto.
            response = await self._client.messages.parse(
                model=self._settings.vision_model,
                max_tokens=self._settings.vision_max_tokens,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            _content_block(content, media_type),
                            {"type": "text", "text": USER_INSTRUCTION},
                        ],
                    }
                ],
                output_format=ReceiptExtraction,
            )
        except anthropic.RateLimitError as exc:
            raise VisionExtractionError(
                "El proveedor de visión está limitando las peticiones."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise VisionExtractionError(
                f"El proveedor de visión devolvió {exc.status_code}."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise VisionExtractionError("No se pudo contactar con el proveedor de visión.") from exc

        # Un rechazo del clasificador de seguridad llega como HTTP 200: hay que
        # comprobar `stop_reason` antes de leer el contenido.
        if response.stop_reason == "refusal":
            raise VisionExtractionError(
                "El modelo rechazó procesar el documento.",
                details={"stop_reason": "refusal"},
            )

        if response.parsed_output is None:
            raise VisionExtractionError("El modelo no devolvió una extracción válida.")

        logger.info(
            "receipt.extracted",
            extra={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "confidence": response.parsed_output.confidence,
            },
        )
        return response.parsed_output


# Finales que no son una extracción válida: el modelo cortó por política,
# no por haber terminado. Equivalen al `refusal` de Anthropic.
_FINALES_BLOQUEADOS = frozenset(
    {
        genai_types.FinishReason.SAFETY,
        genai_types.FinishReason.PROHIBITED_CONTENT,
        genai_types.FinishReason.BLOCKLIST,
        genai_types.FinishReason.SPII,
        genai_types.FinishReason.RECITATION,
        genai_types.FinishReason.IMAGE_SAFETY,
    }
)


class GeminiVisionProvider:
    """Extractor respaldado por la API de Google Gemini.

    Comparte el prompt y el esquema con el proveedor de Anthropic: el SDK
    convierte `ReceiptExtraction` a su propio formato conservando los campos
    obligatorios, los nulables y las descripciones, así que el contrato de
    salida es el mismo y el resto del sistema no nota de dónde viene el dato.
    """

    def __init__(
        self, config: Settings | None = None, client: genai.Client | None = None
    ) -> None:
        self._settings = config or default_settings
        self._client = client or genai.Client(
            api_key=self._settings.google_api_key,
            http_options=genai_types.HttpOptions(
                # Un 503 "high demand" o un 429 son transitorios y frecuentes en
                # el plan gratuito: reintentar con espera creciente evita dejar
                # el comprobante sin analizar por un pico de carga ajeno.
                retry_options=genai_types.HttpRetryOptions(
                    attempts=4,
                    initial_delay=1.0,
                    max_delay=15.0,
                    http_status_codes=[429, 500, 502, 503, 504],
                ),
                timeout=int(self._settings.vision_timeout_seconds * 1000),
            ),
        )

    async def extract(self, content: bytes, media_type: str) -> ReceiptExtraction:
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise VisionExtractionError(
                f"Tipo de fichero no soportado: {media_type}",
                details={"supported": sorted(SUPPORTED_MEDIA_TYPES)},
            )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._settings.gemini_model,
                contents=[
                    genai_types.Part.from_bytes(data=content, mime_type=media_type),
                    USER_INSTRUCTION,
                ],
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ReceiptExtraction,
                    # Aquí no hay herramientas que llamar; desactivarlo evita
                    # además un aviso del SDK en cada petición.
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
        except genai_errors.ClientError as exc:
            # 4xx: clave inválida, cuota agotada, fichero rechazado.
            raise VisionExtractionError(
                f"Gemini rechazó la petición ({exc.code})."
            ) from exc
        except genai_errors.ServerError as exc:
            raise VisionExtractionError("Gemini no está disponible ahora mismo.") from exc
        except genai_errors.APIError as exc:
            raise VisionExtractionError(f"Error del proveedor de visión: {exc}") from exc

        self._verificar_bloqueo(response)

        if not isinstance(response.parsed, ReceiptExtraction):
            raise VisionExtractionError("El modelo no devolvió una extracción válida.")

        uso = response.usage_metadata
        logger.info(
            "receipt.extracted",
            extra={
                "provider": "gemini",
                "input_tokens": getattr(uso, "prompt_token_count", None),
                "output_tokens": getattr(uso, "candidates_token_count", None),
                "confidence": response.parsed.confidence,
            },
        )
        return response.parsed

    @staticmethod
    def _verificar_bloqueo(response: genai_types.GenerateContentResponse) -> None:
        """Un corte por política llega como respuesta correcta, no como error."""
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason is not None:
            raise VisionExtractionError(
                "El modelo rechazó procesar el documento.",
                details={"block_reason": str(feedback.block_reason)},
            )

        for candidate in response.candidates or []:
            if candidate.finish_reason in _FINALES_BLOQUEADOS:
                raise VisionExtractionError(
                    "El modelo rechazó procesar el documento.",
                    details={"finish_reason": str(candidate.finish_reason)},
                )


class StubVisionProvider:
    """Extractor de reserva cuando no hay credenciales configuradas.

    Devuelve deliberadamente un resultado ilegible: es preferible que la
    conciliación quede pendiente de revisión manual a que un entorno sin modelo
    dé por bueno un comprobante que nadie ha leído.
    """

    async def extract(self, content: bytes, media_type: str) -> ReceiptExtraction:
        logger.warning("vision.stub_provider_used", extra={"media_type": media_type})
        return ReceiptExtraction(
            document_type=DocumentType.UNREADABLE,
            is_legible=False,
            bank_name=None,
            operation_number=None,
            amount=None,
            currency=None,
            transaction_date=None,
            sender_name=None,
            sender_account=None,
            receiver_name=None,
            receiver_account=None,
            concept=None,
            tampering_indicators=[],
            confidence=0.0,
        )


def get_vision_provider(config: Settings | None = None) -> VisionProvider:
    """Selecciona el proveedor de visión.

    Con `VISION_PROVIDER=auto` (por defecto) se usa el primero que tenga
    credenciales. Nombrar uno explícitamente sin su clave no arranca con otro a
    escondidas: cae al stub y lo registra, porque cambiar de modelo por sorpresa
    en un sistema que decide sobre dinero sería peor que no extraer nada.
    """
    cfg = config or default_settings
    elegido = cfg.vision_provider

    if elegido == "anthropic":
        if cfg.anthropic_api_key:
            return AnthropicVisionProvider(cfg)
        logger.error("vision.proveedor_sin_credencial", extra={"provider": "anthropic"})
        return StubVisionProvider()

    if elegido == "gemini":
        if cfg.google_api_key:
            return GeminiVisionProvider(cfg)
        logger.error("vision.proveedor_sin_credencial", extra={"provider": "gemini"})
        return StubVisionProvider()

    if cfg.anthropic_api_key:
        return AnthropicVisionProvider(cfg)
    if cfg.google_api_key:
        return GeminiVisionProvider(cfg)
    return StubVisionProvider()
