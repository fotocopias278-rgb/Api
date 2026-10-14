"""Validación del contenido real de los ficheros subidos.

El `Content-Type` de una petición multipart lo elige quien sube el fichero:
es una declaración, no un hecho. Confiar en él permite colar cualquier cosa
—un ejecutable, un script, un ZIP— con solo escribir `image/png` en la
cabecera. Aquí se mira el contenido.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

from app.core.exceptions import ValidationError

# Firmas de los formatos admitidos. Cada entrada es una lista de alternativas
# (prefijo, desplazamiento) y basta con que una encaje.
FIRMAS: dict[str, tuple[tuple[bytes, int], ...]] = {
    "image/png": ((b"\x89PNG\r\n\x1a\n", 0),),
    "image/jpeg": ((b"\xff\xd8\xff", 0),),
    "image/gif": ((b"GIF87a", 0), (b"GIF89a", 0)),
    # WEBP es un contenedor RIFF: `RIFF` al inicio y `WEBP` en el byte 8.
    "image/webp": ((b"RIFF", 0),),
    "application/pdf": ((b"%PDF-", 0),),
}

# Caracteres que no deben llegar nunca a un nombre de fichero en disco.
_PELIGROSOS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def verify_file_signature(content: bytes, declared_content_type: str) -> None:
    """Comprueba que los bytes reales correspondan al tipo declarado.

    Lanza `ValidationError` si no encajan. Es una función pura: no toca disco
    ni red, de modo que se puede probar exhaustivamente.
    """
    firmas = FIRMAS.get(declared_content_type)
    if firmas is None:
        raise ValidationError(
            f"Tipo de fichero no soportado: {declared_content_type}",
            details={"supported": sorted(FIRMAS)},
        )

    if not any(content.startswith(prefijo) for prefijo, _ in firmas):
        raise ValidationError(
            "El contenido binario del fichero no corresponde con el tipo declarado.",
            details={"declared_content_type": declared_content_type},
        )

    # WEBP necesita una segunda comprobación: `RIFF` por sí solo también
    # encabeza ficheros WAV o AVI, que no son imágenes.
    if declared_content_type == "image/webp" and content[8:12] != b"WEBP":
        raise ValidationError(
            "El contenido binario del fichero no corresponde con el tipo declarado.",
            details={"declared_content_type": declared_content_type},
        )


def safe_filename(filename: str, *, por_defecto: str = "comprobante") -> str:
    """Devuelve un nombre de fichero seguro para usar como clave de almacén.

    `PurePath(...).name` descarta cualquier componente de ruta, lo que neutraliza
    tanto `../../etc/passwd` como rutas absolutas o separadores de Windows.
    Después se eliminan los caracteres de control y los reservados.
    """
    # Las barras invertidas no son separador en POSIX, así que PurePath no las
    # trataría como tales: se normalizan antes.
    candidato = PurePath(filename.replace("\\", "/")).name
    candidato = unicodedata.normalize("NFKC", candidato)
    candidato = _PELIGROSOS.sub("_", candidato).strip(". ")

    # `..` y `.` sobreviven a lo anterior y no son nombres válidos.
    if not candidato or candidato in {".", ".."}:
        return por_defecto
    return candidato[:255]
