"""Validación del contenido real de los ficheros (SEC-01) y rutas (SEC-02)."""

import pytest

from app.core.exceptions import ValidationError
from app.core.files import safe_filename, verify_file_signature

PNG = bytes.fromhex("89504e470d0a1a0a") + b"datos"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
GIF = b"GIF89a\x01\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3"


@pytest.mark.parametrize(
    ("contenido", "tipo"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"),
     (WEBP, "image/webp"), (PDF, "application/pdf")],
)
def test_los_ficheros_legitimos_se_aceptan(contenido, tipo):
    verify_file_signature(contenido, tipo)  # no debe lanzar


@pytest.mark.parametrize(
    ("contenido", "tipo", "ataque"),
    [
        (b"MZ\x90\x00\x03\x00", "image/png", "ejecutable de Windows"),
        (b"\x7fELF\x02\x01\x01", "image/png", "binario ELF de Linux"),
        (b"#!/bin/sh\nrm -rf /", "image/png", "script de shell"),
        (b"<?php system($_GET['c']); ?>", "image/jpeg", "webshell PHP"),
        (b"PK\x03\x04", "application/pdf", "ZIP disfrazado de PDF"),
        (b"<svg onload=alert(1)>", "image/png", "SVG con script"),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", "image/webp", "audio WAV como WEBP"),
        (PNG, "image/jpeg", "PNG real declarado como JPEG"),
        (b"", "image/png", "fichero vacío"),
    ],
)
def test_el_contenido_falsificado_se_rechaza(contenido, tipo, ataque):
    """El Content-Type lo elige quien sube: hay que mirar los bytes."""
    with pytest.raises(ValidationError, match="no corresponde con el tipo declarado"):
        verify_file_signature(contenido, tipo)


def test_un_tipo_no_soportado_se_rechaza():
    with pytest.raises(ValidationError, match="no soportado"):
        verify_file_signature(b"texto", "text/plain")


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("../../etc/passwd", "passwd"),
        ("../../../root/.ssh/id_rsa", "id_rsa"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("/etc/shadow", "shadow"),
        ("recibo.png", "recibo.png"),
        ("..", "comprobante"),
        (".", "comprobante"),
        ("", "comprobante"),
        ("con\x00nulo.png", "con_nulo.png"),
        ("con\nsalto.png", "con_salto.png"),
    ],
)
def test_el_nombre_de_fichero_se_sanea(entrada, esperado):
    assert safe_filename(entrada) == esperado


def test_un_nombre_larguisimo_se_recorta():
    assert len(safe_filename("a" * 500 + ".png")) == 255
