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
