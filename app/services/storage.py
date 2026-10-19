"""Almacenamiento de los ficheros de comprobante.

Dos implementaciones tras el mismo protocolo: disco local para desarrollo y
S3 —o cualquier servicio compatible— para producción. El resto del código solo
conoce `storage_uri`, así que cambiar de una a otra no toca nada más.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import url2pathname

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import NotFoundError, ValidationError


class ReceiptStorage(Protocol):
    async def save(self, key: str, content: bytes) -> str: ...
    async def load(self, uri: str) -> bytes: ...


class LocalFileStorage:
    """Persistencia en el sistema de ficheros, apta para desarrollo y tests."""

    def __init__(self, config: Settings | None = None) -> None:
        cfg = config or default_settings
        # Se resuelve a ruta absoluta: `as_uri()` falla sobre una relativa, y el
        # valor por defecto (`./var/receipts`) lo es. Sin esto, toda subida
        # revienta con 500 en cualquier despliegue que no fije la variable.
        self._root = Path(cfg.receipt_storage_dir).expanduser().resolve()

    def _ruta_segura(self, destino: Path) -> Path:
        """Garantiza que la ruta cae dentro del directorio raíz.

        Última barrera contra el escape de directorio: aunque la clave venga ya
        saneada, aquí se resuelve la ruta real —siguiendo `..` y enlaces— y se
        comprueba la pertenencia. Es barato y cierra la clase entera de fallo.
        """
        resuelta = destino.resolve()
        if not resuelta.is_relative_to(self._root):
            raise ValidationError(
                "La ruta del fichero escapa del directorio de almacenamiento.",
                details={"root": str(self._root)},
            )
        return resuelta

    async def save(self, key: str, content: bytes) -> str:
        path = self._ruta_segura(self._root / key)
        # El I/O de disco es bloqueante: fuera del bucle de eventos.
        await asyncio.to_thread(self._write, path, content)
        return path.as_uri()

    async def load(self, uri: str) -> bytes:
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValidationError("La referencia del fichero local no es válida.")
        path = self._ruta_segura(Path(url2pathname(parsed.path)))
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError(f"No se encontró el fichero {uri}.") from exc

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class S3Storage:
    """Almacenamiento en cualquier servicio compatible con S3.

    Vale igual para AWS, Cloudflare R2, Backblaze B2 o MinIO: solo cambia
    `S3_ENDPOINT_URL`. Es lo que permite más de una réplica —el disco local no
    se comparte— y que un reinicio no se lleve los comprobantes por delante.
    """

    def __init__(self, config: Settings | None = None) -> None:
        cfg = config or default_settings
        if not cfg.s3_bucket:
            raise ValueError("S3_BUCKET es obligatorio con el almacenamiento S3.")
        self._bucket = cfg.s3_bucket
        self._session = aioboto3.Session(
            aws_access_key_id=cfg.s3_access_key_id,
            aws_secret_access_key=cfg.s3_secret_access_key,
            region_name=cfg.s3_region,
        )
        self._endpoint = cfg.s3_endpoint_url

    def _cliente(self):
        return self._session.client("s3", endpoint_url=self._endpoint)

    async def save(self, key: str, content: bytes) -> str:
        async with self._cliente() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=content)
        return f"s3://{self._bucket}/{key}"

    async def load(self, uri: str) -> bytes:
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        async with self._cliente() as s3:
            try:
                respuesta = await s3.get_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                # Un objeto ausente es un 404 del dominio, no un fallo del
                # proveedor: quien llama ya sabe tratarlo.
                if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                    raise NotFoundError(f"No se encontró el fichero {uri}.") from exc
                raise
            return await respuesta["Body"].read()


def get_receipt_storage(config: Settings | None = None) -> ReceiptStorage:
    """Elige el almacén según la configuración."""
    cfg = config or default_settings
    if cfg.receipt_storage_backend == "s3":
        return S3Storage(cfg)
    return LocalFileStorage(cfg)
