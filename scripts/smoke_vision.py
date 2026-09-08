"""Prueba de humo de la extracción contra la API real de Claude.

Uso:
    GOOGLE_API_KEY=...    python scripts/smoke_vision.py [ruta_al_comprobante]
    ANTHROPIC_API_KEY=... python scripts/smoke_vision.py [ruta_al_comprobante]

Hace una única llamada real al modelo con un comprobante de ejemplo, enseña lo
que ha extraído y lo concilia contra un pago de prueba. Es lo único del sistema
que no se puede verificar sin credenciales.
"""

from __future__ import annotations

import asyncio
import mimetypes
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.exceptions import VisionExtractionError
from app.core.money import format_amount
from app.db.models import Payment
from app.schemas.enums import PaymentMethod
from app.services.reconciliation import reconcile
from app.services.vision import StubVisionProvider, get_vision_provider

POR_DEFECTO = Path(__file__).resolve().parent.parent / "tests/fixtures/comprobante_ejemplo.png"

# Datos del comprobante de ejemplo, para poder conciliar contra algo real.
IMPORTE_ESPERADO = 128_450
FECHA_ESPERADA = datetime(2026, 4, 17, 11, 42, 5, tzinfo=UTC)


def barra(titulo: str) -> None:
    print(f"\n\033[1m{titulo}\033[0m\n" + "─" * 64)


async def main() -> int:
    settings = get_settings()
    proveedor = get_vision_provider(settings)
    if isinstance(proveedor, StubVisionProvider):
        print(
            "✗ No hay proveedor de visión configurado.\n"
            "  Exporta GOOGLE_API_KEY (Gemini) o ANTHROPIC_API_KEY (Claude)\n"
            "  y vuelve a ejecutar."
        )
        return 1

    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else POR_DEFECTO
    if not ruta.exists():
        print(f"✗ No existe el fichero {ruta}")
        return 1

    contenido = ruta.read_bytes()
    tipo = mimetypes.guess_type(ruta.name)[0] or "image/png"
    print(f"Comprobante: {ruta.name} ({len(contenido):,} bytes, {tipo})")
    nombre = type(proveedor).__name__.replace("VisionProvider", "")
    modelo = settings.gemini_model if nombre == "Gemini" else settings.vision_model
    print(f"Proveedor:   {nombre} ({modelo})")

    barra("Llamando al modelo")
    inicio = asyncio.get_event_loop().time()
    try:
        extraccion = await proveedor.extract(contenido, tipo)
    except VisionExtractionError as exc:
        # Un fallo del proveedor es un resultado esperable de esta prueba
        # (clave inválida, cuota agotada): se informa, no se vuelca una traza.
        print(f"✗ {exc.message}")
        if exc.details:
            print(f"  {exc.details}")
        return 1
    segundos = asyncio.get_event_loop().time() - inicio
    print(f"Respuesta en {segundos:.1f} s")

    barra("Datos extraídos")
    for campo, valor in extraccion.model_dump().items():
        print(f"  {campo:22} {valor}")

    minimas = extraccion.amount_minor()
    barra("Comprobaciones")
    esperado = {
        "importe normalizado a céntimos": (minimas, IMPORTE_ESPERADO),
        "divisa": (extraccion.currency, "EUR"),
        "nº de operación": (extraccion.operation_number, "TRF-2026-0417-88231"),
        "documento legible": (extraccion.is_legible, True),
        "sin indicios de manipulación": (bool(extraccion.tampering_indicators), False),
    }
    fallos = 0
    for etiqueta, (obtenido, quiere) in esperado.items():
        ok = obtenido == quiere
        fallos += not ok
        print(f"  {'✓' if ok else '✗'} {etiqueta:32} {obtenido!r}"
              + ("" if ok else f"  (esperado {quiere!r})"))

    barra("Conciliación contra un pago que cuadra")
    pago = Payment(
        reference="pay_smoke",
        customer_id="cli_smoke",
        amount_minor=IMPORTE_ESPERADO,
        currency="EUR",
        method=PaymentMethod.BANK_TRANSFER,
        created_at=FECHA_ESPERADA,
    )
    resultado = reconcile(extraccion, pago)
    print(f"  Pago:      {format_amount(pago.amount_minor, pago.currency)} {pago.currency}")
    print(f"  Veredicto: {resultado.status}")
    for nota in resultado.notes:
        print(f"    · {nota}")

    if fallos:
        print(f"\n✗ {fallos} comprobación(es) fallida(s).")
        return 1
    print("\n✓ Extracción y conciliación correctas contra la API real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
