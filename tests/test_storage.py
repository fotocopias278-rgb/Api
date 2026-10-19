"""Almacenamiento de comprobantes en disco."""

from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

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
        await storage.load((tmp_path / "no-existe.png").as_uri())


async def test_una_clave_con_escape_de_directorio_se_rechaza(tmp_path):
    """Última barrera: aunque la clave viniera sin sanear, no sale de la raíz."""
    from app.core.exceptions import ValidationError

    storage = LocalFileStorage(ajustes(str(tmp_path / "comprobantes")))
    with pytest.raises(ValidationError, match="escapa del directorio"):
        await storage.save("../../escape.png", b"datos")


async def test_no_se_puede_leer_fuera_de_la_raiz(tmp_path):
    from app.core.exceptions import ValidationError

    secreto = tmp_path / "secreto.txt"
    secreto.write_text("contenido privado")
    storage = LocalFileStorage(ajustes(str(tmp_path / "comprobantes")))

    with pytest.raises(ValidationError, match="escapa del directorio"):
        await storage.load(secreto.as_uri())


async def test_el_fichero_escrito_queda_dentro_de_la_raiz(tmp_path):
    raiz = tmp_path / "comprobantes"
    storage = LocalFileStorage(ajustes(str(raiz)))
    uri = await storage.save("comercio/recibo.png", b"datos")
    path = Path(url2pathname(urlsplit(uri).path))
    assert path.is_relative_to(raiz.resolve())
