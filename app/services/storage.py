"""Almacenamiento de los ficheros de comprobante.

La implementación por defecto escribe en disco local. Para producción basta con
sustituirla por una que hable con S3/GCS respetando el mismo protocolo: el resto
del código solo conoce `storage_uri`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import NotFoundError


class ReceiptStorage(Protocol):
    async def save(self, key: str, content: bytes) -> str: ...
    async def load(self, uri: str) -> bytes: ...


class LocalFileStorage:
    """Persistencia en el sistema de ficheros, apta para desarrollo y tests."""

    def __init__(self, config: Settings | None = None) -> None:
        cfg = config or default_settings
        self._root = Path(cfg.receipt_storage_dir)

    async def save(self, key: str, content: bytes) -> str:
        path = self._root / key
        # El I/O de disco es bloqueante: fuera del bucle de eventos.
        await asyncio.to_thread(self._write, path, content)
        return path.as_uri()

    async def load(self, uri: str) -> bytes:
        path = Path(uri.removeprefix("file://"))
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError(f"No se encontró el fichero {uri}.") from exc

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def get_receipt_storage(config: Settings | None = None) -> ReceiptStorage:
    return LocalFileStorage(config)
