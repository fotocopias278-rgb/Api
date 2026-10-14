"""Endpoints de comprobantes bancarios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, Path, UploadFile, status

from app.api.deps import ReceiptServiceDep, SettingsDep
from app.core.exceptions import ValidationError
from app.db.models import Payment, Receipt
from app.schemas.receipt import ReceiptAccepted, ReceiptRead
from app.workers.tasks import analyze_receipt_inline, enqueue_receipt_analysis

router = APIRouter(prefix="/receipts", tags=["comprobantes"])


def _to_read(receipt: Receipt, payment: Payment | None = None) -> ReceiptRead:
    model = ReceiptRead.model_validate(receipt)
    if payment is not None:
        model.payment_reference = payment.reference
    return model


@router.post(
    "",
    response_model=ReceiptAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Subir un comprobante para su análisis",
)
async def upload_receipt(
    service: ReceiptServiceDep,
    settings: SettingsDep,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="Imagen o PDF del comprobante.")],
    payment_reference: Annotated[str | None, Form(max_length=64)] = None,
) -> ReceiptAccepted:
    """Registra el comprobante y encola su extracción con IA.

    Responde 202: la lectura del documento ocurre en segundo plano; el resultado
    se consulta en `GET /receipts/{id}`.
    """
    content = await file.read()
    if not file.content_type:
        raise ValidationError("No se pudo determinar el tipo del fichero.")

    receipt, duplicate = await service.register(
        content=content,
        filename=file.filename or "comprobante",
        content_type=file.content_type,
        payment_reference=payment_reference,
    )

    if settings.receipt_processing_mode == "inline":
        # Se ejecuta después de enviar la respuesta: el cliente sigue recibiendo
        # su 202 sin esperar a la llamada al modelo.
        background.add_task(analyze_receipt_inline, receipt.id)
        task_id = None
    else:
        task_id = enqueue_receipt_analysis(receipt.id)

    return ReceiptAccepted(
        id=receipt.id,
        status=receipt.status,
        task_id=task_id,
        duplicate_of=duplicate.id if duplicate else None,
    )


@router.get("/{receipt_id}", response_model=ReceiptRead, summary="Consultar un comprobante")
async def get_receipt(
    receipt_id: Annotated[uuid.UUID, Path(description="Identificador del comprobante.")],
    service: ReceiptServiceDep,
) -> ReceiptRead:
    """Devuelve el estado del análisis y, si terminó, los datos extraídos."""
    receipt = await service.get(receipt_id)
    return _to_read(receipt, receipt.payment)
