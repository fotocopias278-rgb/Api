"""Autenticación por clave de API y utilidades criptográficas."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import Settings

API_KEY_HEADER = "X-API-Key"
ADMIN_KEY_HEADER = "X-Admin-Key"


def verify_admin_key(candidate: str | None, settings: Settings) -> bool:
    """Compara la clave de administración en tiempo constante.

    Se recorren todas aunque haya coincidencia temprana para no filtrar por
    temporización cuántas claves hay ni cuál acertó.
    """
    if not candidate or not settings.admin_api_keys:
        return False

    matched = False
    for known in settings.admin_api_keys:
        if hmac.compare_digest(candidate, known):
            matched = True
    return matched


# Longitud del prefijo que se guarda en claro para identificar la clave.
KEY_PREFIX_LENGTH = 12


def generate_api_key(prefix: str = "finai") -> str:
    """Genera una clave de API apta para producción."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    """Huella de la clave, que es lo único que se persiste.

    SHA-256 a secas y no un hash de contraseña (bcrypt, argon2): una clave de
    API tiene 256 bits de entropía aleatoria, así que no hay diccionario que
    atacar, y en cambio sí hay que verificarla en cada petición —un KDF lento
    aquí sería un ataque de denegación de servicio contra uno mismo.
    """
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str:
    """Trozo inicial mostrable, para distinguir claves sin revelarlas."""
    return key[:KEY_PREFIX_LENGTH]


def sha256_hexdigest(data: bytes) -> str:
    """Huella del contenido, usada para deduplicar comprobantes."""
    return hashlib.sha256(data).hexdigest()
