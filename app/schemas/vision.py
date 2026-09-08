"""Esquema estructurado que el modelo multimodal debe devolver.

Todos los campos son obligatorios pero anulables: los *structured outputs*
exigen un esquema cerrado, y forzar la presencia de cada clave evita que el
modelo omita silenciosamente un dato que no supo leer — preferimos un `null`
explícito, que sí podemos auditar.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.money import to_minor


class DocumentType(StrEnum):
    TRANSFER_RECEIPT = "transfer_receipt"
    DEPOSIT_SLIP = "deposit_slip"
    APP_SCREENSHOT = "app_screenshot"
    OTHER = "other"
    UNREADABLE = "unreadable"


class ReceiptExtraction(BaseModel):
    """Datos extraídos de un comprobante bancario."""

    document_type: DocumentType = Field(description="Naturaleza del documento.")
    is_legible: bool = Field(description="False si la imagen no permite leer los datos.")

    bank_name: str | None = Field(description="Entidad emisora tal y como aparece.")
    operation_number: str | None = Field(
        description="Número de operación, referencia o localizador."
    )
    amount: str | None = Field(
        description=(
            "Importe normalizado con punto decimal, sin separador de miles ni "
            "símbolo de divisa. Ejemplo: '1234.56'."
        )
    )
    currency: str | None = Field(description="Código ISO-4217 de tres letras, p. ej. 'EUR'.")
    transaction_date: str | None = Field(
        description="Fecha y hora de la operación en formato ISO-8601."
    )

    sender_name: str | None = Field(description="Ordenante de la transferencia.")
    sender_account: str | None = Field(description="Cuenta origen, tal como se muestre.")
    receiver_name: str | None = Field(description="Beneficiario.")
    receiver_account: str | None = Field(description="Cuenta destino, tal como se muestre.")
    concept: str | None = Field(description="Concepto o descripción de la operación.")

    tampering_indicators: list[str] = Field(
        description=(
            "Indicios de manipulación observados: tipografías inconsistentes, "
            "bordes retocados, alineaciones imposibles. Lista vacía si no hay."
        )
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confianza global en la extracción, de 0 a 1."
    )

    def amount_minor(self) -> int | None:
        """Importe en unidades mínimas, o `None` si no es interpretable."""
        if self.amount is None or self.currency is None:
            return None
        try:
            return to_minor(Decimal(self.amount), self.currency)
        except (InvalidOperation, ValueError):
            return None
