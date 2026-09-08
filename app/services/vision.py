"""Extracción de datos de comprobantes bancarios con IA multimodal.

Se usa la API de Claude con *structured outputs* (`messages.parse`), que
garantiza que la respuesta valida contra `ReceiptExtraction`; así no hace falta
parsear texto libre ni tolerar JSON malformado.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import anthropic

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
    """Selecciona el proveedor según haya o no credenciales."""
    cfg = config or default_settings
    if cfg.anthropic_api_key:
        return AnthropicVisionProvider(cfg)
    return StubVisionProvider()
