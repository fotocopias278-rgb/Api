"""Esquemas transversales de la API."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str | None = None


class Page(BaseModel, Generic[T]):
    """Paginación por desplazamiento."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)
