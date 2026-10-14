"""Almacenamiento S3, contra un servidor S3 real en proceso.

Se usa `moto` en modo servidor y no un doble de Python: así se ejercitan de
verdad la firma de las peticiones, el endpoint personalizado (que es como se
apunta a R2, B2 o MinIO) y los errores del protocolo.
"""

import contextlib
import socket

import pytest

from app.core.exceptions import NotFoundError
from app.services.storage import S3Storage, get_receipt_storage
from tests.conftest import ajustes_con

BUCKET = "comprobantes-de-prueba"


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor_s3():
    from moto.server import ThreadedMotoServer

    puerto = _puerto_libre()
    servidor = ThreadedMotoServer(port=puerto, verbose=False)
    servidor.start()
    yield f"http://127.0.0.1:{puerto}"
    servidor.stop()


@pytest.fixture
def ajustes_s3(servidor_s3):
    return ajustes_con(
        receipt_storage_backend="s3",
        s3_bucket=BUCKET,
        s3_endpoint_url=servidor_s3,
        s3_access_key_id="test",
        s3_secret_access_key="test",
        s3_region="us-east-1",
    )


@pytest.fixture
async def almacen(ajustes_s3):
    storage = S3Storage(ajustes_s3)
    async with storage._cliente() as s3:
        # El bucket persiste entre tests del módulo: crearlo de nuevo no es error.
        with contextlib.suppress(Exception):
            await s3.create_bucket(Bucket=BUCKET)
    return storage


async def test_se_guarda_y_se_recupera(almacen):
    uri = await almacen.save("comercio-1/recibo.png", b"contenido binario")
    assert uri == f"s3://{BUCKET}/comercio-1/recibo.png"
    assert await almacen.load(uri) == b"contenido binario"


async def test_las_claves_separan_por_comercio(almacen):
    """Cada comercio bajo su prefijo: permite borrar o exportar en bloque."""
    uno = await almacen.save("comercio-1/r.png", b"de uno")
    dos = await almacen.save("comercio-2/r.png", b"de dos")

    assert await almacen.load(uno) == b"de uno"
    assert await almacen.load(dos) == b"de dos"


async def test_un_fichero_inexistente_es_un_404_del_dominio(almacen):
    """No debe escaparse un ClientError crudo del proveedor."""
    with pytest.raises(NotFoundError):
        await almacen.load(f"s3://{BUCKET}/no/existe.png")


async def test_el_contenido_binario_sobrevive_intacto(almacen):
    datos = bytes(range(256)) * 40
    uri = await almacen.save("comercio-1/binario.bin", datos)
    assert await almacen.load(uri) == datos


async def test_la_fabrica_elige_s3_por_configuracion(ajustes_s3):
    assert isinstance(get_receipt_storage(ajustes_s3), S3Storage)


def test_sin_bucket_no_se_construye():
    with pytest.raises(ValueError, match="S3_BUCKET"):
        S3Storage(ajustes_con(receipt_storage_backend="s3", s3_bucket=None))


async def test_el_flujo_completo_funciona_sobre_s3(client, session, merchant, ajustes_s3, almacen):
    """Subir y analizar un comprobante sin tocar el disco local."""
    import uuid

    from app.services.receipts import ReceiptService
    from tests.test_receipts_api import PNG_1PX, FakeVisionProvider, extraccion

    servicio = ReceiptService(
        session, merchant=merchant, storage=almacen, config=ajustes_s3,
        provider=FakeVisionProvider(extraccion()),
    )
    receipt, _ = await servicio.register(
        content=PNG_1PX, filename="recibo.png", content_type="image/png"
    )
    assert receipt.storage_uri.startswith("s3://")
    # La clave lleva el comercio delante.
    assert str(merchant.id) in receipt.storage_uri

    analizado = await servicio.analyze(uuid.UUID(str(receipt.id)))
    assert analizado.status == "extracted"
