"""Enumeraciones del dominio, compartidas por modelos y esquemas."""

from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    REFUNDED = "refunded"


class PaymentMethod(StrEnum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"


class RiskDecision(StrEnum):
    APPROVE = "approve"
    REVIEW = "review"
    REJECT = "reject"


class ReceiptStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    FAILED = "failed"


class ReconciliationStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    DUPLICATE = "duplicate"
    UNREADABLE = "unreadable"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DiscrepancyKind(StrEnum):
    """Tipos de desajuste que el informe de conciliación sabe nombrar."""

    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE_RECEIPT = "duplicate_receipt"
    MISSING_RECEIPT = "missing_receipt"
    ORPHAN_RECEIPT = "orphan_receipt"
    UNREADABLE_RECEIPT = "unreadable_receipt"
    GATEWAY_WITHOUT_PAYMENT = "gateway_without_payment"
