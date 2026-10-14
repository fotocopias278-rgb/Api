"""Almacenamiento de comprobantes en disco."""

import pytest

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.services.storage import LocalFileStorage


def ajustes(directorio: str) -> Settings:
    base = get_settings().model_dump(exclude={"admin_api_keys", "fraud_blocked_bins"})
    return Settings(**{**base, "receipt_storage_dir": directorio})


async def test_una_ruta_relativa_funciona(tmp_path, monkeypatch):
    """El valor por defecto es relativo (`./var/receipts`).

    `Path.as_uri()` lanza ValueError sobre una ruta relativa, así que sin
    resolverla toda subida acababa en 500 en cualquier despliegue que no
    fijara RECEIPT_STORAGE_DIR — incluidos render.yaml y docker-compose.
    """
    monkeypatch.chdir(tmp_path)
    storage = LocalFileStorage(ajustes("./var/receipts"))

    uri = await storage.save("abc/recibo.png", b"contenido")

    assert uri.startswith("file:///")
    assert await storage.load(uri) == b"contenido"


async def test_una_ruta_absoluta_sigue_funcionando(tmp_path):
    storage = LocalFileStorage(ajustes(str(tmp_path / "comprobantes")))
    uri = await storage.save("def/recibo.png", b"datos")
    assert await storage.load(uri) == b"datos"


async def test_leer_un_fichero_inexistente(tmp_path):
    storage = LocalFileStorage(ajustes(str(tmp_path)))
    with pytest.raises(NotFoundError):
        await storage.load(f"file://{tmp_path}/no-existe.png")
