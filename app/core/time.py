"""Helpers para normalizar fechas entre motores de base de datos."""

from __future__ import annotations

from datetime import UTC, datetime


def _con_zona(momento: datetime) -> datetime:
    """SQLite devuelve datetimes sin zona; restarlos de uno aware lanza TypeError."""
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)
