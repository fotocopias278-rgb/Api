"""Conciliación entre un comprobante extraído y el pago que dice justificar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.money import format_amount
from app.db.models import Payment
from app.schemas.enums import ReconciliationStatus
from app.schemas.receipt import ReconciliationResult
from app.schemas.vision import ReceiptExtraction


def _parse_date(raw: str | None) -> datetime | None:
    """Interpreta la fecha ISO-8601 devuelta por el modelo, si es válida."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Sin zona horaria asumimos UTC: comparar naive con aware lanza TypeError.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def reconcile(
    extraction: ReceiptExtraction,
    payment: Payment,
    *,
    duplicate_operation: bool = False,
    config: Settings | None = None,
) -> ReconciliationResult:
    """Decide si el comprobante justifica el pago.

    `duplicate_operation` lo aporta quien llama tras consultar si ese número de
    operación ya se usó para otro pago — el reuso de un justificante es uno de
    los fraudes de conciliación más comunes.
    """
    cfg = config or default_settings
    notes: list[str] = []

    if not extraction.is_legible:
        return ReconciliationResult(
            status=ReconciliationStatus.UNREADABLE,
            notes=["El comprobante no es legible; se requiere revisión manual."],
        )

    if duplicate_operation:
        return ReconciliationResult(
            status=ReconciliationStatus.DUPLICATE,
            notes=[
                f"El número de operación {extraction.operation_number!r} ya se "
                "utilizó para justificar otro pago."
            ],
        )

    if extraction.tampering_indicators:
        notes.append(
            "Indicios de manipulación: " + "; ".join(extraction.tampering_indicators)
        )

    # --- Divisa ---
    if extraction.currency is None:
        notes.append("El comprobante no indica divisa.")
    elif extraction.currency.upper() != payment.currency.upper():
        notes.append(
            f"Divisa distinta: comprobante {extraction.currency.upper()}, "
            f"pago {payment.currency.upper()}."
        )

    # --- Importe ---
    extracted_minor = extraction.amount_minor()
    if extracted_minor is None:
        notes.append("No se pudo interpretar el importe del comprobante.")
    else:
        delta = abs(extracted_minor - payment.amount_minor)
        if delta > cfg.reconciliation_amount_tolerance_minor:
            notes.append(
                f"Importe distinto: comprobante "
                f"{format_amount(extracted_minor, extraction.currency or payment.currency)}, "
                f"pago {format_amount(payment.amount_minor, payment.currency)}."
            )

    # --- Fecha ---
    tx_date = _parse_date(extraction.transaction_date)
    if tx_date is None:
        notes.append("El comprobante no indica una fecha interpretable.")
    else:
        window = timedelta(hours=cfg.reconciliation_date_window_hours)
        created = payment.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if abs(tx_date - created) > window:
            notes.append(
                f"Fecha fuera de la ventana de {cfg.reconciliation_date_window_hours} h."
            )

    if notes:
        return ReconciliationResult(status=ReconciliationStatus.MISMATCHED, notes=notes)

    return ReconciliationResult(
        status=ReconciliationStatus.MATCHED,
        notes=["Importe, divisa y fecha coinciden con el pago."],
    )
