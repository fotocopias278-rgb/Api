"""Autenticación por clave de API y utilidades criptográficas."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import Settings

API_KEY_HEADER = "X-API-Key"


def verify_api_key(candidate: str | None, settings: Settings) -> bool:
    """Compara la clave recibida contra las configuradas en tiempo constante.

    Se recorren todas las claves aunque haya coincidencia temprana para no
    filtrar por temporización cuántas claves hay ni cuál acertó.
    """
    if not candidate or not settings.api_keys:
        return False

    matched = False
    for known in settings.api_keys:
        if hmac.compare_digest(candidate, known):
            matched = True
    return matched


def generate_api_key(prefix: str = "finai") -> str:
    """Genera una clave de API apta para producción."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def sha256_hexdigest(data: bytes) -> str:
    """Huella del contenido, usada para deduplicar comprobantes."""
    return hashlib.sha256(data).hexdigest()
